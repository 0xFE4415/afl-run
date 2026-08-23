from __future__ import annotations

import asyncio
import logging
import shlex
import signal
from pathlib import Path

import click

from afl_run.config import Config
from afl_run.environment import build_environment
from afl_run.host import configure_host
from afl_run.launcher import FuzzerGroup
from afl_run.orchestration import (
    MASTER_NAME,
    build_cmplog_command,
    build_master_command,
    build_worker_specs,
    reset_output_directory,
    wait_for_master,
)
from afl_run.paths import ResolvedPaths, resolve_paths

LOGGER = logging.getLogger(__name__)
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
MASTER_STARTUP_TIMEOUT_SECONDS = 30


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
    if dry_run:
        if fresh:
            raise click.ClickException("--dry-run cannot be combined with --fresh")
        _print_dry_run(config, resolved)
        return
    try:
        asyncio.run(_run_campaign(config, resolved, timeout, fresh))
    except asyncio.CancelledError:
        LOGGER.info("campaign interrupted")
    except TimeoutError:
        LOGGER.info("campaign timed out")
    except (RuntimeError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


def _print_dry_run(config: Config, resolved: ResolvedPaths) -> None:
    click.echo("Dry run: no processes will be started.")
    click.echo(f"would start main: {shlex.join(build_master_command(config, resolved))}")
    if resolved.cmplog is not None:
        click.echo(f"would start cmplog: {shlex.join(build_cmplog_command(config, resolved))}")
    for name, command in build_worker_specs(config, resolved):
        click.echo(f"would start {name}: {shlex.join(command)}")


async def _run_campaign(
    config: Config,
    resolved: ResolvedPaths,
    timeout: float | None = None,
    fresh: bool = False,
) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    for signum in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signum, task.cancel)

    try:
        campaign = _run_campaign_with_signals(config, resolved, fresh, timeout)
        await campaign
    finally:
        for signum in SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signum)


async def _run_campaign_with_signals(
    config: Config, resolved: ResolvedPaths, fresh: bool, timeout: float | None = None
) -> None:
    await asyncio.to_thread(configure_host, config.host)
    tmp_root = resolved.afl_tmpdir
    if fresh:
        await asyncio.to_thread(reset_output_directory, resolved.out_dir)
    else:
        resolved.out_dir.mkdir(parents=True, exist_ok=True)

    environment = build_environment(config)
    append_logs = not fresh
    async with FuzzerGroup() as group:
        master = await group.launch(
            build_master_command(config, resolved),
            MASTER_NAME,
            resolved.log_dir,
            environment,
            tmp_root,
            append_logs,
        )
        await asyncio.wait_for(
            asyncio.to_thread(
                wait_for_master,
                resolved.out_dir / MASTER_NAME / "fuzzer_stats",
                master.process,
            ),
            timeout=MASTER_STARTUP_TIMEOUT_SECONDS,
        )

        async def run_campaign() -> None:
            if resolved.cmplog is not None:
                await group.launch(
                    build_cmplog_command(config, resolved),
                    "cmplog",
                    resolved.log_dir,
                    environment,
                    tmp_root,
                    append_logs,
                )
            for name, command in build_worker_specs(config, resolved):
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
