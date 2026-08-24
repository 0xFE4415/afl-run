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
    return _build_args(
        config,
        paths,
        include_cmplog=False,
        timeout=round(config.timeout_ms * config.asan_timeout_scale),
        memory_limit=config.memory_limit_asan_mb,
    )


def build_instance_flags(
    config: EngineConfig,
    role: str | None,
    name: str,
) -> tuple[str, ...]:
    role_flags = config.flags.get(role, ()) if role is not None else ()
    return role_flags + config.flags.get(name, ())


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
