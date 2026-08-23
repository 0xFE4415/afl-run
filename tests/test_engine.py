from __future__ import annotations

from pathlib import Path

from helpers import relative_paths
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import EngineConfig
from afl_run.engine import (
    build_asan_args,
    build_cmplog_args,
    build_common_no_cmplog_args,
)


def test_common_no_cmplog_can_disable_deterministic_skip() -> None:
    config = EngineConfig(skip_deterministic=False)
    assert build_common_no_cmplog_args(config, relative_paths()) == (
        "-G",
        "4096",
        "-t",
        "2500",
        "-x",
        "dict",
    )


def test_deterministic_skip_is_disabled_by_default() -> None:
    assert "-z" not in build_common_no_cmplog_args(EngineConfig(), relative_paths())


def test_cmplog_args_use_independent_memory_limit() -> None:
    config = EngineConfig(
        memory_limit_mb=1024,
        memory_limit_cmplog_mb=2048,
        skip_deterministic=True,
    )
    assert build_cmplog_args(config, relative_paths()) == (
        "-G",
        "4096",
        "-m",
        "2048",
        "-t",
        "2500",
        "-c",
        "cmplog",
        "-x",
        "dict",
        "-z",
    )


def test_cmplog_args_default_to_unlimited() -> None:
    assert "-m" not in build_cmplog_args(EngineConfig(memory_limit_mb=1024), relative_paths())


def test_additional_flags_are_appended_to_engine_args() -> None:
    config = EngineConfig(additional_flags=("-Z", "-p", "explore"))

    assert build_common_no_cmplog_args(config, relative_paths())[-3:] == (
        "-Z",
        "-p",
        "explore",
    )


def test_engine_args_omit_unconfigured_dictionary() -> None:
    paths = relative_paths()
    paths.dictionary = None

    assert "-x" not in build_common_no_cmplog_args(EngineConfig(), paths)


def test_asan_args_use_scaled_timeout() -> None:
    config = EngineConfig(asan_timeout_scale=3, memory_limit_asan_mb=0, skip_deterministic=True)
    assert build_asan_args(config, relative_paths()) == (
        "-G",
        "4096",
        "-m",
        "0",
        "-t",
        "7500",
        "-x",
        "dict",
        "-z",
    )


def test_asan_args_use_scaled_timeout_with_memory_limit() -> None:
    config = EngineConfig(
        memory_limit_asan_mb=512,
        skip_deterministic=False,
    )
    assert build_asan_args(config, relative_paths()) == (
        "-G",
        "4096",
        "-m",
        "512",
        "-t",
        "5000",
        "-x",
        "dict",
    )


@given(
    max_input_length=st.integers(min_value=0, max_value=1_000_000),
    memory=st.integers(min_value=0, max_value=1_000_000),
    timeout=st.integers(min_value=0, max_value=1_000_000),
    skip_deterministic=st.booleans(),
)
def test_common_args_reflect_engine_settings(
    max_input_length: int,
    memory: int,
    timeout: int,
    skip_deterministic: bool,
) -> None:
    config = EngineConfig(
        max_input_length=max_input_length,
        memory_limit_mb=memory,
        timeout_ms=timeout,
        skip_deterministic=skip_deterministic,
    )

    args = build_common_no_cmplog_args(config, relative_paths())

    assert args[:6] == (
        "-G",
        str(max_input_length),
        "-m",
        str(memory),
        "-t",
        str(timeout),
    )
    assert args[6:8] == ("-x", "dict")
    assert ("-z" in args) is skip_deterministic


@given(
    memory=st.none() | st.integers(min_value=0, max_value=1_000_000),
    dictionary=st.none() | st.just(Path("dict")),
    skip_deterministic=st.booleans(),
    additional_flags=st.lists(
        st.from_regex(r"--?[a-z]{1,6}", fullmatch=True), max_size=3
    ).map(tuple),
)
def test_common_args_preserve_optional_settings(
    memory: int | None,
    dictionary: Path | None,
    skip_deterministic: bool,
    additional_flags: tuple[str, ...],
) -> None:
    paths = relative_paths()
    paths.dictionary = dictionary
    config = EngineConfig(
        memory_limit_mb=memory,
        skip_deterministic=skip_deterministic,
        additional_flags=additional_flags,
    )

    args = build_common_no_cmplog_args(config, paths)

    assert args[:2] == ("-G", "4096")
    if memory is not None:
        assert ("-m", str(memory)) in zip(args, args[1:])
    else:
        assert "-m" not in args
    if dictionary is not None:
        assert ("-x", str(dictionary)) in zip(args, args[1:])
    else:
        assert "-x" not in args
    assert ("-z" in args) is skip_deterministic
    if additional_flags:
        assert args[-len(additional_flags) :] == additional_flags


@given(
    timeout=st.integers(min_value=0, max_value=1_000_000),
    scale=st.integers(min_value=0, max_value=100),
)
def test_asan_args_scale_timeout(timeout: int, scale: int) -> None:
    config = EngineConfig(timeout_ms=timeout, asan_timeout_scale=scale)

    args = build_asan_args(config, relative_paths())

    assert args[2:4] == ("-t", str(timeout * scale))


@given(
    timeout=st.integers(min_value=0, max_value=1_000_000),
    scale=st.integers(min_value=0, max_value=100),
)
def test_asan_args_scale_timeout_property(
    timeout: int, scale: int
) -> None:
    config = EngineConfig(
        timeout_ms=timeout,
        asan_timeout_scale=scale,
    )

    args = build_asan_args(config, relative_paths())

    assert args[2:4] == ("-t", str(timeout * scale))
