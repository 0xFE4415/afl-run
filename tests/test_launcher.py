from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afl_run.launcher import (
    FuzzerGroup,
    FuzzerProcess,
    _abort_if_any_died,
    launch_fuzzer,
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

    def kill(self) -> None:
        return None


class _StubbornProcess:
    pid = 3
    returncode: int | None = None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


class _ErrorProcess:
    pid = 4
    returncode = 1

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        raise OSError("wait failed")


def test_launch_fuzzer_sets_log_and_tmpdir(tmp_path: Path, caplog) -> None:
    process = _process(pid=42)
    caplog.set_level(logging.INFO, logger="afl_run.launcher")

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
    assert "starting main: afl-fuzz" in caplog.text
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


def test_launch_fuzzer_appends_existing_log_on_resume(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"previous output\n")
    process = _process(pid=42)

    async def run() -> FuzzerProcess:
        with patch(
            "afl_run.launcher.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            return await launch_fuzzer(("afl-fuzz",), "main", tmp_path, {}, append=True)

    result = asyncio.run(run())
    result.log_file.write(b"new output\n")
    result.log_file.close()

    assert log_path.read_bytes() == b"previous output\nnew output\n"


def test_fuzzer_group_tracks_launched_fuzzer() -> None:
    fuzzer = FuzzerProcess("main", _process(), Path("main.log"), MagicMock())

    async def run() -> None:
        with patch("afl_run.launcher.launch_fuzzer", new=AsyncMock(return_value=fuzzer)):
            async with FuzzerGroup() as group:
                result = await group.launch(("afl-fuzz",), "main", Path("logs"), {})
                assert result is fuzzer
                assert group.fuzzers == (fuzzer,)

    asyncio.run(run())


def test_fuzzer_group_terminates_processes_on_exit() -> None:
    live_process = _process()
    dead_process = _process(returncode=1)
    live_log = MagicMock()
    dead_log = MagicMock()
    live = FuzzerProcess("live", live_process, Path("live.log"), live_log)
    dead = FuzzerProcess("dead", dead_process, Path("dead.log"), dead_log)

    async def run() -> None:
        async with FuzzerGroup((live, dead)):
            pass

    asyncio.run(run())

    live_process.terminate.assert_called_once_with()
    live_process.wait.assert_awaited_once()
    dead_process.terminate.assert_not_called()
    live_log.close.assert_called_once_with()
    dead_log.close.assert_called_once_with()


def test_fuzzer_group_kills_processes_that_ignore_terminate() -> None:
    process = _StubbornProcess()
    exited_process = _process()
    exited_process.terminate.side_effect = lambda: setattr(exited_process, "returncode", 0)
    log = MagicMock()
    exited_log = MagicMock()
    fuzzer = FuzzerProcess("stubborn", process, Path("stubborn.log"), log)
    exited = FuzzerProcess("exited", exited_process, Path("exited.log"), exited_log)

    async def run() -> None:
        with patch("afl_run.launcher.SHUTDOWN_TIMEOUT_SECONDS", 0.01):
            await FuzzerGroup((fuzzer, exited)).__aexit__(None, None, None)

    asyncio.run(run())

    assert process.returncode == -9
    log.close.assert_called_once_with()
    exited_log.close.assert_called_once_with()


def test_fuzzer_group_reports_dead_process() -> None:
    log = MagicMock()
    process = _process(returncode=1)
    fuzzer = FuzzerProcess("main", process, Path("main.log"), log)

    async def run() -> None:
        async with FuzzerGroup((fuzzer,)) as group:
            with pytest.raises(RuntimeError, match=r"main.*main\.log"):
                await group.abort_if_any_died()

    asyncio.run(run())
    process.wait.assert_awaited()
    log.close.assert_called_once_with()


def test_abort_if_any_died_reports_all_dead_processes() -> None:
    first = FuzzerProcess("first", _process(returncode=1), Path("first.log"), MagicMock())
    second = FuzzerProcess("second", _process(returncode=2), Path("second.log"), MagicMock())

    async def run() -> None:
        with pytest.raises(RuntimeError) as error:
            await _abort_if_any_died((first, second))
        assert "first" in str(error.value)
        assert "second" in str(error.value)

    asyncio.run(run())


def test_abort_if_any_died_reports_wait_errors() -> None:
    process = _ErrorProcess()
    fuzzer = FuzzerProcess("broken", process, Path("broken.log"), MagicMock())

    async def run() -> None:
        with pytest.raises(RuntimeError, match="wait failed"):
            await _abort_if_any_died((fuzzer,))

    asyncio.run(run())


def test_abort_if_any_died_accepts_empty_group() -> None:
    asyncio.run(_abort_if_any_died(()))


def test_fuzzer_group_cancels_other_waiters() -> None:
    pending_process = _PendingProcess()
    first = FuzzerProcess("first", _process(returncode=1), Path("first.log"), MagicMock())
    pending = FuzzerProcess("pending", pending_process, Path("pending.log"), MagicMock())

    async def run() -> None:
        async with FuzzerGroup((first, pending)) as group:
            with pytest.raises(RuntimeError):
                await group.abort_if_any_died()

    asyncio.run(run())

    assert pending_process.calls == 2
