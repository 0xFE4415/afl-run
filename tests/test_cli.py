from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from afl_run.cli import _run_campaign, main
from afl_run.config import Config, ExecutionConfig, PathConfig
from afl_run.launcher import FuzzerProcess
from afl_run.paths import ResolvedPaths, resolve_paths


def _config(
    tmp_path: Path,
    *,
    n_workers: int = 0,
    optional: bool = False,
    cmplog: bool = True,
) -> tuple[Config, ResolvedPaths]:
    cfg = Config(
        execution=ExecutionConfig(n_workers=n_workers),
        paths=PathConfig(
            main="main",
            cmplog="cmplog" if cmplog else None,
            laf="laf" if optional else None,
            asan_main="asan" if optional else None,
            dictionary="dict",
            seeds_dir="seeds",
            out_dir=str(tmp_path / "out"),
        ),
    )
    paths = ResolvedPaths(
        main=Path("main"),
        cmplog=Path("cmplog") if cmplog else None,
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


def test_run_campaign_launches_master_cmplog_and_workers(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    cfg, paths = _config(tmp_path, n_workers=3, optional=True)
    launched: list[str] = []

    async def launch(
        command: tuple[str, ...],
        name: str,
        *args: object,
        **kwargs: object,
    ) -> FuzzerProcess:
        launched.append(name)
        return _fuzzer(name)

    with (
        patch("afl_run.cli.configure_host") as configure,
        patch("afl_run.cli.reset_output_directory") as prepare,
        patch("afl_run.cli.build_environment", return_value={"PATH": "/bin"}),
        patch("afl_run.cli.FuzzerGroup.launch", side_effect=launch),
        patch("afl_run.cli.asyncio.to_thread", new=AsyncMock()) as to_thread,
        patch("afl_run.cli.FuzzerGroup.abort_if_any_died", new=AsyncMock()),
        patch("afl_run.cli.FuzzerGroup.__aexit__", new=AsyncMock(return_value=False)),
    ):
        asyncio.run(_run_campaign(cfg, paths, fresh=True))

    to_thread.assert_any_await(configure, cfg.host)
    to_thread.assert_any_await(prepare, paths.out_dir)
    assert launched == ["main", "cmplog", "s1", "s2", "s3", "laf", "asan1", "asan2"]
    assert f"Monitor: afl-whatsup {paths.out_dir}" in caplog.text
    assert "Stop: press Ctrl-C" in caplog.text
    assert "Emergency stop: pkill afl-fuzz" in caplog.text


def test_run_campaign_without_optional_workers_uses_existing_output(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path, cmplog=False)
    existing = paths.out_dir / "existing.txt"
    paths.out_dir.mkdir(parents=True)
    existing.write_text("keep")
    launched: list[str] = []

    async def launch(
        command: tuple[str, ...],
        name: str,
        *args: object,
        **kwargs: object,
    ) -> FuzzerProcess:
        launched.append(name)
        return _fuzzer(name)

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.reset_output_directory") as prepare,
        patch("afl_run.cli.build_environment", return_value={}),
        patch("afl_run.cli.FuzzerGroup.launch", side_effect=launch),
        patch("afl_run.cli.asyncio.to_thread", new=AsyncMock()),
        patch("afl_run.cli.FuzzerGroup.abort_if_any_died", new=AsyncMock()),
        patch("afl_run.cli.FuzzerGroup.__aexit__", new=AsyncMock(return_value=False)),
    ):
        asyncio.run(_run_campaign(cfg, paths))

    assert paths.out_dir.is_dir()
    assert existing.read_text() == "keep"
    assert launched == ["main"]
    prepare.assert_not_called()


def test_run_campaign_end_to_end_with_stub_afl_fuzz(tmp_path: Path, monkeypatch) -> None:
    stub = tmp_path / "afl-fuzz"
    stub.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -o) out=\"$2\"; shift 2 ;;\n"
        "    -M|-S) name=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "mkdir -p \"$out/$name\"\n"
        ": > \"$out/$name/fuzzer_stats\"\n"
        "trap 'exit 0' TERM INT\n"
        "while :; do sleep 1; done\n"
    )
    stub.chmod(0o755)
    harness = _tmp_executable(tmp_path / "harness")
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    cfg = Config(
        paths=PathConfig(
            main=str(harness),
            seeds_dir=str(seeds),
            out_dir=str(tmp_path / "out"),
        )
    )
    paths = resolve_paths(cfg)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    async def run() -> None:
        with patch("afl_run.cli.configure_host"):
            with pytest.raises(TimeoutError):
                await _run_campaign(cfg, paths, timeout=0.2, fresh=True)

    asyncio.run(run())
    assert (paths.out_dir / "main" / "fuzzer_stats").is_file()


def _tmp_executable(path: Path) -> Path:
    path.write_text("")
    path.chmod(0o755)
    return path


def test_run_campaign_terminates_started_fuzzers_on_failure(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)
    master = _fuzzer("main")

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.build_environment", return_value={}),
        patch("afl_run.cli.FuzzerGroup.launch", new=AsyncMock(return_value=master)),
        patch(
            "afl_run.cli.asyncio.to_thread",
            new=AsyncMock(side_effect=[None, RuntimeError("master failed")]),
        ),
        patch(
            "afl_run.cli.FuzzerGroup.__aexit__",
            new=AsyncMock(return_value=False),
        ) as exit_group,
    ):
        with pytest.raises(RuntimeError, match="master failed"):
            asyncio.run(_run_campaign(cfg, paths))

    exit_group.assert_awaited_once()


def test_run_campaign_does_not_cleanup_if_host_setup_fails(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)

    with (
        patch("afl_run.cli.configure_host", side_effect=OSError("host failed")),
        patch(
            "afl_run.cli.FuzzerGroup.__aexit__",
            new=AsyncMock(return_value=False),
        ) as exit_group,
    ):
        with pytest.raises(OSError, match="host failed"):
            asyncio.run(_run_campaign(cfg, paths))

    exit_group.assert_not_awaited()


def test_run_campaign_cleans_up_before_process_start(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)

    with (
        patch("afl_run.cli.configure_host"),
        patch("afl_run.cli.build_environment", return_value={}),
            patch(
                "afl_run.orchestration.build_master_command",
                side_effect=OSError("command failed"),
            ),
        patch(
            "afl_run.cli.FuzzerGroup.__aexit__",
            new=AsyncMock(return_value=False),
        ) as exit_group,
    ):
        with pytest.raises(OSError, match="command failed"):
            asyncio.run(_run_campaign(cfg, paths))

    exit_group.assert_awaited_once()


def test_main_handles_campaign_cancellation(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")

    def cancel(campaign: Any) -> None:
        campaign.close()
        raise asyncio.CancelledError

    with (
        patch("afl_run.cli.Config.model_validate_json", return_value=MagicMock()),
        patch("afl_run.cli.resolve_paths", return_value=MagicMock()),
        patch("afl_run.cli.asyncio.run", side_effect=cancel),
    ):
        result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code == 0
    assert "campaign interrupted" in caplog.text


def test_main_handles_campaign_timeout(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")

    def timeout(campaign: Any) -> None:
        campaign.close()
        raise TimeoutError

    with (
        patch("afl_run.cli.Config.model_validate_json", return_value=MagicMock()),
        patch("afl_run.cli.resolve_paths", return_value=MagicMock()),
        patch("afl_run.cli.asyncio.run", side_effect=timeout),
    ):
        result = CliRunner().invoke(main, ["--timeout", "1.5", str(config_path)])

    assert result.exit_code == 0
    assert "campaign timed out" in caplog.text


def test_main_reports_campaign_runtime_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")

    with (
        patch("afl_run.cli.Config.model_validate_json", return_value=MagicMock()),
        patch("afl_run.cli.resolve_paths", return_value=MagicMock()),
        patch(
            "afl_run.cli.asyncio.run",
            side_effect=_close_and_raise(RuntimeError("campaign failed")),
        ),
    ):
        result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code == 1
    assert "Error: campaign failed" in result.output


def test_main_reports_campaign_os_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")

    with (
        patch("afl_run.cli.Config.model_validate_json", return_value=MagicMock()),
        patch("afl_run.cli.resolve_paths", return_value=MagicMock()),
        patch("afl_run.cli.asyncio.run", side_effect=_close_and_raise(OSError("disk full"))),
    ):
        result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code == 1
    assert "Error: disk full" in result.output


def _close_and_raise(error: BaseException):
    def raise_error(campaign: Any) -> None:
        campaign.close()
        raise error

    return raise_error


def test_run_campaign_applies_timeout(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)

    with patch("afl_run.cli._run_campaign_with_signals", new=AsyncMock()) as campaign:
        asyncio.run(_run_campaign(cfg, paths, timeout=1.5))

    campaign.assert_awaited_once_with(cfg, paths, False, 1.5, False)


def test_run_campaign_registers_and_removes_interrupt_signals(tmp_path: Path) -> None:
    cfg, paths = _config(tmp_path)
    loop = MagicMock()

    with (
        patch("afl_run.cli.asyncio.get_running_loop", return_value=loop),
        patch("afl_run.cli._run_campaign_with_signals", new=AsyncMock()),
    ):
        asyncio.run(_run_campaign(cfg, paths))

    expected = [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]
    assert [call.args[0] for call in loop.add_signal_handler.call_args_list] == expected
    assert [call.args[0] for call in loop.remove_signal_handler.call_args_list] == expected
