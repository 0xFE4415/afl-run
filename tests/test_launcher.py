from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afl_run.launcher import (
    FuzzerProcess,
    abort_if_any_died,
    launch_fuzzer,
    terminate_fuzzers,
    wait_for_fuzzers,
)


def _process(returncode: int | None = None, pid: int = 1) -> MagicMock:
    process = MagicMock(pid=pid, returncode=returncode)
    process.wait = AsyncMock(return_value=returncode or 0)
    return process


class _PendingProcess:
    pid = 2
    returncode = 1

    def __init__(self) -> None:
        self.calls = 0

    async def wait(self) -> int:
        self.calls += 1
        if self.calls == 1:
            await asyncio.Future()
        return 1

    def terminate(self) -> None:
        return None


def test_launch_fuzzer_sets_log_and_tmpdir(tmp_path: Path) -> None:
    process = _process(pid=42)

    async def run() -> FuzzerProcess:
        with patch(
            "afl_run.launcher.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ) as create:
            result = await launch_fuzzer(
                ("afl-fuzz",),
                "main",
                tmp_path / "logs",
                {"PATH": "/bin"},
                tmp_path / "tmp",
            )
        create.assert_awaited_once()
        assert create.call_args.kwargs["env"]["AFL_TMPDIR"] == str(
            tmp_path / "tmp" / "main"
        )
        return result

    result = asyncio.run(run())
    assert result.pid == 42
    assert result.log_path == tmp_path / "logs" / "main.log"
    result.log_file.close()


def test_launch_fuzzer_closes_log_when_start_fails(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = MagicMock()
        with patch(
            "afl_run.launcher.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("failed")),
        ), patch("pathlib.Path.open", return_value=log_file), pytest.raises(OSError):
            await launch_fuzzer(("afl-fuzz",), "main", tmp_path, {})
        log_file.close.assert_called_once_with()

    asyncio.run(run())


def test_terminate_fuzzers_terminates_live_processes() -> None:
    live_process = _process()
    dead_process = _process(returncode=1)
    live_log = MagicMock()
    dead_log = MagicMock()
    live = FuzzerProcess("live", live_process, Path("live.log"), live_log)
    dead = FuzzerProcess("dead", dead_process, Path("dead.log"), dead_log)

    asyncio.run(terminate_fuzzers((live, dead)))

    live_process.terminate.assert_called_once_with()
    live_process.wait.assert_awaited_once()
    dead_process.terminate.assert_not_called()
    live_log.close.assert_called_once_with()
    dead_log.close.assert_called_once_with()


def test_wait_for_fuzzers_waits_and_closes_logs() -> None:
    process = _process()
    log = MagicMock()
    fuzzer = FuzzerProcess("main", process, Path("main.log"), log)

    asyncio.run(wait_for_fuzzers((fuzzer,)))

    process.wait.assert_awaited_once()
    log.close.assert_called_once_with()


def test_abort_if_any_died_reports_dead_process() -> None:
    log = MagicMock()
    process = _process(returncode=1)
    fuzzer = FuzzerProcess("main", process, Path("main.log"), log)

    with pytest.raises(RuntimeError, match="main"):
        asyncio.run(abort_if_any_died((fuzzer,)))

    process.wait.assert_awaited_once()
    log.close.assert_not_called()


def test_abort_if_any_died_cancels_other_waiters() -> None:
    pending_process = _PendingProcess()
    first = FuzzerProcess("first", _process(returncode=1), Path("first.log"), MagicMock())
    pending = FuzzerProcess("pending", pending_process, Path("pending.log"), MagicMock())

    with pytest.raises(RuntimeError):
        asyncio.run(abort_if_any_died((first, pending)))

    assert pending_process.calls == 1
