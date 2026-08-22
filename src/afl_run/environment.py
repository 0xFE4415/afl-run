from __future__ import annotations

import os
from collections.abc import Mapping

from afl_run.config import Config


def build_environment(
    config: Config,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment.update(config.env.variables)
    return environment
