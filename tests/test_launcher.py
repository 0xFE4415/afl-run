from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from helpers import mock_process

from afl_run.launcher import (
    LOG_TAIL_LINES,
    LOG_TAIL_MAX_BYTES,
    STARTUP_WARNING_SECONDS,
    FuzzerGroup,
    FuzzerProcess,
    ProcessLike,
    _format_size,
    _has_liveness_progressed,
    _LivenessSample,
    _log_liveness,
    _monitor_startup,
    _read_wait_channel,
    _sample_liveness,
    _signal_process_group,
    _terminate_fuzzers,
    _wait_for_completion,
    _warn_if_startup_is_slow,
    format_log_tail,
    handle_sleep_signal,
    launch_fuzzer,
    read_log_tail,
)


class _PendingProcess:
    pid = 2
    returncode = 1

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def wait(self) -> int:
        self.calls += 1
        if self.calls == 1:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
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
    process = mock_process(pid=42)
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
        assert create.call_args.kwargs["env"]["AFL_TMPDIR"] == str(tmp_path / "tmp" / "main")
        return result

    result = asyncio.run(run())
    assert result.pid == 42
    assert result.log_path == tmp_path / "logs" / "main.log"
    assert "starting main: afl-fuzz" in caplog.text
    result.log_file.close()


def test_launch_fuzzer_closes_log_when_start_fails(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = MagicMock()
        with (
            patch(
                "afl_run.launcher.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=OSError("failed")),
            ),
            patch("pathlib.Path.open", return_value=log_file),
            pytest.raises(OSError),
        ):
            await launch_fuzzer(("afl-fuzz",), "main", tmp_path, {})
        log_file.close.assert_called_once_with()

    asyncio.run(run())


def test_launch_fuzzer_appends_existing_log_on_resume(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"previous output\n")
    process = mock_process(pid=42)

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
    fuzzer = FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock())

    async def run() -> None:
        with patch("afl_run.launcher.launch_fuzzer", new=AsyncMock(return_value=fuzzer)):
            async with FuzzerGroup() as group:
                result = await group.launch(("afl-fuzz",), "main", Path("logs"), {})
                assert result is fuzzer
                assert group.fuzzers == (fuzzer,)

    asyncio.run(run())


def test_dry_run_group_does_not_spawn_processes() -> None:
    async def run() -> None:
        async with FuzzerGroup(dry_run=True) as group:
            fuzzer = await group.launch(("afl-fuzz",), "main", Path("logs"), {})
            assert group.fuzzers == (fuzzer,)
            fuzzer.process.terminate()
            fuzzer.process.kill()
            assert await fuzzer.process.wait() == 0

    asyncio.run(run())


def test_fuzzer_group_terminates_processes_on_exit() -> None:
    live_process = mock_process()
    dead_process = mock_process(returncode=1)
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
    exited_process = mock_process()
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
    process = mock_process(returncode=1)
    fuzzer = FuzzerProcess("main", process, Path("main.log"), log)

    async def run() -> None:
        async with FuzzerGroup((fuzzer,)) as group:
            with pytest.raises(RuntimeError, match=r"main.*main\.log"):
                await group.wait_for_completion()

    asyncio.run(run())
    process.wait.assert_awaited()
    log.close.assert_called_once_with()


def test_wait_for_completion_reports_all_dead_processes() -> None:
    first = FuzzerProcess("first", mock_process(returncode=1), Path("first.log"), MagicMock())
    second = FuzzerProcess("second", mock_process(returncode=2), Path("second.log"), MagicMock())

    async def run() -> None:
        with pytest.raises(RuntimeError) as error:
            await _wait_for_completion((first, second))
        assert "first" in str(error.value)
        assert "second" in str(error.value)

    asyncio.run(run())


def test_wait_for_completion_reports_wait_errors() -> None:
    process = _ErrorProcess()
    fuzzer = FuzzerProcess("broken", process, Path("broken.log"), MagicMock())

    async def run() -> None:
        with pytest.raises(RuntimeError, match="wait failed"):
            await _wait_for_completion((fuzzer,))

    asyncio.run(run())


def test_wait_for_completion_accepts_empty_group() -> None:
    asyncio.run(_wait_for_completion(()))


def _launch_probe() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["python3", "-c", "while True: pass"], close_fds=True)


def test_wait_until_ready_warns_when_startup_is_slow(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="afl_run.launcher")
    fuzzer = FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock())

    def slow_waiter(
        stats_path: Path,
        main: ProcessLike,
        since: float,
        log_path: Path | None,
        log_start_offset: int,
    ) -> None:
        time.sleep(0.2)

    async def run() -> None:
        group = FuzzerGroup()
        with (
            patch("afl_run.launcher.STARTUP_WARNING_SECONDS", 0.05),
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.01),
        ):
            await group.wait_until_ready(Path("stats"), fuzzer, slow_waiter, 0.0)

    asyncio.run(run())

    assert "fuzzer instance startup is slow" in caplog.text


def test_wait_until_ready_does_not_warn_on_fast_startup(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="afl_run.launcher")
    waiter = MagicMock()

    async def run() -> None:
        group = FuzzerGroup()
        await group.wait_until_ready(
            Path("stats"),
            FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock()),
            waiter,
            0.0,
        )

    asyncio.run(run())

    waiter.assert_called_once()
    assert "fuzzer instance startup is slow" not in caplog.text


def test_warn_if_startup_is_slow_warns_once(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="afl_run.launcher")

    assert _warn_if_startup_is_slow(STARTUP_WARNING_SECONDS, False) is True
    assert _warn_if_startup_is_slow(STARTUP_WARNING_SECONDS, True) is True

    assert caplog.text.count("fuzzer instance startup is slow") == 1


def test_warn_if_startup_is_slow_keeps_false_before_threshold() -> None:
    assert _warn_if_startup_is_slow(0, False) is False


def test_log_liveness_skips_missing_sample(caplog) -> None:
    caplog.set_level(logging.INFO, logger="afl_run.launcher")

    _log_liveness(1, None, 1)

    assert "still calibrating" not in caplog.text


def test_log_liveness_logs_sample(caplog) -> None:
    caplog.set_level(logging.INFO, logger="afl_run.launcher")

    _log_liveness(1, _LivenessSample(2, 3 * 1024**2, 4 * 1024**2), 5)

    assert "main PID 1: CPU 2s, RSS 3.0MB, log 4.0MB" in caplog.text


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, _LivenessSample(1, 1, 1), True),
        (_LivenessSample(1, 1, 1), _LivenessSample(2, 1, 1), True),
        (_LivenessSample(1, 1, 1), _LivenessSample(1, 1, 2), True),
        (_LivenessSample(1, 1, 1), _LivenessSample(1, 1, 1), False),
    ],
)
def test_has_liveness_progressed(previous, current, expected) -> None:
    assert _has_liveness_progressed(previous, current) is expected


def test_wait_until_ready_propagates_waiter_error() -> None:
    def failing_waiter(
        stats_path: Path,
        main: ProcessLike,
        since: float,
        log_path: Path | None,
        log_start_offset: int,
    ) -> None:
        raise RuntimeError("main exited")

    async def run() -> None:
        group = FuzzerGroup()
        await group.wait_until_ready(
            Path("stats"),
            FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock()),
            failing_waiter,
            0.0,
        )

    with pytest.raises(RuntimeError, match="main exited"):
        asyncio.run(run())


def test_wait_until_ready_propagates_stuck_watchdog_error() -> None:
    async def stuck_monitor(pid: int, log_path: Path, is_sleeping: Callable[[], bool]) -> None:
        raise RuntimeError("fuzzer instance appears stuck")

    def slow_waiter(
        stats_path: Path,
        main: ProcessLike,
        since: float,
        log_path: Path | None,
        log_start_offset: int,
    ) -> None:
        time.sleep(1)

    async def run() -> None:
        group = FuzzerGroup()
        with patch("afl_run.launcher._monitor_startup", new=stuck_monitor):
            await group.wait_until_ready(
                Path("stats"),
                FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock()),
                slow_waiter,
                0.0,
            )

    with pytest.raises(RuntimeError, match="appears stuck"):
        asyncio.run(run())


def test_monitor_flags_stuck_main(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.02),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 0.1),
        ):
            await asyncio.wait_for(_monitor_startup(child.pid, log_path, lambda: False), timeout=5)

    try:
        with pytest.raises(RuntimeError, match="appears stuck"):
            asyncio.run(run())
    finally:
        child.terminate()
        child.wait()


def test_monitor_allows_progressing_main(tmp_path: Path) -> None:
    child = _launch_probe()
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.02),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 0.15),
        ):
            await asyncio.wait_for(
                _monitor_startup(child.pid, log_path, lambda: False), timeout=0.4
            )

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(run())
    finally:
        child.kill()
        child.wait()


def test_monitor_logs_liveness(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="afl_run.launcher")
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"abc")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.01),
            patch("afl_run.launcher.LIVENESS_LOG_SECONDS", 0.05),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 999),
        ):
            await asyncio.wait_for(
                _monitor_startup(child.pid, log_path, lambda: False), timeout=0.3
            )

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(run())
    finally:
        child.terminate()
        child.wait()

    assert "still calibrating" in caplog.text


def test_monitor_tolerates_unreadable_proc(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.01),
            patch("afl_run.launcher.LIVENESS_LOG_SECONDS", 0.05),
        ):
            await asyncio.wait_for(_monitor_startup(-1, log_path, lambda: False), timeout=0.2)

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_monitor_counts_log_growth_as_progress(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.05),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 0.2),
        ):
            monitor = asyncio.ensure_future(_monitor_startup(child.pid, log_path, lambda: False))
            for _ in range(6):
                await asyncio.sleep(0.05)
                with log_path.open("ab") as log_file:
                    log_file.write(b"x")
            monitor.cancel()
            result = (await asyncio.gather(monitor, return_exceptions=True))[0]
            assert result is None or isinstance(result, asyncio.CancelledError)

    try:
        asyncio.run(run())
    finally:
        child.terminate()
        child.wait()


def test_sample_liveness_reads_proc_and_log(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"abc")

    try:
        # Poll until the freshly spawned process is observable; sampling it
        # immediately can race with process bookkeeping under a busy suite.
        sample = None
        for _ in range(100):
            sample = _sample_liveness(child.pid, log_path)
            if sample is not None and sample[1] > 0:
                break
            time.sleep(0.01)
    finally:
        child.terminate()
        child.wait()

    assert sample is not None
    assert sample[0] >= 0
    assert sample[1] > 0
    assert sample[2] == 3


def test_sample_liveness_returns_none_for_missingmock_process(tmp_path: Path) -> None:
    assert _sample_liveness(-1, tmp_path / "main.log") is None


def test_sample_liveness_handles_missing_log(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])

    try:
        sample = _sample_liveness(child.pid, tmp_path / "missing.log")
    finally:
        child.terminate()
        child.wait()

    assert sample is not None
    assert sample[2] == 0


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [(45 * 1024 * 1024, "45.0MB"), (2_684_354_560, "2.5GB")],
)
def test_format_size(size_bytes: int, expected: str) -> None:
    assert _format_size(size_bytes) == expected


def test_fuzzer_group_cancels_other_waiters() -> None:
    pending_process = _PendingProcess()
    first = FuzzerProcess("first", mock_process(returncode=1), Path("first.log"), MagicMock())
    pending = FuzzerProcess("pending", pending_process, Path("pending.log"), MagicMock())

    async def run() -> None:
        async with FuzzerGroup((first, pending)) as group:
            with pytest.raises(RuntimeError):
                await group.wait_for_completion()

    asyncio.run(run())

    assert pending_process.calls == 2
    assert pending_process.cancelled is True


def test_fuzzer_group_toggle_sleep_stops_and_continues_live_processes() -> None:
    live = FuzzerProcess("live", mock_process(), Path("live.log"), MagicMock())
    dead = FuzzerProcess("dead", mock_process(returncode=1), Path("dead.log"), MagicMock())

    async def run() -> None:
        async with FuzzerGroup((live, dead)) as group:
            with (
                patch("afl_run.launcher.os.getpgid", return_value=live.pid),
                patch("afl_run.launcher.os.killpg") as killpg,
            ):
                assert group.is_sleeping is False
                assert group.toggle_sleep() is True
                assert group.is_sleeping is True
                assert group.toggle_sleep() is False
                assert group.is_sleeping is False
                # Dead fuzzers (returncode set) are skipped.
                killpg.assert_has_calls(
                    [
                        call(live.pid, signal.SIGSTOP),
                        call(live.pid, signal.SIGCONT),
                    ]
                )

    asyncio.run(run())


def test_dry_run_group_toggle_sleep_only_tracks_state() -> None:
    async def run() -> None:
        async with FuzzerGroup(dry_run=True) as group:
            fuzzer = await group.launch(("afl-fuzz",), "main", Path("logs"), {})
            fuzzer.process.terminate()
            fuzzer.process.kill()
            assert await fuzzer.process.wait() == 0
            assert group.toggle_sleep() is True
            assert group.is_sleeping is True

    asyncio.run(run())


def test_handle_sleep_signal_logs_sleep_and_resume(caplog) -> None:
    caplog.set_level(logging.INFO, logger="afl_run.launcher")

    async def run() -> None:
        async with FuzzerGroup() as group:
            handle_sleep_signal(group)
            assert "fuzzers sleeping" in caplog.text
            handle_sleep_signal(group)
            assert "fuzzers resumed" in caplog.text

    asyncio.run(run())


def test_signal_process_group_warns_on_failure(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="afl_run.launcher")

    with patch("afl_run.launcher.os.getpgid", side_effect=ProcessLookupError("no such pid")):
        _signal_process_group(999, signal.SIGSTOP)

    assert "could not signal fuzzer process group 999" in caplog.text


def test_signal_process_group_warns_on_permission_error(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="afl_run.launcher")

    with (
        patch("afl_run.launcher.os.getpgid", return_value=999),
        patch("afl_run.launcher.os.killpg", side_effect=PermissionError("denied")),
    ):
        _signal_process_group(999, signal.SIGSTOP)

    assert "could not signal fuzzer process group 999" in caplog.text


def test_signal_process_group_signals_group_on_success() -> None:
    with (
        patch("afl_run.launcher.os.getpgid", return_value=7),
        patch("afl_run.launcher.os.killpg") as killpg,
    ):
        _signal_process_group(7, signal.SIGCONT)

    killpg.assert_called_once_with(7, signal.SIGCONT)


def test_read_log_tail_returns_last_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_text("first\nsecond\nthird\n")

    assert read_log_tail(log_path) == "first\nsecond\nthird"


def test_read_log_tail_limits_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_text("".join(f"line {index}\n" for index in range(LOG_TAIL_LINES + 10)))

    tail = read_log_tail(log_path)

    assert tail is not None
    assert tail.splitlines()[0] == f"line {10}"
    assert len(tail.splitlines()) == LOG_TAIL_LINES


def test_read_log_tail_missing_log_returns_none(tmp_path: Path) -> None:
    assert read_log_tail(tmp_path / "missing.log") is None


def test_read_log_tail_directory_returns_none(tmp_path: Path) -> None:
    assert read_log_tail(tmp_path) is None


def test_read_log_tail_empty_log_returns_none(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    assert read_log_tail(log_path) is None


def test_read_log_tail_blank_log_returns_none(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"\n")

    assert read_log_tail(log_path) is None


def test_read_log_tail_caps_huge_log(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"x" * (LOG_TAIL_MAX_BYTES + 100) + b"\nPROGRAM ABORT : aborted\n")

    assert read_log_tail(log_path) == "PROGRAM ABORT : aborted"


def test_read_log_tail_strips_ansi_escapes(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"\x1b[1;91mPROGRAM ABORT\x1b[0m : Pipe at the beginning\n")

    assert read_log_tail(log_path) == "PROGRAM ABORT : Pipe at the beginning"


def test_format_log_tail_renders_tail_block(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_text("boom\n")

    assert format_log_tail(log_path) == f"\n--- tail of {log_path} ---\nboom"


def test_format_log_tail_skips_unreadable_log(tmp_path: Path) -> None:
    assert format_log_tail(tmp_path / "missing.log") == ""


def test_wait_for_completion_includes_log_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "w1.log"
    log_path.write_text("afl-fuzz: target died\n")
    fuzzer = FuzzerProcess("w1", mock_process(returncode=1), log_path, MagicMock())

    async def run() -> RuntimeError:
        with pytest.raises(RuntimeError) as error:
            await _wait_for_completion((fuzzer,))
        return error.value

    error = asyncio.run(run())

    message = str(error)
    assert f"fuzzer w1 exited with code 1; see log {log_path}" in message
    assert f"--- tail of {log_path} ---" in message
    assert "afl-fuzz: target died" in message


def test_read_log_tail_ignores_output_before_start_offset(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    previous = b"PROGRAM ABORT : previous run\n"
    log_path.write_bytes(previous + b"this run\n")

    assert read_log_tail(log_path, len(previous)) == "this run"


def test_read_log_tail_returns_none_when_nothing_written_after_offset(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"previous run\n")

    assert read_log_tail(log_path, log_path.stat().st_size) is None


def test_read_log_tail_keeps_full_first_line_when_cap_lands_on_line_boundary(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "main.log"
    window = b"a" * (LOG_TAIL_MAX_BYTES - 1) + b"\n"
    log_path.write_bytes(b"older\n" + window)

    assert read_log_tail(log_path) == "a" * (LOG_TAIL_MAX_BYTES - 1)


def test_format_log_tail_honours_start_offset(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"old\nnew\n")

    assert format_log_tail(log_path, 4) == f"\n--- tail of {log_path} ---\nnew"


def test_launch_fuzzer_records_log_size_when_appending(tmp_path: Path) -> None:
    (tmp_path / "main.log").write_bytes(b"previous output\n")

    async def run() -> FuzzerProcess:
        with patch(
            "afl_run.launcher.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_process()),
        ):
            return await launch_fuzzer(("afl-fuzz",), "main", tmp_path, {}, append=True)

    result = asyncio.run(run())
    result.log_file.close()

    assert result.log_start_offset == len(b"previous output\n")


def test_launch_fuzzer_starts_log_offset_at_zero_when_truncating(tmp_path: Path) -> None:
    (tmp_path / "main.log").write_bytes(b"previous output\n")

    async def run() -> FuzzerProcess:
        with patch(
            "afl_run.launcher.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_process()),
        ):
            return await launch_fuzzer(("afl-fuzz",), "main", tmp_path, {})

    result = asyncio.run(run())
    result.log_file.close()

    assert result.log_start_offset == 0


def test_wait_for_completion_ignores_previous_run_output(tmp_path: Path) -> None:
    log_path = tmp_path / "w1.log"
    log_path.write_bytes(b"failure from the PREVIOUS run\nthis run\n")
    offset = len(b"failure from the PREVIOUS run\n")
    fuzzer = FuzzerProcess("w1", mock_process(returncode=-9), log_path, MagicMock(), offset)

    async def run() -> RuntimeError:
        with pytest.raises(RuntimeError) as error:
            await _wait_for_completion((fuzzer,))
        return error.value

    message = str(asyncio.run(run()))

    assert "this run" in message
    assert "PREVIOUS" not in message


def test_wait_until_ready_passes_log_start_offset_to_waiter() -> None:
    waiter = MagicMock()
    main = FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock(), 17)

    async def run() -> None:
        await FuzzerGroup().wait_until_ready(Path("stats"), main, waiter, 1.5)

    asyncio.run(run())

    waiter.assert_called_once_with(Path("stats"), main.process, 1.5, Path("main.log"), 17)


def test_monitor_does_not_flag_sleeping_main(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.02),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 0.1),
        ):
            await asyncio.wait_for(_monitor_startup(child.pid, log_path, lambda: True), timeout=0.4)

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(run())
    finally:
        child.terminate()
        child.wait()


def test_group_monitor_sees_sleep_state() -> None:
    seen: list[bool] = []
    group = FuzzerGroup(dry_run=False)

    async def spy_monitor(pid: int, log_path: Path, is_sleeping: Callable[[], bool]) -> None:
        seen.append(is_sleeping())
        group.toggle_sleep()
        seen.append(is_sleeping())
        group.toggle_sleep()
        seen.append(is_sleeping())
        raise RuntimeError("stop")

    def slow_waiter(*args: object) -> None:
        time.sleep(0.5)

    async def run() -> None:
        group.toggle_sleep()
        with patch("afl_run.launcher._monitor_startup", new=spy_monitor):
            await group.wait_until_ready(
                Path("stats"),
                FuzzerProcess("main", mock_process(), Path("main.log"), MagicMock()),
                slow_waiter,
                0.0,
            )

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(run())

    assert seen == [True, False, True]


def test_read_wait_channel_reports_unavailable_process() -> None:
    assert _read_wait_channel(2**30) == "wait channel unavailable"


def test_read_wait_channel_reports_channel_name() -> None:
    with patch("pathlib.Path.read_text", return_value="do_epoll_wait\n"):
        assert _read_wait_channel(1) == "wait channel do_epoll_wait"


def test_read_wait_channel_reports_zero_for_empty_channel() -> None:
    with patch("pathlib.Path.read_text", return_value=""):
        assert _read_wait_channel(1) == "wait channel 0"


@pytest.mark.parametrize("sleeping", [True, False])
def test_monitor_logs_slow_startup_and_liveness_only_when_awake(
    sleeping: bool, tmp_path: Path, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="afl_run.launcher")
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.02),
            patch("afl_run.launcher.STARTUP_WARNING_SECONDS", 0),
            patch("afl_run.launcher.LIVENESS_LOG_SECONDS", 0.01),
        ):
            await asyncio.wait_for(
                _monitor_startup(child.pid, log_path, lambda: sleeping), timeout=0.2
            )

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(run())
    finally:
        child.terminate()
        child.wait()

    assert ("startup is slow" in caplog.text) is not sleeping
    assert ("still calibrating" in caplog.text) is not sleeping


def test_stuck_message_reports_state_instead_of_stale_proc_path(tmp_path: Path) -> None:
    child = subprocess.Popen(["sleep", "30"])
    log_path = tmp_path / "main.log"
    log_path.write_bytes(b"")

    async def run() -> None:
        with (
            patch("afl_run.launcher.LIVENESS_PROBE_SECONDS", 0.02),
            patch("afl_run.launcher.STARTUP_STUCK_SECONDS", 0.1),
        ):
            await asyncio.wait_for(_monitor_startup(child.pid, log_path, lambda: False), timeout=5)

    try:
        with pytest.raises(RuntimeError) as error:
            asyncio.run(run())
    finally:
        child.terminate()
        child.wait()

    assert "wait channel" in str(error.value)
    assert "/proc/" not in str(error.value)


class _CancelledDuringShutdownProcess:
    pid = 5

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


def test_cancellation_during_shutdown_kills_fuzzers_before_reraising() -> None:
    process = _CancelledDuringShutdownProcess()
    log = MagicMock()
    fuzzer = FuzzerProcess("stubborn", process, Path("stubborn.log"), log)

    async def run() -> None:
        with (
            patch("afl_run.launcher.os.killpg") as killpg,
            patch("afl_run.launcher.os.getpgid", return_value=process.pid),
        ):
            task = asyncio.ensure_future(_terminate_fuzzers((fuzzer,)))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            killpg.assert_any_call(process.pid, signal.SIGKILL)

    asyncio.run(run())

    assert process.killed
    log.close.assert_called_once_with()


class _ControlledProcess:
    def __init__(self, pid: int = 1) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._finished = asyncio.Event()

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        self._finished.set()

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        await self._finished.wait()
        assert self.returncode is not None
        return self.returncode


def _controlled_fuzzer(name: str, process: _ControlledProcess) -> FuzzerProcess:
    return FuzzerProcess(name, process, Path(f"{name}.log"), MagicMock())


def test_wait_for_completion_returns_when_all_fuzzers_exit_cleanly() -> None:
    first, second = _ControlledProcess(), _ControlledProcess()

    async def run() -> None:
        group = FuzzerGroup((_controlled_fuzzer("a", first), _controlled_fuzzer("b", second)))
        waiting = asyncio.ensure_future(group.wait_for_completion())
        await asyncio.sleep(0.01)
        first.finish(0)
        await asyncio.sleep(0.01)
        assert not waiting.done()
        second.finish(0)
        await asyncio.wait_for(waiting, timeout=1)

    asyncio.run(run())


def test_wait_for_completion_fails_on_nonzero_exit_while_others_run() -> None:
    clean, failing, running = _ControlledProcess(), _ControlledProcess(), _ControlledProcess()
    clean.finish(0)
    failing.finish(3)

    async def run() -> None:
        group = FuzzerGroup(
            (
                _controlled_fuzzer("clean", clean),
                _controlled_fuzzer("failing", failing),
                _controlled_fuzzer("running", running),
            )
        )
        await group.wait_for_completion()

    with pytest.raises(RuntimeError) as error:
        asyncio.run(run())

    assert "fuzzer failing exited with code 3" in str(error.value)
    assert "clean" not in str(error.value)


def test_wait_for_completion_times_out_after_fuzzing_seconds() -> None:
    process = _ControlledProcess()

    async def run() -> None:
        group = FuzzerGroup((_controlled_fuzzer("a", process),))
        await group.wait_for_completion(0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_wait_for_completion_does_not_count_sleeping_time() -> None:
    process = _ControlledProcess()

    async def run() -> float:
        group = FuzzerGroup((_controlled_fuzzer("a", process),), dry_run=False)
        waiting = asyncio.ensure_future(group.wait_for_completion(0.3))
        await asyncio.sleep(0.1)
        group.toggle_sleep()
        await asyncio.sleep(0.5)
        assert not waiting.done()
        resumed = time.monotonic()
        group.toggle_sleep()
        with pytest.raises(TimeoutError):
            await waiting
        return time.monotonic() - resumed

    awake_after_resume = asyncio.run(run())

    assert 0.1 <= awake_after_resume < 0.35


def test_wait_for_completion_reports_fuzzer_that_dies_while_sleeping() -> None:
    process = _ControlledProcess()

    async def run() -> None:
        group = FuzzerGroup((_controlled_fuzzer("a", process),))
        waiting = asyncio.ensure_future(group.wait_for_completion(10))
        await asyncio.sleep(0.01)
        group.toggle_sleep()
        await asyncio.sleep(0.01)
        process.finish(-9)
        await waiting

    with pytest.raises(RuntimeError, match="fuzzer a exited with code -9"):
        asyncio.run(run())


def test_wait_for_completion_without_timeout_is_not_bounded_by_sleep_toggles() -> None:
    process = _ControlledProcess()

    async def run() -> None:
        group = FuzzerGroup((_controlled_fuzzer("a", process),))
        waiting = asyncio.ensure_future(group.wait_for_completion())
        await asyncio.sleep(0.01)
        group.toggle_sleep()
        group.toggle_sleep()
        process.finish(0)
        await asyncio.wait_for(waiting, timeout=1)

    asyncio.run(run())
