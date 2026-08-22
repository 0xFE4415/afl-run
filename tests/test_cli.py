from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afl_run.cli import _run_campaign
from afl_run.config import Config, ExecutionConfig, PathConfig
from afl_run.launcher import FuzzerProcess
from afl_run.paths import ResolvedPaths


def _config(
    tmp_path: Path,
    *,
    n_instances: int = 1,
    optional: bool = False,
) -> tuple[Config, ResolvedPaths]:
    cfg = Config(
        execution=ExecutionConfig(n_instances=n_instances, fresh=True),
        paths=PathConfig(
            main="main",
            cmplog="cmplog",
            laf="laf" if optional else None,
            asan_main="asan" if optional else None,
            dictionary="dict",
            seeds_dir="seeds",
            out_dir=str(tmp_path / "out"),
        ),
    )
    paths = ResolvedPaths(
        main=Path("main"),
        cmplog=Path("cmplog"),
        laf=Path("laf") if optional else None,
        asan_main=Path("asan") if optional else None,
        dictionary=Path("dict"),
        seeds_dir=Path("seeds"),
        out_dir=tmp_path / "out",
        log_dir=tmp_path / "logs",
    )
    return cfg, paths


def _fuzzer(name: str) -> FuzzerProcess:
    process = MagicMock(pid=1, returncode=None)
    process.wait = AsyncMock(return_value=0)
    return FuzzerProcess(name, process, Path(f"{name}.log"), MagicMock())


def test_run_campaign_launches_master_cmplog_and_workers(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path, n_instances=3, optional=True)
    launched: list[str] = []

    async def launch(*args: object, **kwargs: object) -> FuzzerProcess:
        launched.append(str(args[1]))
        return _fuzzer(str(args[1]))

    with (
        patch("afl_run.cli.configure_host") as configure,
        patch("afl_run.cli.prepare_shared_memory") as prepare,
        patch("afl_run.cli.build_environment", return_value={"PATH": "/bin"}),
        patch("afl_run.cli.launch_fuzzer", side_effect=launch),
        patch("afl_run.cli.asyncio.to_thread", new=AsyncMock()),
        patch("afl_run.cli.abort_if_any_died", new=AsyncMock()),
    ):
        asyncio.run(_run_campaign(cfg, paths))

    configure.assert_called_once_with(cfg.host)
    prepare.assert_called_once_with(paths.out_dir)
    assert launched == ["main", "cmplog", "s1", "s2", "laf", "asan1", "asan2"]


def test_run_campaign_without_optional_workers_uses_existing_output(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)
    cfg.execution.fresh = False

    async def launch(*args: object, **kwargs: object) -> FuzzerProcess:
        return _fuzzer(str(args[1]))

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.build_environment", return_value={}),
        patch("afl_run.cli.launch_fuzzer", side_effect=launch),
        patch("afl_run.cli.asyncio.to_thread", new=AsyncMock()),
        patch("afl_run.cli.abort_if_any_died", new=AsyncMock()),
    ):
        asyncio.run(_run_campaign(cfg, paths))

    assert paths.out_dir.is_dir()


def test_run_campaign_terminates_started_fuzzers_on_failure(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)
    master = _fuzzer("main")

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.build_environment", return_value={}),
        patch("afl_run.cli.launch_fuzzer", new=AsyncMock(return_value=master)),
        patch(
            "afl_run.cli.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("master failed")),
        ),
        patch("afl_run.cli.terminate_fuzzers", new=AsyncMock()) as terminate,
    ):
        with pytest.raises(RuntimeError, match="master failed"):
            asyncio.run(_run_campaign(cfg, paths))

    terminate.assert_awaited_once_with((master,))


def test_run_campaign_does_not_cleanup_if_host_setup_fails(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)

    with (
        patch("afl_run.cli.configure_host", side_effect=OSError("host failed")),
        patch("afl_run.cli.terminate_fuzzers", new=AsyncMock()) as terminate,
    ):
        with pytest.raises(OSError, match="host failed"):
            asyncio.run(_run_campaign(cfg, paths))

    terminate.assert_not_awaited()


def test_run_campaign_cleans_up_before_process_start(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.build_environment", return_value={}),
        patch("afl_run.cli.build_master_command", side_effect=OSError("command failed")),
        patch("afl_run.cli.terminate_fuzzers", new=AsyncMock()) as terminate,
    ):
        with pytest.raises(OSError, match="command failed"):
            asyncio.run(_run_campaign(cfg, paths))

    terminate.assert_awaited_once_with(())
