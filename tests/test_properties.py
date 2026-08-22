from __future__ import annotations

from pathlib import Path

import pytest
from helpers import minimal_path_config
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import Config, EngineConfig, ExecutionConfig, PathConfig
from afl_run.paths import resolve_paths


@given(
    n=st.integers(min_value=0, max_value=64),
    timeout=st.integers(min_value=1, max_value=60000),
)
def test_config_roundtrip(n: int, timeout: int) -> None:
    cfg = Config(
        paths=minimal_path_config(),
        execution=ExecutionConfig(n_workers=n),
        engine=EngineConfig(timeout_ms=timeout),
    )
    restored = Config.model_validate(cfg.model_dump())
    assert restored == cfg


@pytest.mark.parametrize("path", ["/no/such/dir/abc123", "/tmp/does-not-exist-xyz"])
def test_afl_tmpdir_rejects_nonexistent(path: str, tmp_path: Path) -> None:
    main = tmp_path / "main"
    cmplog = tmp_path / "cmplog"
    dictionary = tmp_path / "dict"
    main.write_text("")
    cmplog.write_text("")
    dictionary.write_text("")
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    cfg = Config(
        paths=PathConfig(
            main=str(main),
            cmplog=str(cmplog),
            dictionary=str(dictionary),
            seeds_dir=str(seeds),
            out_dir=str(tmp_path / "out"),
        ),
        engine=EngineConfig(afl_tmpdir=path),
    )

    with pytest.raises(ValueError):
        resolve_paths(cfg)
