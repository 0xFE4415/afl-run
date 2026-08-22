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


def resolve_paths(cfg: Config) -> ResolvedPaths:
    path_config = cfg.paths
    main = Path(path_config.main)
    cmplog = Path(path_config.cmplog) if path_config.cmplog else None
    dictionary = Path(path_config.dictionary) if path_config.dictionary else None
    seeds_dir = Path(path_config.seeds_dir)
    out_dir = Path(path_config.out_dir)
    laf = Path(path_config.laf) if path_config.laf else None
    asan_main = Path(path_config.asan_main) if path_config.asan_main else None
    log_dir = Path(cfg.execution.log_dir)
    afl_tmpdir = Path(cfg.engine.afl_tmpdir) if cfg.engine.afl_tmpdir else None

    resolved = ResolvedPaths(
        main=main,
        cmplog=cmplog,
        laf=laf,
        asan_main=asan_main,
        dictionary=dictionary,
        seeds_dir=seeds_dir,
        out_dir=out_dir,
        log_dir=log_dir,
        afl_tmpdir=afl_tmpdir,
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
    if resolved_paths.afl_tmpdir is not None:
        _require_dir(resolved_paths.afl_tmpdir, "afl_tmpdir")
