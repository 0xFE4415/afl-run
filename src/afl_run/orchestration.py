from __future__ import annotations

import os
import selectors
import shutil
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol

from inotify_simple import INotify, flags

from afl_run.config import Config
from afl_run.engine import build_asan_args, build_common_args, build_common_no_cmplog_args
from afl_run.paths import ResolvedPaths


class ProcessLike(Protocol):
    @property
    def pid(self) -> int: ...


def build_master_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_fuzzer_command(
        paths,
        build_common_no_cmplog_args(config.engine, paths),
        "-M",
        "main",
        paths.main,
    )


def build_cmplog_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_fuzzer_command(
        paths,
        build_common_args(config.engine, paths),
        "-S",
        "cmplog",
        paths.cmplog,
    )


def build_worker_commands(
    config: Config,
    paths: ResolvedPaths,
) -> tuple[tuple[str, ...], ...]:
    return tuple(command for _, command in build_worker_specs(config, paths))


def build_worker_specs(
    config: Config,
    paths: ResolvedPaths,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    specs: list[tuple[str, tuple[str, ...]]] = []
    common_args = build_common_no_cmplog_args(config.engine, paths)
    for index in range(1, config.execution.n_instances):
        specs.append(
            (
                f"s{index}",
                _build_fuzzer_command(paths, common_args, "-S", f"s{index}", paths.main),
            )
        )
    if paths.laf is not None:
        specs.append(
            ("laf", _build_fuzzer_command(paths, common_args, "-S", "laf", paths.laf))
        )
    if paths.asan_main is not None:
        asan_args = build_asan_args(config.engine, paths)
        for index in range(1, config.engine.asan_instances + 1):
            specs.append(
                (
                    f"asan{index}",
                    _build_fuzzer_command(
                        paths,
                        asan_args,
                        "-S",
                        f"asan{index}",
                        paths.asan_main,
                    ),
                )
            )
    return tuple(specs)


def _build_fuzzer_command(
    paths: ResolvedPaths,
    args: tuple[str, ...],
    instance_flag: str,
    instance_name: str,
    target: Path,
) -> tuple[str, ...]:
    return (
        "afl-fuzz",
        "-i",
        str(paths.seeds_dir),
        "-o",
        str(paths.out_dir),
        *args,
        instance_flag,
        instance_name,
        "--",
        str(target),
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

    with ExitStack() as stack:
        notifier = INotify()
        stack.callback(notifier.close)
        notifier.add_watch(
            str(stats_path.parent),
            flags.CREATE | flags.MOVED_TO | flags.CLOSE_WRITE,
        )
        if stats_path.is_file():
            return
        pidfd = os.pidfd_open(master.pid)
        stack.callback(os.close, pidfd)
        _wait_for_events(stats_path, notifier, pidfd)


def _wait_for_events(stats_path: Path, notifier: INotify, pidfd: int) -> None:
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
