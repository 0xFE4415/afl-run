from __future__ import annotations

import logging
import subprocess
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from afl_run.config import HostConfig

LOGGER = logging.getLogger(__name__)
RANDOMIZE_VA_SPACE = Path("/proc/sys/kernel/randomize_va_space")
CORE_PATTERN = Path("/proc/sys/kernel/core_pattern")


@contextmanager
def configured_host(config: HostConfig) -> Generator[None]:
    settings = (
        (RANDOMIZE_VA_SPACE, config.randomize_va_space),
        (CORE_PATTERN, config.core_pattern),
    )
    changes = []
    for path, value in settings:
        previous = path.read_text().strip()
        if previous != value:
            changes.append((path, previous, value))
    if not changes:
        yield
        return
    _check_passwordless_sudo()
    with ExitStack() as restores:
        for path, previous, value in changes:
            _write_sysctl(path, value)
            restores.callback(_restore_sysctl, path, previous)
        yield


def _restore_sysctl(path: Path, previous: str) -> None:
    try:
        _write_sysctl(path, previous)
    except RuntimeError as error:
        LOGGER.error("could not restore %s to %r: %s", path, previous, error)


def _check_passwordless_sudo() -> None:
    try:
        subprocess.run(
            ["sudo", "-n", "true"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError) as error:
        raise RuntimeError("passwordless sudo is required to configure the host") from error


def _write_sysctl(path: Path, value: str) -> None:
    try:
        subprocess.run(
            ["sudo", "tee", str(path)],
            input=f"{value}\n",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"failed to write {path}") from error
