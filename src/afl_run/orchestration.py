from __future__ import annotations

import os
import selectors
import shutil
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from inotify_simple import INotify, flags

from afl_run.config import Config, EngineConfig
from afl_run.engine import (
    build_asan_args,
    build_cmplog_args,
    build_common_no_cmplog_args,
    build_instance_flags,
)
from afl_run.launcher import ProcessLike
from afl_run.paths import ResolvedPaths

MAIN_NAME = "main"
CMPLOG_NAME = "cmplog"
# Filesystem timestamps can lag time.time() by a few milliseconds; tolerate
# that skew when deciding whether fuzzer_stats was written after launch.
STATS_MTIME_SKEW_SECONDS = 1.0

type Command = tuple[str, ...]
type FuzzerSpec = tuple[str, Command]
type FuzzerSpecs = tuple[FuzzerSpec, ...]


def build_main_command(config: Config, paths: ResolvedPaths) -> Command:
    return _build_instance_command(
        config,
        paths,
        MAIN_NAME,
        paths.main,
        args_builder=build_common_no_cmplog_args,
        instance_flag="-M",
    )


def build_cmplog_command(config: Config, paths: ResolvedPaths) -> Command:
    if not paths.cmplog:
        raise ValueError("CmpLog harness is not configured")
    return _build_instance_command(
        config,
        paths,
        CMPLOG_NAME,
        paths.main,
        args_builder=build_cmplog_args,
    )


def build_worker_specs(
    config: Config,
    paths: ResolvedPaths,
) -> FuzzerSpecs:
    specs: list[FuzzerSpec] = []
    for index in range(1, config.execution.n_workers + 1):
        specs.append(
            (
                f"w{index}",
                _build_instance_command(
                    config,
                    paths,
                    f"w{index}",
                    paths.main,
                    args_builder=build_common_no_cmplog_args,
                    role="worker",
                ),
            )
        )

    if paths.laf:
        specs.append(
            (
                "laf",
                _build_instance_command(
                    config,
                    paths,
                    "laf",
                    paths.laf,
                    args_builder=build_common_no_cmplog_args,
                ),
            )
        )

    if paths.asan:
        for index in range(1, config.engine.asan_instances + 1):
            specs.append(
                (
                    f"asan{index}",
                    _build_instance_command(
                        config,
                        paths,
                        f"asan{index}",
                        paths.asan,
                        args_builder=build_asan_args,
                        role="asan",
                    ),
                )
            )

    return tuple(specs)


def build_campaign_specs(
    config: Config,
    paths: ResolvedPaths,
) -> FuzzerSpecs:
    specs: list[FuzzerSpec] = [(MAIN_NAME, build_main_command(config, paths))]
    if paths.cmplog:
        specs.append((CMPLOG_NAME, build_cmplog_command(config, paths)))
    specs.extend(build_worker_specs(config, paths))
    return tuple(specs)


def _build_instance_command(
    config: Config,
    paths: ResolvedPaths,
    name: str,
    target: Path,
    *,
    args_builder: Callable[[EngineConfig, ResolvedPaths], Command],
    role: str | None = None,
    instance_flag: str = "-S",
) -> Command:
    args = args_builder(config.engine, paths) + build_instance_flags(config.engine, role, name)
    return _build_fuzzer_command(paths, args, instance_flag, name, target)


def _build_fuzzer_command(
    paths: ResolvedPaths,
    args: Command,
    instance_flag: str,
    instance_name: str,
    target: Path,
) -> Command:
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


def reset_output_directory(root: Path) -> None:
    resolved_root = root.resolve()
    unsafe_paths = (Path("/"), Path.home().resolve(), Path.cwd().resolve())
    if any(protected_path.is_relative_to(resolved_root) for protected_path in unsafe_paths):
        raise ValueError(f"refusing to remove unsafe output directory: {root}")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError(f"output directory is not a regular directory: {root}")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def wait_for_main(
    stats_path: Path,
    main: ProcessLike,
    since: float,
) -> None:
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    since -= STATS_MTIME_SKEW_SECONDS
    if _stats_written_since(stats_path, since):
        return

    with ExitStack() as stack:
        notifier = INotify()
        stack.callback(notifier.close)
        notifier.add_watch(
            str(stats_path.parent),
            flags.CREATE | flags.MOVED_TO | flags.CLOSE_WRITE,
        )
        if _stats_written_since(stats_path, since):
            return
        try:
            pidfd = os.pidfd_open(main.pid)
        except ProcessLookupError:
            if _stats_written_since(stats_path, since):
                return
            raise RuntimeError(f"main exited before creating {stats_path}") from None
        stack.callback(os.close, pidfd)
        _wait_for_events(stats_path, notifier, pidfd, since)


def _stats_written_since(stats_path: Path, since: float) -> bool:
    # A fuzzer_stats file left over from a previous run must not count as
    # readiness for a resumed campaign.
    try:
        return stats_path.stat().st_mtime >= since
    except OSError:
        return False


def _wait_for_events(
    stats_path: Path,
    notifier: INotify,
    pidfd: int,
    since: float,
) -> None:
    with selectors.DefaultSelector() as selector:
        selector.register(notifier.fileno(), selectors.EVENT_READ, "filesystem")
        selector.register(pidfd, selectors.EVENT_READ, "process")
        while True:
            for key, _ in selector.select():
                if key.data == "process":
                    if _stats_written_since(stats_path, since):
                        return
                    raise RuntimeError(f"main exited before creating {stats_path}")
                notifier.read()
                if _stats_written_since(stats_path, since):
                    return
