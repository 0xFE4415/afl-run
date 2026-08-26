from __future__ import annotations

import asyncio
import io
import logging
import os
import shlex
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

    @property
    def pid(self) -> int:
        return self.process.pid


class FuzzerGroup:
    def __init__(self, fuzzers: Iterable[FuzzerProcess] = (), *, dry_run: bool = False) -> None:
        self._fuzzers = list(fuzzers)
        self._dry_run = dry_run

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

    async def abort_if_any_died(self) -> None:
        if self._dry_run:
            return
        await _abort_if_any_died(self.fuzzers)

    async def wait_for_main(
        self,
        stats_path: Path,
        main: FuzzerProcess,
        waiter: Callable[[Path, ProcessLike, float], None],
        since: float,
    ) -> None:
        if self._dry_run:
            return
        waiter_task = asyncio.ensure_future(
            asyncio.to_thread(waiter, stats_path, main.process, since)
        )
        monitor_task = asyncio.ensure_future(_monitor_main_startup(main.pid, main.log_path))
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


async def _monitor_main_startup(pid: int, log_path: Path) -> None:
    started = time.monotonic()
    last_progress = started
    warned_slow = False
    next_liveness_log = LIVENESS_LOG_SECONDS
    sample = _sample_liveness(pid, log_path)
    while True:
        elapsed = time.monotonic() - started
        warned_slow = _warn_if_startup_is_slow(elapsed, warned_slow)
        if elapsed >= next_liveness_log:
            next_liveness_log += LIVENESS_LOG_SECONDS
            _log_liveness(pid, sample, elapsed)
        await asyncio.sleep(LIVENESS_PROBE_SECONDS)
        current = _sample_liveness(pid, log_path)
        if current is None:
            continue
        if _has_liveness_progressed(sample, current):
            sample = current
            last_progress = time.monotonic()
        elif time.monotonic() - last_progress >= STARTUP_STUCK_SECONDS:
            raise RuntimeError(
                f"main instance appears stuck: no CPU or log progress for"
                f" {STARTUP_STUCK_SECONDS:.0f}s (PID {pid},"
                f" CPU {current.cpu_seconds:.0f}s, RSS {_format_size(current.rss_bytes)},"
                f" log {_format_size(current.log_bytes)});"
                f" inspect {log_path} and /proc/{pid}/stack"
            )


def _warn_if_startup_is_slow(elapsed: float, warned: bool) -> bool:
    if warned or elapsed < STARTUP_WARNING_SECONDS:
        return warned
    LOGGER.warning(
        "main instance startup is slow; no fuzzer_stats after %s seconds,"
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
    try:
        LOGGER.info("starting %s: %s", name, shlex.join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=child_environment,
        )
    except BaseException:
        log_file.close()
        raise
    return FuzzerProcess(name, process, log_path, log_file)


async def _abort_if_any_died(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    if not fuzzers:
        return
    tasks = {asyncio.create_task(fuzzer.process.wait()): fuzzer for fuzzer in fuzzers}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    errors = [(tasks[task], task.exception()) for task in done]
    for fuzzer, error in errors:
        if error is not None:
            raise RuntimeError(f"fuzzer {fuzzer.name} wait failed: {error}") from error
    exited = tuple(tasks[task] for task in done)
    details = "; ".join(
        f"fuzzer {fuzzer.name} exited with code {fuzzer.process.returncode}; "
        f"see log {fuzzer.log_path}"
        for fuzzer in exited
    )
    raise RuntimeError(details)


async def _terminate_fuzzers(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    live_fuzzers = tuple(fuzzer for fuzzer in fuzzers if fuzzer.process.returncode is None)
    for fuzzer in live_fuzzers:
        fuzzer.process.terminate()
    try:
        await asyncio.wait_for(
            asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers)),
            timeout=SHUTDOWN_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        for fuzzer in live_fuzzers:
            if fuzzer.process.returncode is None:
                fuzzer.process.kill()
        await asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers))
    finally:
        _close_logs(fuzzers)


def _close_logs(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    for fuzzer in fuzzers:
        fuzzer.log_file.close()
