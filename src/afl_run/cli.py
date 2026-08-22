from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import click
from pydantic import ValidationError

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
from afl_run.paths import PathValidationError, ResolvedPaths, resolve_paths

LOGGER = logging.getLogger(__name__)


@click.command()
@click.option("--fresh", is_flag=True, help="Remove existing campaign output before starting.")
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
def main(config_path: str, timeout: float | None, fresh: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        cfg = Config.model_validate_json(Path(config_path).read_text())
    except ValidationError as error:
        raise click.ClickException(str(error)) from error
    try:
        resolved = resolve_paths(cfg)
    except PathValidationError as error:
        raise click.ClickException(str(error)) from error
    try:
        asyncio.run(_run_campaign(cfg, resolved, timeout, fresh))
    except asyncio.CancelledError:
        LOGGER.info("campaign interrupted")
    except TimeoutError:
        LOGGER.info("campaign timed out")
    except (RuntimeError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


async def _run_campaign(
    cfg: Config,
    resolved: ResolvedPaths,
    timeout: float | None = None,
    fresh: bool = False,
) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(signum, task.cancel)

    try:
        campaign = _run_campaign_with_signals(cfg, resolved, fresh)
        if timeout is None:
            await campaign
        else:
            await asyncio.wait_for(campaign, timeout=timeout)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(signum)


async def _run_campaign_with_signals(
    cfg: Config, resolved: ResolvedPaths, fresh: bool
) -> None:
    await asyncio.to_thread(configure_host, cfg.host)
    tmp_root = resolved.afl_tmpdir
    if fresh:
        await asyncio.to_thread(reset_output_directory, resolved.out_dir)
    else:
        resolved.out_dir.mkdir(parents=True, exist_ok=True)

    environment = build_environment(cfg)
    append_logs = not fresh
    async with FuzzerGroup() as group:
        master = await group.launch(
            build_master_command(cfg, resolved),
            MASTER_NAME,
            resolved.log_dir,
            environment,
            tmp_root,
            append_logs,
        )
        await asyncio.to_thread(
            wait_for_master,
            resolved.out_dir / MASTER_NAME / "fuzzer_stats",
            master.process,
        )

        if resolved.cmplog is not None:
            await group.launch(
                build_cmplog_command(cfg, resolved),
                "cmplog",
                resolved.log_dir,
                environment,
                tmp_root,
                append_logs,
            )
        for name, command in build_worker_specs(cfg, resolved):
            await group.launch(command, name, resolved.log_dir, environment, tmp_root, append_logs)

        await group.abort_if_any_died()
