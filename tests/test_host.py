from __future__ import annotations

import subprocess
from unittest.mock import call, patch

from afl_run.config import HostConfig
from afl_run.host import CORE_PATTERN, RANDOMIZE_VA_SPACE, configure_host


def test_configure_host() -> None:
    with patch("afl_run.host.subprocess.run") as run:
        configure_host(HostConfig(randomize_va_space="1", core_pattern="core.%p"))

    assert run.call_args_list == [
        call(
            ["sudo", "tee", str(RANDOMIZE_VA_SPACE)],
            input="1\n",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        ),
        call(
            ["sudo", "tee", str(CORE_PATTERN)],
            input="core.%p\n",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        ),
    ]
