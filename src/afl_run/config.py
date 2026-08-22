from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class ExecutionConfig(BaseModel):
    n_instances: int = Field(default=1, ge=1)
    fresh: bool = False
    log_dir: str = "logs"


class PathConfig(BaseModel):
    main: str
    cmplog: str
    laf: str | None = None
    asan_main: str | None = None
    dictionary: str
    seeds_dir: str
    out_dir: str


class EngineConfig(BaseModel):
    timeout_ms: int = Field(default=2500, ge=0)
    timeout_asan_ms: int | None = Field(default=None, ge=0)
    memory_limit_mb: int | None = Field(default=None, ge=0)
    memory_limit_asan_mb: int | None = Field(default=None, ge=0)
    max_input_length: int = Field(default=4096, ge=0)
    skip_deterministic: bool = True
    asan_instances: int = Field(default=2, ge=0)
    asan_timeout_scale: int = Field(default=2, ge=0)
    afl_tmpdir: str | None = None

    @field_validator("afl_tmpdir")
    @classmethod
    def _check_tmpdir(cls, v: str | None) -> str | None:
        if v is not None and not Path(v).is_dir():
            raise ValueError(f"afl_tmpdir does not exist: {v}")
        return v


class EnvConfig(BaseModel):
    DEFAULT_VARIABLES: ClassVar[dict[str, str]] = {
        "AFL_MAP_SIZE": "262144",
        "AFL_NO_AUTODICT": "1",
        "AFL_FORKSRV_INIT_TMOUT": "60000",
        "AFL_AUTORESUME": "1",
        "ASAN_OPTIONS": (
            "detect_leaks=0:abort_on_error=1:symbolize=0:"
            "fast_unwind_on_malloc=1:malloc_context_size=0"
        ),
    }

    variables: dict[str, str] = Field(
        default_factory=lambda: dict(EnvConfig.DEFAULT_VARIABLES)
    )

    @field_validator("variables")
    @classmethod
    def _merge_defaults(cls, variables: dict[str, str]) -> dict[str, str]:
        return {**cls.DEFAULT_VARIABLES, **variables}


class HostConfig(BaseModel):
    randomize_va_space: str = "0"
    core_pattern: str = "core"


class Config(BaseModel):
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)
    host: HostConfig = Field(default_factory=HostConfig)
