from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from collections.abc import Iterable, Mapping
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
    def __init__(self, fuzzers: Iterable[FuzzerProcess] = ()) -> None:
        self._fuzzers = list(fuzzers)

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
        fuzzer = await launch_fuzzer(command, name, log_dir, environment, tmp_root, append)
        self._fuzzers.append(fuzzer)
        return fuzzer

    async def abort_if_any_died(self) -> None:
        await _abort_if_any_died(self.fuzzers)


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
    tasks = {asyncio.create_task(fuzzer.process.wait()): fuzzer for fuzzer in fuzzers}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    fuzzer = tasks[next(iter(done))]
    raise RuntimeError(
        f"fuzzer {fuzzer.name} exited with code {fuzzer.process.returncode}; "
        f"see log {fuzzer.log_path}"
    )


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
