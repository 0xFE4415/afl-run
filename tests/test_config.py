from __future__ import annotations

from pathlib import Path

import pytest

from afl_run.config import Config, EngineConfig


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


def test_readme_config_example_matches_model() -> None:
    readme = Path(__file__).parents[1] / "README.md"
    content = readme.read_text(encoding="utf-8")
    _, start, content = content.partition("```json\n")
    example, end, _ = content.partition("\n```")

    assert start
    assert end
    config = Config.model_validate_json(example)
    assert config.execution.n_instances == 4
    assert config.paths.main == "build-afl/afl_harness"
