from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import pytest
from helpers import minimal_path_config
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from afl_run.config import (
    Config,
    EngineConfig,
    EnvConfig,
    ExecutionConfig,
    HostConfig,
    PathConfig,
)


def test_default_config() -> None:
    cfg = Config(paths=minimal_path_config())
    assert cfg.execution.n_workers == 0
    assert cfg.engine.timeout_ms == 2500
    assert cfg.engine.memory_limit_mb is None
    assert cfg.env.variables == {}


def test_config_is_frozen() -> None:
    cfg = Config(paths=minimal_path_config())

    with pytest.raises(ValidationError, match="frozen"):
        setattr(cfg, "execution", ExecutionConfig())
    with pytest.raises(ValidationError, match="frozen"):
        setattr(cfg.execution, "n_workers", 1)


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
        Config(paths=PathConfig(main="main", seeds_dir="campaign/seeds", out_dir="campaign"))


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


def _config_with_protected(field: str, protected: str, out_dir: str) -> Config:
    return Config(
        paths=PathConfig(
            main="main",
            seeds_dir=protected if field == "seeds_dir" else "seeds",
            out_dir=out_dir,
        ),
        execution=ExecutionConfig(log_dir=protected if field == "log_dir" else "logs"),
        engine=EngineConfig(afl_tmpdir=protected if field == "afl_tmpdir" else None),
    )


_path_segments = st.lists(
    st.from_regex(r"[a-z][a-z0-9_-]{0,7}", fullmatch=True),
    min_size=1,
    max_size=4,
)

_path_suffix = st.lists(
    st.from_regex(r"[a-z][a-z0-9_-]{0,7}", fullmatch=True),
    max_size=4,
)


@given(
    field=st.sampled_from(["seeds_dir", "log_dir", "afl_tmpdir"]),
    prefix=_path_segments,
    suffix=_path_suffix,
)
def test_out_dir_overlapping_protected_directory_is_rejected(
    field: str, prefix: list[str], suffix: list[str]
) -> None:
    out_dir = "/".join(prefix)
    protected = "/".join([*prefix, *suffix])

    with pytest.raises(ValidationError, match=field):
        _config_with_protected(field, protected, out_dir)


@given(
    field=st.sampled_from(["seeds_dir", "log_dir", "afl_tmpdir"]),
    out_segments=_path_segments,
    protected_segments=_path_segments,
)
def test_out_dir_disjoint_protected_directories_are_accepted(
    field: str, out_segments: list[str], protected_segments: list[str]
) -> None:
    out_dir = "/".join(["out", *out_segments])
    protected = "/".join(["protected", *protected_segments])

    cfg = _config_with_protected(field, protected, out_dir)

    assert cfg.paths.out_dir == out_dir


@pytest.mark.parametrize(
    "field",
    [
        "timeout_ms",
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


@pytest.mark.parametrize("value", [2, 2.5])
def test_asan_timeout_scale_accepts_int_and_float(value: int | float) -> None:
    config = EngineConfig.model_validate({"asan_timeout_scale": value})

    assert config.asan_timeout_scale == float(value)
    assert isinstance(config.asan_timeout_scale, float)


@pytest.mark.parametrize("value", ["0", "1", "2"])
def test_host_accepts_valid_randomize_va_space(value: Literal["0", "1", "2"]) -> None:
    assert HostConfig(randomize_va_space=value).randomize_va_space == value


@pytest.mark.parametrize("value", ["", "3", "-1", "01", "on", "false"])
def test_host_rejects_invalid_randomize_va_space(value: str) -> None:
    with pytest.raises(ValidationError, match="randomize_va_space"):
        HostConfig(randomize_va_space=cast("Literal['0', '1', '2']", value))


def test_host_rejects_empty_core_pattern() -> None:
    with pytest.raises(ValidationError, match="core_pattern"):
        HostConfig(core_pattern="")


@pytest.mark.parametrize(
    "field",
    [
        "main",
        "seeds_dir",
        "out_dir",
        "cmplog",
        "laf",
        "asan",
        "dictionary",
    ],
)
def test_paths_reject_empty_strings(field: str) -> None:
    values = dict.fromkeys(("main", "seeds_dir", "out_dir"), "x")
    values[field] = ""

    with pytest.raises(ValidationError, match=field):
        PathConfig.model_validate(values)


def test_execution_rejects_empty_log_dir() -> None:
    with pytest.raises(ValidationError, match="log_dir"):
        ExecutionConfig(log_dir="")


def test_engine_rejects_empty_afl_tmpdir() -> None:
    with pytest.raises(ValidationError, match="afl_tmpdir"):
        EngineConfig(afl_tmpdir="")


@pytest.mark.parametrize("flags", [("",), ("ok", "  ")])
def test_engine_rejects_blank_additional_flags(flags: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="additional_flags"):
        EngineConfig(additional_flags=flags)


def test_engine_accepts_flag_roles_and_instance_names() -> None:
    config = EngineConfig.model_validate(
        {
            "flags": {
                "main": ("-p", "explore"),
                "cmplog": ("-L", "0"),
                "w3": ("-p", "rare"),
            }
        }
    )

    assert config.flags == {
        "main": ("-p", "explore"),
        "cmplog": ("-L", "0"),
        "w3": ("-p", "rare"),
    }


@pytest.mark.parametrize(
    "key", ["bogus", "W1", "worker2", "", "main2", "w0", "w01", "asan0", "asan01"]
)
def test_engine_rejects_unknown_flag_target(key: str) -> None:
    with pytest.raises(ValidationError, match="flag"):
        EngineConfig.model_validate({"flags": {key: ("-Z",)}})


@pytest.mark.parametrize("flags", [("-Z", ""), ("  ",)])
def test_engine_rejects_blank_flag_items(flags: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="flags"):
        EngineConfig.model_validate({"flags": {"main": flags}})


def test_config_accepts_flags_for_configured_instances() -> None:
    cfg = Config(
        execution=ExecutionConfig(n_workers=3),
        paths=PathConfig(
            main="main",
            cmplog="cmplog",
            laf="laf",
            asan="asan",
            seeds_dir="seeds",
            out_dir="out",
        ),
        engine=EngineConfig(
            asan_instances=2,
            flags={"w3": ("-p", "rare"), "laf": ("-Z",), "asan2": ("-p", "fast")},
        ),
    )

    assert cfg.engine.flags["w3"] == ("-p", "rare")


@pytest.mark.parametrize("key", ["w3"])
def test_config_rejects_flags_for_unavailable_worker(key: str) -> None:
    with pytest.raises(ValidationError, match="flags"):
        Config(paths=minimal_path_config(), engine=EngineConfig(flags={key: ("-Z",)}))


def test_config_rejects_cmplog_flags_without_harness() -> None:
    with pytest.raises(ValidationError, match="cmplog"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out"),
            engine=EngineConfig(flags={"cmplog": ("-L", "0")}),
        )


def test_config_rejects_laf_flags_without_harness() -> None:
    with pytest.raises(ValidationError, match="laf"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out"),
            engine=EngineConfig(flags={"laf": ("-Z",)}),
        )


def test_config_rejects_asan_flags_without_harness() -> None:
    with pytest.raises(ValidationError, match="asan"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out"),
            engine=EngineConfig(flags={"asan1": ("-p", "fast")}),
        )


def test_config_rejects_asan_flags_out_of_range() -> None:
    with pytest.raises(ValidationError, match="flags"):
        Config(
            paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out", asan="asan"),
            engine=EngineConfig(asan_instances=1, flags={"asan2": ("-p", "fast")}),
        )


@pytest.mark.parametrize("key", ["", "A=B", "A\x00B"])
def test_env_rejects_invalid_variable_names(key: str) -> None:
    with pytest.raises(ValidationError, match="invalid environment variable name"):
        EnvConfig(variables={key: "1"})


def test_env_accepts_regular_variable_names() -> None:
    variables = {"AFL_MAP_SIZE": "262144"}

    assert EnvConfig(variables=variables).variables == variables


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
