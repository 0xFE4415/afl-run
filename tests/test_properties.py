from __future__ import annotations

import pytest
from helpers import minimal_path_config
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import Config, EngineConfig, ExecutionConfig


@given(
    n=st.integers(min_value=1, max_value=64),
    fresh=st.booleans(),
    timeout=st.integers(min_value=1, max_value=60000),
)
def test_config_roundtrip(n: int, fresh: bool, timeout: int) -> None:
    cfg = Config(
        paths=minimal_path_config(),
        execution=ExecutionConfig(n_instances=n, fresh=fresh),
        engine=EngineConfig(timeout_ms=timeout),
    )
    restored = Config.model_validate(cfg.model_dump())
    assert restored == cfg


@pytest.mark.parametrize("path", ["/no/such/dir/abc123", "/tmp/does-not-exist-xyz"])
def test_afl_tmpdir_rejects_nonexistent(path: str) -> None:
    with pytest.raises(ValueError):
        EngineConfig(afl_tmpdir=path)
