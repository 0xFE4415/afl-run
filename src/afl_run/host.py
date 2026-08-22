from __future__ import annotations

import subprocess
from pathlib import Path

from afl_run.config import HostConfig

RANDOMIZE_VA_SPACE = Path("/proc/sys/kernel/randomize_va_space")
CORE_PATTERN = Path("/proc/sys/kernel/core_pattern")


def configure_host(config: HostConfig) -> None:
    _write_sysctl(RANDOMIZE_VA_SPACE, config.randomize_va_space)
    _write_sysctl(CORE_PATTERN, config.core_pattern)


def _write_sysctl(path: Path, value: str) -> None:
    if path.read_text().strip() == value:
        return
    subprocess.run(
        ["sudo", "tee", str(path)],
        input=f"{value}\n",
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )
