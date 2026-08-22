from __future__ import annotations

from pydantic import BaseModel, Field


class ExecutionConfig(BaseModel):
    n_workers: int = Field(default=0, ge=0)
    log_dir: str = "logs"


class PathConfig(BaseModel):
    main: str
    seeds_dir: str
    out_dir: str
    cmplog: str | None = None
    laf: str | None = None
    asan_main: str | None = None
    dictionary: str | None = None


class EngineConfig(BaseModel):
    timeout_ms: int = Field(default=2500, ge=0)
    timeout_asan_ms: int | None = Field(default=None, ge=0)
    memory_limit_mb: int | None = Field(default=None, ge=0)
    memory_limit_cmplog_mb: int | None = Field(default=None, ge=0)
    memory_limit_asan_mb: int | None = Field(default=None, ge=0)
    max_input_length: int = Field(default=4096, ge=0)
    skip_deterministic: bool = True
    asan_instances: int = Field(default=2, ge=0)
    asan_timeout_scale: int = Field(default=2, ge=0)
    afl_tmpdir: str | None = None
    additional_flags: tuple[str, ...] = ()


class EnvConfig(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


class HostConfig(BaseModel):
    randomize_va_space: str = "0"
    core_pattern: str = "core"


class Config(BaseModel):
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)
    host: HostConfig = Field(default_factory=HostConfig)
