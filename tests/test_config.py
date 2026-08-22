from __future__ import annotations

from pathlib import Path

import pytest
from helpers import minimal_path_config
from pydantic import ValidationError

from afl_run.config import Config, EngineConfig, ExecutionConfig, PathConfig


def test_default_config() -> None:
    cfg = Config(paths=minimal_path_config())
    assert cfg.execution.n_workers == 0
    assert cfg.engine.timeout_ms == 2500
    assert cfg.engine.memory_limit_mb is None
    assert cfg.env.variables == {}


def test_afl_tmpdir_none_ok() -> None:
    cfg = EngineConfig(afl_tmpdir=None)
    assert cfg.afl_tmpdir is None


def test_afl_tmpdir_missing_is_accepted_before_path_resolution() -> None:
    assert EngineConfig(afl_tmpdir="/no/such/dir/abc123").afl_tmpdir == "/no/such/dir/abc123"


def test_disjoint_directories_are_accepted() -> None:
    cfg = Config(
        paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out"),
        execution=ExecutionConfig(log_dir="logs"),
        engine=EngineConfig(afl_tmpdir="tmp"),
    )

    assert cfg.paths.out_dir == "out"


def test_out_dir_equal_to_seeds_dir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="seeds_dir"):
        Config(paths=PathConfig(main="main", seeds_dir="out", out_dir="out"))


def test_out_dir_containing_seeds_dir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="seeds_dir"):
        Config(
            paths=PathConfig(main="main", seeds_dir="campaign/seeds", out_dir="campaign")
        )


def test_out_dir_equal_to_log_dir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="log_dir"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="logs"),
            execution=ExecutionConfig(log_dir="logs"),
        )


def test_out_dir_containing_afl_tmpdir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="afl_tmpdir"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="campaign"),
            engine=EngineConfig(afl_tmpdir="campaign/tmp"),
        )


@pytest.mark.parametrize(
    "field",
    [
        "timeout_ms",
        "timeout_asan_ms",
        "memory_limit_mb",
        "memory_limit_cmplog_mb",
        "memory_limit_asan_mb",
        "max_input_length",
        "asan_instances",
        "asan_timeout_scale",
    ],
)
def test_engine_rejects_negative_values(field: str) -> None:
    with pytest.raises(ValueError):
        EngineConfig.model_validate({field: -1})


def test_readme_config_example_matches_model() -> None:
    readme = Path(__file__).parents[1] / "README.md"
    content = readme.read_text(encoding="utf-8")
    _, start, content = content.partition("```json\n")
    example, end, _ = content.partition("\n```")

    assert start
    assert end
    config = Config.model_validate_json(example)
    assert config.execution.n_workers == 4
    assert config.paths.main == "build-afl/afl_harness"
