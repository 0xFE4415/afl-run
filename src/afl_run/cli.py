from __future__ import annotations

import asyncio
from pathlib import Path

import click

from afl_run.config import Config
from afl_run.environment import build_environment
from afl_run.host import configure_host
from afl_run.launcher import FuzzerProcess, abort_if_any_died, launch_fuzzer, terminate_fuzzers
from afl_run.orchestration import (
    build_cmplog_command,
    build_master_command,
    build_worker_commands,
    prepare_shared_memory,
    wait_for_master,
)
from afl_run.paths import ResolvedPaths, resolve_paths


@click.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
def main(config_path: str) -> None:
    cfg = Config.model_validate_json(Path(config_path).read_text())
    resolved = resolve_paths(cfg)
    asyncio.run(_run_campaign(cfg, resolved))


async def _run_campaign(cfg: Config, resolved: ResolvedPaths) -> None:
    configure_host(cfg.host)
    paths = resolved
    tmp_root = Path(cfg.engine.afl_tmpdir) if cfg.engine.afl_tmpdir is not None else None
    if cfg.execution.fresh:
        prepare_shared_memory(paths.out_dir)
    else:
        paths.out_dir.mkdir(parents=True, exist_ok=True)

    environment = build_environment(cfg)
    fuzzers: list[FuzzerProcess] = []
    try:
        master = await launch_fuzzer(
            build_master_command(cfg, paths),
            "main",
            paths.log_dir,
            environment,
            tmp_root,
        )
        fuzzers.append(master)
        await asyncio.to_thread(
            wait_for_master,
            paths.out_dir / "main" / "fuzzer_stats",
            master.process,
        )

        cmplog = await launch_fuzzer(
            build_cmplog_command(cfg, paths),
            "cmplog",
            paths.log_dir,
            environment,
            tmp_root,
        )
        fuzzers.append(cmplog)

        worker_names = (f"s{index}" for index in range(1, cfg.execution.n_instances))
        if paths.laf is not None:
            worker_names = (*worker_names, "laf")
        if paths.asan_main is not None:
            worker_names = (
                *worker_names,
                *(f"asan{index}" for index in range(1, cfg.engine.asan_instances + 1)),
            )
        for name, command in zip(worker_names, build_worker_commands(cfg, paths), strict=True):
            fuzzers.append(
                await launch_fuzzer(
                    command,
                    name,
                    paths.log_dir,
                    environment,
                    tmp_root,
                )
            )

        await abort_if_any_died(tuple(fuzzers))
    except BaseException:
        if fuzzers:
            await terminate_fuzzers(tuple(fuzzers))
        raise
