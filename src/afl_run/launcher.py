from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


class AsyncProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    async def wait(self) -> int: ...


@dataclass
class FuzzerProcess:
    name: str
    process: AsyncProcess
    log_path: Path
    log_file: BinaryIO

    @property
    def pid(self) -> int:
        return self.process.pid


async def launch_fuzzer(
    command: tuple[str, ...],
    name: str,
    log_dir: Path,
    environment: Mapping[str, str],
    tmp_root: Path | None = None,
) -> FuzzerProcess:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    child_environment = dict(environment)
    if tmp_root is not None:
        tmp_dir = tmp_root / name
        tmp_dir.mkdir(parents=True, exist_ok=True)
        child_environment["AFL_TMPDIR"] = str(tmp_dir)

    log_file = log_path.open("wb")
    try:
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


async def abort_if_any_died(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    tasks = {asyncio.create_task(fuzzer.process.wait()): fuzzer for fuzzer in fuzzers}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    fuzzer = tasks[next(iter(done))]
    await terminate_fuzzers(fuzzers)
    raise RuntimeError(f"fuzzer {fuzzer.name} exited with code {fuzzer.process.returncode}")


async def terminate_fuzzers(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    for fuzzer in fuzzers:
        if fuzzer.process.returncode is None:
            fuzzer.process.terminate()
    try:
        await asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers))
    finally:
        _close_logs(fuzzers)


async def wait_for_fuzzers(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    try:
        await asyncio.gather(*(fuzzer.process.wait() for fuzzer in fuzzers))
    finally:
        _close_logs(fuzzers)


def _close_logs(fuzzers: tuple[FuzzerProcess, ...]) -> None:
    for fuzzer in fuzzers:
        fuzzer.log_file.close()
