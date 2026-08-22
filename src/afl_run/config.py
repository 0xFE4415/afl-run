from __future__ import annotations

import os

from pydantic import BaseModel, Field, field_validator


class ExecutionConfig(BaseModel):
    n_instances: int = Field(default=1, ge=1)
    fresh: bool = False
    log_dir: str = "logs"


class PathConfig(BaseModel):
    main: str | None = None
    cmplog: str | None = None
    laf: str | None = None
    asan_main: str | None = None
    dictionary: str | None = None
    seeds_dir: str | None = None
    out_dir: str | None = None


class EngineConfig(BaseModel):
    timeout_ms: int = 2500
    timeout_asan_ms: int | None = None
    memory_limit_mb: int = 1024
    globals: int = 4096
    skip_deterministic: bool = True
    asan_instances: int = 2
    asan_timeout_scale: int = 2
    afl_tmpdir: str | None = None

    @field_validator("afl_tmpdir")
    @classmethod
    def _check_tmpdir(cls, v: str | None) -> str | None:
        if v is not None and not os.path.isdir(v):
            raise ValueError(f"afl_tmpdir does not exist: {v}")
        return v


class EnvConfig(BaseModel):
    variables: dict[str, str] = Field(
        default_factory=lambda: {
            "AFL_MAP_SIZE": "262144",
            "AFL_NO_AUTODICT": "1",
            "AFL_FORKSRV_INIT_TMOUT": "60000",
            "AFL_AUTORESUME": "1",
            "ASAN_OPTIONS": (
                "detect_leaks=0:abort_on_error=1:symbolize=0:"
                "fast_unwind_on_malloc=1:malloc_context_size=0"
            ),
        }
    )


class HostConfig(BaseModel):
    randomize_va_space: str = "0"
    core_pattern: str = "core"


class Config(BaseModel):
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)
    host: HostConfig = Field(default_factory=HostConfig)
