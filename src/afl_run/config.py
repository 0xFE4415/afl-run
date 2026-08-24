from __future__ import annotations

import re
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


_FLAG_ROLE_NAMES = frozenset({"worker", "asan"})
_FLAG_TARGET_PATTERN = re.compile(r"main|cmplog|laf|w[1-9][0-9]*|asan[1-9][0-9]*")


def _reject_blank_flags(items: tuple[str, ...], label: str) -> tuple[str, ...]:
    for item in items:
        if not item.strip():
            raise ValueError(f"{label} must not contain blank items")
    return items


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
    flags: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @field_validator("additional_flags")
    @classmethod
    def reject_blank_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _reject_blank_flags(value, "additional_flags")

    @field_validator("flags")
    @classmethod
    def validate_flags(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        for name, items in value.items():
            if name not in _FLAG_ROLE_NAMES and _FLAG_TARGET_PATTERN.fullmatch(name) is None:
                raise ValueError(f"unknown flag target: {name!r}")
            _reject_blank_flags(items, f"flags[{name!r}]")
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

    @model_validator(mode="after")
    def reject_unknown_flag_targets(self) -> Self:
        n_workers = self.execution.n_workers
        asan_instances = self.engine.asan_instances
        for name in self.engine.flags:
            if name in ("worker", "asan", "main"):
                continue
            if name == "cmplog":
                if self.paths.cmplog is None:
                    raise ValueError(
                        "flags reference cmplog but no CmpLog harness is configured"
                    )
                continue
            if name == "laf":
                if self.paths.laf is None:
                    raise ValueError("flags reference laf but no LAF harness is configured")
                continue
            if name.startswith("w"):
                index = int(name.removeprefix("w"))
                if not 1 <= index <= n_workers:
                    raise ValueError(
                        f"flags reference {name!r} but {n_workers} workers are configured"
                    )
                continue
            index = int(name.removeprefix("asan"))
            if self.paths.asan_main is None:
                raise ValueError(
                    f"flags reference {name!r} but no ASAN harness is configured"
                )
            if not 1 <= index <= asan_instances:
                raise ValueError(
                    f"flags reference {name!r} but {asan_instances} ASAN instances are configured"
                )
        return self
