from __future__ import annotations

import os
import selectors
import shutil
from pathlib import Path
from typing import Protocol

from inotify_simple import INotify, flags

from afl_run.config import Config
from afl_run.engine import build_common_args, build_common_no_cmplog_args
from afl_run.paths import ResolvedPaths


class ProcessLike(Protocol):
    @property
    def pid(self) -> int: ...


def build_master_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    return (
        "afl-fuzz",
        "-i",
        str(paths.seeds_dir),
        "-o",
        str(paths.out_dir),
        *build_common_no_cmplog_args(config.engine, paths),
        "-M",
        "main",
        "--",
        str(paths.main),
    )


def build_cmplog_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    return (
        "afl-fuzz",
        "-i",
        str(paths.seeds_dir),
        "-o",
        str(paths.out_dir),
        *build_common_args(config.engine, paths),
        "-S",
        "cmplog",
        "--",
        str(paths.cmplog),
    )


def prepare_shared_memory(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def wait_for_master(
    stats_path: Path,
    master: ProcessLike,
) -> None:
    if stats_path.is_file():
        return

    notifier = INotify()
    notifier.add_watch(
        str(stats_path.parent),
        flags.CREATE | flags.MOVED_TO | flags.CLOSE_WRITE,
    )
    pidfd = os.pidfd_open(master.pid)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(notifier.fileno(), selectors.EVENT_READ, "filesystem")
            selector.register(pidfd, selectors.EVENT_READ, "process")
            while True:
                for key, _ in selector.select():
                    if key.data == "process":
                        raise RuntimeError(f"master exited before creating {stats_path}")
                    notifier.read()
                    if stats_path.is_file():
                        return
    finally:
        os.close(pidfd)
