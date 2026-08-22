from __future__ import annotations

from afl_run.config import EngineConfig
from afl_run.paths import ResolvedPaths


def build_common_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_args(config, paths, include_cmplog=True, timeout=config.timeout_ms)


def build_common_no_cmplog_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_args(config, paths, include_cmplog=False, timeout=config.timeout_ms)


def build_asan_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    timeout = (
        config.timeout_asan_ms
        if config.timeout_asan_ms is not None
        else config.timeout_ms * config.asan_timeout_scale
    )
    args = ["-G", str(config.globals), "-t", str(timeout), "-x", str(paths.dictionary)]
    if config.skip_deterministic:
        args.append("-z")
    return tuple(args)


def _build_args(
    config: EngineConfig,
    paths: ResolvedPaths,
    *,
    include_cmplog: bool,
    timeout: int,
) -> tuple[str, ...]:
    args = [
        "-G",
        str(config.globals),
        "-m",
        str(config.memory_limit_mb),
        "-t",
        str(timeout),
    ]
    if include_cmplog:
        args.extend(["-c", str(paths.cmplog)])
    args.extend(["-x", str(paths.dictionary)])
    if config.skip_deterministic:
        args.append("-z")
    return tuple(args)
