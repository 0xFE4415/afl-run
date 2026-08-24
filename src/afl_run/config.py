from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator


class ExecutionConfig(BaseModel):
    n_workers: int = Field(default=0, ge=0)
    log_dir: str = Field(default="logs", min_length=1)


class PathConfig(BaseModel):
    main: str = Field(min_length=1)
    seeds_dir: str = Field(min_length=1)
    out_dir: str = Field(min_length=1)
    cmplog: str | None = Field(default=None, min_length=1)
    laf: str | None = Field(default=None, min_length=1)
    asan_main: str | None = Field(default=None, min_length=1)
    dictionary: str | None = Field(default=None, min_length=1)


class EngineConfig(BaseModel):
    timeout_ms: int = Field(default=2500, ge=0)
    memory_limit_mb: int | None = Field(default=None, ge=0)
    memory_limit_cmplog_mb: int | None = Field(default=None, ge=0)
    memory_limit_asan_mb: int | None = Field(default=None, ge=0)
    max_input_length: int = Field(default=4096, ge=0)
    skip_deterministic: bool = False
    asan_instances: int = Field(default=2, ge=0)
    asan_timeout_scale: float = Field(default=2.0, ge=0)
    afl_tmpdir: str | None = Field(default=None, min_length=1)
    additional_flags: tuple[str, ...] = ()

    @field_validator("additional_flags")
    @classmethod
    def reject_blank_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for flag in value:
            if not flag.strip():
                raise ValueError("additional_flags must not contain blank items")
        return value


class EnvConfig(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_invalid_variable_names(self) -> Self:
        for name in self.variables:
            if not name or "=" in name or "\x00" in name:
                raise ValueError(f"invalid environment variable name: {name!r}")
        return self


class HostConfig(BaseModel):
    randomize_va_space: str = "0"
    core_pattern: str = Field(default="core", min_length=1)

    @field_validator("randomize_va_space")
    @classmethod
    def check_randomize_va_space(cls, value: str) -> str:
        if value not in ("0", "1", "2"):
            raise ValueError(
                f"randomize_va_space must be one of '0', '1', '2', got {value!r}"
            )
        return value


class Config(BaseModel):
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)
    host: HostConfig = Field(default_factory=HostConfig)

    @model_validator(mode="after")
    def reject_output_overlapping_directories(self) -> Self:
        out_dir = Path(self.paths.out_dir).resolve()
        protected = (
            ("seeds_dir", self.paths.seeds_dir),
            ("log_dir", self.execution.log_dir),
            ("afl_tmpdir", self.engine.afl_tmpdir),
        )
        for name, value in protected:
            if value is not None and Path(value).resolve().is_relative_to(out_dir):
                raise ValueError(
                    f"out_dir {self.paths.out_dir!r} must not equal or contain {name} {value!r}"
                )
        return self
