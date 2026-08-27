from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from afl_run.config import Config, EngineConfig, ExecutionConfig, PathConfig
from afl_run.launcher import FuzzerProcess
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
        asan=None,
        dictionary=Path("dict"),
        seeds_dir=Path("seeds"),
        out_dir=Path("out"),
        log_dir=Path("logs"),
    )


def make_config(
    *,
    execution: ExecutionConfig | None = None,
    engine: EngineConfig | None = None,
    paths: PathConfig | None = None,
) -> Config:
    return Config(
        paths=paths
        or PathConfig(
            main="main",
            cmplog="cmplog",
            dictionary="dict",
            seeds_dir="seeds",
            out_dir="out",
        ),
        execution=execution or ExecutionConfig(),
        engine=engine
        or EngineConfig(
            memory_limit_mb=1024,
            memory_limit_cmplog_mb=2048,
            skip_deterministic=True,
        ),
    )


def harness(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    path.chmod(0o755)
    return path


def tmp_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def tmp_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def tmp_executable(path: Path) -> Path:
    path.write_text("")
    path.chmod(0o755)
    return path


def valid_path_config(
    tmp_path: Path,
    *,
    cmplog: bool = True,
    laf: bool = True,
    asan: bool = True,
    dictionary: bool = True,
) -> PathConfig:
    return PathConfig(
        main=str(harness(tmp_path / "build-afl" / "afl_harness")),
        cmplog=(str(harness(tmp_path / "build-afl-cmp" / "afl_harness")) if cmplog else None),
        laf=str(harness(tmp_path / "build-afl-laf" / "afl_harness")) if laf else None,
        asan=str(harness(tmp_path / "build-asan" / "afl_harness")) if asan else None,
        dictionary=str(tmp_file(tmp_path / "x86.dict")) if dictionary else None,
        seeds_dir=str(tmp_dir(tmp_path / "seeds")),
        out_dir=str(tmp_dir(tmp_path / "out")),
    )


def replace_config_fields(cfg: Config, **updates: Any) -> Config:
    merged: dict[str, Any] = cfg.model_dump()
    for section, values in updates.items():
        if section in merged and isinstance(merged[section], dict):
            merged[section] = {**merged[section], **values}
        else:
            merged[section] = values
    return Config.model_validate(merged)


def mock_process(returncode: int | None = None, pid: int = 1) -> MagicMock:
    process = MagicMock(pid=pid, returncode=returncode)
    process.wait = AsyncMock(return_value=returncode or 0)
    return process


def mock_fuzzer(name: str, *, returncode: int | None = None, pid: int = 1) -> FuzzerProcess:
    return FuzzerProcess(
        name,
        mock_process(returncode=returncode, pid=pid),
        Path(f"{name}.log"),
        MagicMock(),
    )
