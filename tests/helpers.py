from __future__ import annotations

from pathlib import Path

from afl_run.config import PathConfig
from afl_run.paths import ResolvedPaths


def minimal_path_config() -> PathConfig:
    return PathConfig(
        main="main",
        cmplog="cmplog",
        dictionary="dict",
        seeds_dir="seeds",
        out_dir="out",
    )


def relative_paths() -> ResolvedPaths:
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
