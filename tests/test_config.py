from __future__ import annotations

import pytest

from config import Config, EngineConfig


def test_default_config() -> None:
    cfg = Config()
    assert cfg.execution.n_instances == 1
    assert cfg.engine.timeout_ms == 2500
    assert cfg.env.variables["AFL_MAP_SIZE"] == "262144"


def test_afl_tmpdir_none_ok() -> None:
    cfg = EngineConfig(afl_tmpdir=None)
    assert cfg.afl_tmpdir is None


def test_afl_tmpdir_missing_raises() -> None:
    with pytest.raises(ValueError):
        EngineConfig(afl_tmpdir="/no/such/dir/abc123")
