from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from afl_run.config import Config


class PathValidationError(ValueError):
    pass


@dataclass
class ResolvedPaths:
    main: Path
    cmplog: Path
    laf: Path | None
    asan_main: Path | None
    dictionary: Path
    seeds_dir: Path
    out_dir: Path
    log_dir: Path


def resolve_paths(cfg: Config) -> ResolvedPaths:
    p = cfg.paths
    main = Path(p.main)
    cmplog = Path(p.cmplog)
    dictionary = Path(p.dictionary)
    seeds_dir = Path(p.seeds_dir)
    out_dir = Path(p.out_dir)
    laf = Path(p.laf) if p.laf else None
    asan_main = Path(p.asan_main) if p.asan_main else None
    log_dir = Path(cfg.execution.log_dir)

    resolved = ResolvedPaths(
        main=main,
        cmplog=cmplog,
        laf=laf,
        asan_main=asan_main,
        dictionary=dictionary,
        seeds_dir=seeds_dir,
        out_dir=out_dir,
        log_dir=log_dir,
    )
    _validate(resolved)
    return resolved


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise PathValidationError(f"missing {what}: {path}")


def _require_dir(path: Path, what: str) -> None:
    if not path.is_dir():
        raise PathValidationError(f"missing {what}: {path}")


def _validate(r: ResolvedPaths) -> None:
    _require_file(r.main, "MAIN harness")
    _require_file(r.cmplog, "CMPLOG harness")
    _require_file(r.dictionary, "dictionary")
    _require_dir(r.seeds_dir, "seeds dir")
