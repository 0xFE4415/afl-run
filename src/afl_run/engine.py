from __future__ import annotations

from afl_run.config import EngineConfig
from afl_run.paths import ResolvedPaths


def build_cmplog_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_args(
        config,
        paths,
        include_cmplog=True,
        timeout=config.timeout_ms,
        memory_limit=config.memory_limit_cmplog_mb,
    )


def build_common_no_cmplog_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_args(
        config,
        paths,
        include_cmplog=False,
        timeout=config.timeout_ms,
        memory_limit=config.memory_limit_mb,
    )


def build_asan_args(config: EngineConfig, paths: ResolvedPaths) -> tuple[str, ...]:
    timeout = (
        config.timeout_asan_ms
        if config.timeout_asan_ms is not None
        else config.timeout_ms * config.asan_timeout_scale
    )
    return _build_args(
        config,
        paths,
        include_cmplog=False,
        timeout=timeout,
        memory_limit=config.memory_limit_asan_mb,
    )


def _build_args(
    config: EngineConfig,
    paths: ResolvedPaths,
    *,
    include_cmplog: bool,
    timeout: int,
    memory_limit: int | None,
) -> tuple[str, ...]:
    args = (
        "-G",
        str(config.max_input_length),
    )
    if memory_limit is not None:
        args += ("-m", str(memory_limit))
    args += ("-t", str(timeout))
    if include_cmplog:
        args += ("-c", str(paths.cmplog))
    if paths.dictionary is not None:
        args += ("-x", str(paths.dictionary))
    if config.skip_deterministic:
        args += ("-z",)
    args += config.additional_flags
    return args
