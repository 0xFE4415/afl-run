from __future__ import annotations

from pathlib import Path

from afl_run.config import EngineConfig
from afl_run.engine import (
    build_asan_args,
    build_common_args,
    build_common_no_cmplog_args,
)
from afl_run.paths import ResolvedPaths


def _paths() -> ResolvedPaths:
    return ResolvedPaths(
        main=Path("main"),
        cmplog=Path("cmplog"),
        laf=None,
        asan_main=None,
        dictionary=Path("dict"),
        seeds_dir=Path("seeds"),
        out_dir=Path("out"),
        log_dir=Path("logs"),
    )


def test_common_args_include_cmplog_and_z() -> None:
    args = build_common_args(EngineConfig(), _paths())
    assert args == (
        "-G",
        "4096",
        "-m",
        "1024",
        "-t",
        "2500",
        "-c",
        "cmplog",
        "-x",
        "dict",
        "-z",
    )


def test_common_no_cmplog_can_disable_deterministic_skip() -> None:
    config = EngineConfig(skip_deterministic=False)
    assert build_common_no_cmplog_args(config, _paths()) == (
        "-G",
        "4096",
        "-m",
        "1024",
        "-t",
        "2500",
        "-x",
        "dict",
    )


def test_asan_args_use_scaled_timeout() -> None:
    config = EngineConfig(asan_timeout_scale=3)
    assert build_asan_args(config, _paths()) == (
        "-G",
        "4096",
        "-t",
        "7500",
        "-x",
        "dict",
        "-z",
    )


def test_asan_args_use_explicit_timeout() -> None:
    config = EngineConfig(timeout_asan_ms=8000, skip_deterministic=False)
    assert build_asan_args(config, _paths()) == (
        "-G",
        "4096",
        "-t",
        "8000",
        "-x",
        "dict",
    )
