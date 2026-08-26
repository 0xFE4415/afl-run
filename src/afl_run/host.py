from __future__ import annotations

import subprocess
from pathlib import Path

from afl_run.config import HostConfig

RANDOMIZE_VA_SPACE = Path("/proc/sys/kernel/randomize_va_space")
CORE_PATTERN = Path("/proc/sys/kernel/core_pattern")


def configure_host(config: HostConfig) -> None:
    settings = (
        (RANDOMIZE_VA_SPACE, config.randomize_va_space),
        (CORE_PATTERN, config.core_pattern),
    )
    changes = tuple((path, value) for path, value in settings if path.read_text().strip() != value)
    if not changes:
        return
    _check_passwordless_sudo()
    for path, value in changes:
        _write_sysctl(path, value)


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
