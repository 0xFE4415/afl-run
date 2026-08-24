from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

import click

from afl_run.config import Config
from afl_run.environment import build_environment
from afl_run.host import configure_host
from afl_run.launcher import FuzzerGroup
from afl_run.orchestration import (
    MAIN_NAME,
    build_campaign_specs,
    reset_output_directory,
    wait_for_main,
)
from afl_run.paths import ResolvedPaths, resolve_paths

LOGGER = logging.getLogger(__name__)
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@click.command()
@click.option("--fresh", is_flag=True, help="Remove existing campaign output before starting.")
@click.option("--dry-run", is_flag=True, help="Print the campaign commands without starting them.")
@click.option(
    "--timeout",
    type=click.FloatRange(min=0),
    default=None,
    help="Stop the campaign after this many seconds.",
)
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
def main(config_path: str, timeout: float | None, fresh: bool, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = Config.model_validate_json(Path(config_path).read_text())
        resolved = resolve_paths(config)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    try:
        asyncio.run(_run_campaign(config, resolved, timeout, fresh, dry_run))
    except asyncio.CancelledError:
        LOGGER.info("campaign interrupted")
    except TimeoutError:
        LOGGER.info("campaign timed out")
    except (RuntimeError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


async def _run_campaign(
    config: Config,
    resolved: ResolvedPaths,
    timeout: float | None = None,
    fresh: bool = False,
    dry_run: bool = False,
) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    for signum in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signum, task.cancel)

    try:
        campaign = _run_campaign_with_signals(config, resolved, fresh, timeout, dry_run)
        await campaign
    finally:
        for signum in SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signum)


async def _run_campaign_with_signals(
    config: Config,
    resolved: ResolvedPaths,
    fresh: bool,
    timeout: float | None = None,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        await asyncio.to_thread(configure_host, config.host)
        tmp_root = resolved.afl_tmpdir
        if fresh:
            await asyncio.to_thread(reset_output_directory, resolved.out_dir)
        else:
            resolved.out_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_root = None

    environment = build_environment(config)
    append_logs = not fresh
    async with FuzzerGroup(dry_run=dry_run) as group:
        specs = build_campaign_specs(config, resolved)
        main_name, main_command = specs[0]
        launch_started = time.time()
        main = await group.launch(
            main_command,
            main_name,
            resolved.log_dir,
            environment,
            tmp_root,
            append_logs,
        )
        await group.wait_for_main(
            resolved.out_dir / MAIN_NAME / "fuzzer_stats",
            main,
            wait_for_main,
            launch_started,
        )

        async def run_campaign() -> None:
            for name, command in specs[1:]:
                await group.launch(
                    command, name, resolved.log_dir, environment, tmp_root, append_logs
                )

            LOGGER.info("Monitor: afl-whatsup %s", resolved.out_dir)
            LOGGER.info("Stop: press Ctrl-C")
            LOGGER.info("Emergency stop: pkill afl-fuzz")
            await group.abort_if_any_died()

        if timeout is None:
            await run_campaign()
        else:
            await asyncio.wait_for(run_campaign(), timeout=timeout)
