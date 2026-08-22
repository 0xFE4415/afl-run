from __future__ import annotations

from afl_run.config import Config, EnvConfig
from afl_run.environment import build_environment


def test_build_environment_preserves_base_and_applies_config() -> None:
    cfg = Config(env=EnvConfig(variables={"AFL_MAP_SIZE": "123", "CUSTOM": "yes"}))

    environment = build_environment(cfg, {"PATH": "/bin", "CUSTOM": "no"})

    assert environment == {"PATH": "/bin", "AFL_MAP_SIZE": "123", "CUSTOM": "yes"}


def test_build_environment_uses_process_environment() -> None:
    cfg = Config(env=EnvConfig(variables={"CUSTOM": "yes"}))

    environment = build_environment(cfg)

    assert environment["CUSTOM"] == "yes"
