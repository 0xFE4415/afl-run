from __future__ import annotations

import asyncio
import io
import logging
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Protocol, Self

LOGGER = logging.getLogger(__name__)
SHUTDOWN_TIMEOUT_SECONDS = 5


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

    async def wait_for_master(
        self,
        stats_path: Path,
        master: ProcessLike,
        waiter: Callable[[Path, ProcessLike], None],
        timeout: float,
    ) -> None:
        if self._dry_run:
            return
        await asyncio.wait_for(
            asyncio.to_thread(waiter, stats_path, master),
            timeout=timeout,
        )


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
