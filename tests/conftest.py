from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def block_process_group_signals_globally() -> Iterator[None]:
    with (
        patch("afl_run.launcher.os.getpgid", side_effect=lambda pid: pid),
        patch("afl_run.launcher.os.killpg"),
    ):
        yield
