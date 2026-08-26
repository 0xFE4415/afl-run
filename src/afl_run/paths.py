from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from afl_run.config import Config


class PathValidationError(ValueError):
    pass


@dataclass
class ResolvedPaths:
    main: Path
    cmplog: Path | None
    laf: Path | None
    asan_main: Path | None
    dictionary: Path | None
    seeds_dir: Path
    out_dir: Path
    log_dir: Path
    afl_tmpdir: Path | None = None


def resolve_paths(config: Config) -> ResolvedPaths:
    resolved = ResolvedPaths(
        main=Path(config.paths.main),
        cmplog=Path(config.paths.cmplog) if config.paths.cmplog else None,
        laf=Path(config.paths.laf) if config.paths.laf else None,
        asan_main=Path(config.paths.asan_main) if config.paths.asan_main else None,
        dictionary=Path(config.paths.dictionary) if config.paths.dictionary else None,
        seeds_dir=Path(config.paths.seeds_dir),
        out_dir=Path(config.paths.out_dir),
        log_dir=Path(config.execution.log_dir),
        afl_tmpdir=Path(config.engine.afl_tmpdir) if config.engine.afl_tmpdir else None,
    )
    _validate(resolved)
    return resolved


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise PathValidationError(f"missing {what}: {path}")


def _require_dir(path: Path, what: str) -> None:
    if not path.is_dir():
        raise PathValidationError(f"missing {what}: {path}")


def _validate(resolved_paths: ResolvedPaths) -> None:
    _require_file(resolved_paths.main, "MAIN harness")
    if resolved_paths.cmplog is not None:
        _require_file(resolved_paths.cmplog, "CMPLOG harness")
    if resolved_paths.laf is not None:
        _require_file(resolved_paths.laf, "LAF harness")
    if resolved_paths.asan_main is not None:
        _require_file(resolved_paths.asan_main, "ASAN harness")
    if resolved_paths.dictionary is not None:
        _require_file(resolved_paths.dictionary, "dictionary")
    _require_dir(resolved_paths.seeds_dir, "seeds dir")
    if resolved_paths.out_dir.exists() and not resolved_paths.out_dir.is_dir():
        raise PathValidationError(f"out_dir is not a directory: {resolved_paths.out_dir}")
    if resolved_paths.afl_tmpdir is not None:
        _require_dir(resolved_paths.afl_tmpdir, "afl_tmpdir")
