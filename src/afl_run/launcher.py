from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, NamedTuple, Protocol, Self

LOGGER = logging.getLogger(__name__)
SHUTDOWN_TIMEOUT_SECONDS = 5
STARTUP_WARNING_SECONDS = 30
LIVENESS_PROBE_SECONDS = 15
LIVENESS_LOG_SECONDS = 300
STARTUP_STUCK_SECONDS = 300
LOG_TAIL_LINES = 25
LOG_TAIL_MAX_BYTES = 8192
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class ProcessLike(Protocol):
    @property
    def pid(self) -> int: ...


class AsyncProcess(ProcessLike, Protocol):
    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class _DryRunProcess:
    pid = -1
    returncode = 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return 0


@dataclass(frozen=True)
class FuzzerProcess:
    name: str
    process: AsyncProcess
    log_path: Path
    log_file: BinaryIO
    log_start_offset: int = 0

    @property
    def pid(self) -> int:
        return self.process.pid


class FuzzerGroup:
    def __init__(self, fuzzers: Iterable[FuzzerProcess] = (), *, dry_run: bool = False) -> None:
        self._fuzzers = list(fuzzers)
        self._dry_run = dry_run
        self._sleeping = False
        self._awake = asyncio.Event()
        self._awake.set()
        self._asleep = asyncio.Event()

    @property
    def fuzzers(self) -> tuple[FuzzerProcess, ...]:
        return tuple(self._fuzzers)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await _terminate_fuzzers(self.fuzzers)

    async def launch(
        self,
        command: tuple[str, ...],
        name: str,
        log_dir: Path,
        environment: Mapping[str, str],
        tmp_root: Path | None = None,
        append: bool = False,
    ) -> FuzzerProcess:
        if self._dry_run:
            LOGGER.info("would start %s: %s", name, shlex.join(command))
            fuzzer = FuzzerProcess(
                name,
                _DryRunProcess(),
                log_dir / f"{name}.log",
                io.BytesIO(),
            )
        else:
            fuzzer = await launch_fuzzer(command, name, log_dir, environment, tmp_root, append)
        self._fuzzers.append(fuzzer)
        return fuzzer

    async def wait_for_completion(self, fuzzing_seconds: float | None = None) -> None:
        if self._dry_run:
            return
        completion = asyncio.ensure_future(_wait_for_completion(self.fuzzers))
        remaining = fuzzing_seconds
        try:
            while True:
                if self._sleeping:
                    await _wait_for_completion_or_event(completion, self._awake, None)
                else:
                    started = time.monotonic()
                    await _wait_for_completion_or_event(completion, self._asleep, remaining)
                    if remaining is not None:
                        remaining -= time.monotonic() - started
                if completion.done():
                    completion.result()
                    return
                if remaining is not None and remaining <= 0:
                    raise TimeoutError
        finally:
            completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)

    @property
    def is_sleeping(self) -> bool:
        return self._sleeping

    def toggle_sleep(self) -> bool:
        now_sleeping = not self._sleeping
        self._sleeping = now_sleeping
        if now_sleeping:
            self._awake.clear()
            self._asleep.set()
        else:
            self._asleep.clear()
            self._awake.set()
        if self._dry_run:
            return now_sleeping
        signal_to_send = signal.SIGSTOP if now_sleeping else signal.SIGCONT
        for fuzzer in self._fuzzers:
            if fuzzer.process.returncode is None:
                _signal_process_group(fuzzer.pid, signal_to_send)
        return now_sleeping

    async def wait_until_ready(
        self,
        stats_path: Path,
        main: FuzzerProcess,
        waiter: Callable[[Path, ProcessLike, float, Path | None, int], None],
        since: float,
    ) -> None:
        if self._dry_run:
            return
        waiter_task = asyncio.ensure_future(
            asyncio.to_thread(
                waiter, stats_path, main.process, since, main.log_path, main.log_start_offset
            )
        )
        monitor_task = asyncio.ensure_future(
            _monitor_startup(main.pid, main.log_path, lambda: self._sleeping)
        )
        done, pending = await asyncio.wait(
            {waiter_task, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # The completed task's result() raises its exception, if any; a
        # successful waiter takes priority over a watchdog failure.
        if waiter_task in done:
            waiter_task.result()
        else:
            monitor_task.result()


class _LivenessSample(NamedTuple):
    cpu_seconds: float
    rss_bytes: int
    log_bytes: int


async def _monitor_startup(pid: int, log_path: Path, is_sleeping: Callable[[], bool]) -> None:
    started = time.monotonic()
    last_progress = started
    warned_slow = False
    next_liveness_log = LIVENESS_LOG_SECONDS
    sample = _sample_liveness(pid, log_path)
    while True:
        elapsed = time.monotonic() - started
        if not is_sleeping():
            warned_slow = _warn_if_startup_is_slow(elapsed, warned_slow)
            if elapsed >= next_liveness_log:
                next_liveness_log += LIVENESS_LOG_SECONDS
                _log_liveness(pid, sample, elapsed)
        await asyncio.sleep(LIVENESS_PROBE_SECONDS)
        current = _sample_liveness(pid, log_path)
        if current is None:
            continue
        if is_sleeping():
            sample = current
            last_progress = time.monotonic()
        elif _has_liveness_progressed(sample, current):
            sample = current
            last_progress = time.monotonic()
        elif time.monotonic() - last_progress >= STARTUP_STUCK_SECONDS:
            raise RuntimeError(
                f"fuzzer instance appears stuck: no CPU or log progress for"
                f" {STARTUP_STUCK_SECONDS:.0f}s (PID {pid},"
                f" CPU {current.cpu_seconds:.0f}s, RSS {_format_size(current.rss_bytes)},"
                f" log {_format_size(current.log_bytes)}, {_read_wait_channel(pid)});"
                f" inspect {log_path}"
            )


def _read_wait_channel(pid: int) -> str:
    try:
        wait_channel = Path(f"/proc/{pid}/wchan").read_text().strip()
    except OSError:
        return "wait channel unavailable"
    return f"wait channel {wait_channel or '0'}"


def _warn_if_startup_is_slow(elapsed: float, warned: bool) -> bool:
    if warned or elapsed < STARTUP_WARNING_SECONDS:
        return warned
    LOGGER.warning(
        "fuzzer instance startup is slow; no fuzzer_stats after %s seconds,"
        " still waiting (large seed corpora take minutes to calibrate)",
        STARTUP_WARNING_SECONDS,
    )
    return True


def _log_liveness(pid: int, sample: _LivenessSample | None, elapsed: float) -> None:
    if sample is None:
        return
    LOGGER.info(
        "main PID %d: CPU %.0fs, RSS %s, log %s — still calibrating (%.0fs elapsed)",
        pid,
        sample.cpu_seconds,
        _format_size(sample.rss_bytes),
        _format_size(sample.log_bytes),
        elapsed,
    )


def _has_liveness_progressed(previous: _LivenessSample | None, current: _LivenessSample) -> bool:
    return (
        previous is None
        or current.cpu_seconds > previous.cpu_seconds
        or current.log_bytes > previous.log_bytes
    )


def _sample_liveness(pid: int, log_path: Path) -> _LivenessSample | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
    except OSError:
        return None
    cpu_ticks = int(fields[11]) + int(fields[12]) + int(fields[13]) + int(fields[14])
    try:
        log_bytes = log_path.stat().st_size
    except OSError:
        log_bytes = 0
    return _LivenessSample(
        cpu_seconds=cpu_ticks / os.sysconf("SC_CLK_TCK"),
        rss_bytes=int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
        log_bytes=log_bytes,
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.1f}GB"
    return f"{size_bytes / 1024**2:.1f}MB"


async def launch_fuzzer(
    command: tuple[str, ...],
    name: str,
    log_dir: Path,
    environment: Mapping[str, str],
    tmp_root: Path | None = None,
    append: bool = False,
) -> FuzzerProcess:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    child_environment = dict(environment)
    if tmp_root is not None:
        tmp_dir = tmp_root / name
        tmp_dir.mkdir(parents=True, exist_ok=True)
        child_environment["AFL_TMPDIR"] = str(tmp_dir)

    log_file = log_path.open("ab" if append else "wb")
    log_start_offset = log_file.tell()
    try:
        LOGGER.info("starting %s: %s", name, shlex.join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=child_environment,
            start_new_session=True,
        )
    except BaseException:
        log_file.close()
        raise
    return FuzzerProcess(name, process, log_path, log_file, log_start_offset)


async def _wait_for_completion_or_event(
    completion: asyncio.Future[None], event: asyncio.Event, timeout: float | None
) -> None:
    event_task = asyncio.ensure_future(event.wait())
    try:
        await asyncio.wait(
            {completion, event_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        event_task.cancel()


async def _wait_for_completion(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    pending = {asyncio.create_task(fuzzer.process.wait()): fuzzer for fuzzer in fuzzers}
    try:
        while pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            exited: list[FuzzerProcess] = []
            for task in done:
                fuzzer = pending.pop(task)
                error = task.exception()
                if error is not None:
                    raise RuntimeError(f"fuzzer {fuzzer.name} wait failed: {error}") from error
                exited.append(fuzzer)
            failed = tuple(fuzzer for fuzzer in exited if fuzzer.process.returncode != 0)
            if failed:
                raise RuntimeError(_describe_failed_fuzzers(failed))
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def _describe_failed_fuzzers(failed: tuple[FuzzerProcess, ...]) -> str:
    details = "; ".join(
        f"fuzzer {fuzzer.name} exited with code {fuzzer.process.returncode}; "
        f"see log {fuzzer.log_path}"
        for fuzzer in failed
    )
    return details + "".join(
        format_log_tail(fuzzer.log_path, fuzzer.log_start_offset) for fuzzer in failed
    )


def read_log_tail(log_path: Path, start_offset: int = 0) -> str | None:
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            window_start = max(size - LOG_TAIL_MAX_BYTES, start_offset)
            cuts_into_line = window_start > start_offset
            log_file.seek(window_start - 1 if cuts_into_line else window_start)
            data = log_file.read()
    except OSError:
        return None
    starts_on_line_boundary = not cuts_into_line or data[:1] == b"\n"
    if cuts_into_line:
        data = data[1:]
    lines = [
        ANSI_ESCAPE_PATTERN.sub("", line)
        for line in data.decode("utf-8", errors="replace").splitlines()
    ]
    if not starts_on_line_boundary:
        lines = lines[1:]
    return "\n".join(lines[-LOG_TAIL_LINES:]) or None


def format_log_tail(log_path: Path, start_offset: int = 0) -> str:
    tail = read_log_tail(log_path, start_offset)
    if tail is None:
        return ""
    return f"\n--- tail of {log_path} ---\n{tail}"


async def _terminate_fuzzers(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    live_fuzzers = tuple(fuzzer for fuzzer in fuzzers if fuzzer.process.returncode is None)
    # A stopped process only acts on SIGTERM after SIGCONT; SIGKILL needs no resume.
    for fuzzer in live_fuzzers:
        _signal_process_group(fuzzer.pid, signal.SIGCONT)
    for fuzzer in live_fuzzers:
        fuzzer.process.terminate()
    try:
        await asyncio.wait_for(
            asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers)),
            timeout=SHUTDOWN_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _kill_and_reap(fuzzers, live_fuzzers)
    except asyncio.CancelledError:
        await _kill_and_reap(fuzzers, live_fuzzers)
        raise
    finally:
        _close_logs(fuzzers)


async def _kill_and_reap(
    fuzzers: tuple[FuzzerProcess, ...], live_fuzzers: tuple[FuzzerProcess, ...]
) -> None:
    for fuzzer in live_fuzzers:
        if fuzzer.process.returncode is None:
            _signal_process_group(fuzzer.pid, signal.SIGKILL)
            fuzzer.process.kill()
    await asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers))


def _close_logs(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    for fuzzer in fuzzers:
        fuzzer.log_file.close()


def _signal_process_group(pid: int, signum: signal.Signals) -> None:
    # Each fuzzer is started in its own session (see launch_fuzzer), so its
    # process group id equals its pid. Signalling the group stops the fuzzer
    # together with any descendant processes (target, CmpLog helpers, ...).
    try:
        os.killpg(os.getpgid(pid), signum)
    except (ProcessLookupError, PermissionError) as error:
        LOGGER.warning("could not signal fuzzer process group %d: %s", pid, error)


def handle_sleep_signal(group: FuzzerGroup) -> None:
    if group.toggle_sleep():
        LOGGER.info("fuzzers sleeping; press Ctrl-Z to resume")
    else:
        LOGGER.info("fuzzers resumed; press Ctrl-Z to sleep again")
