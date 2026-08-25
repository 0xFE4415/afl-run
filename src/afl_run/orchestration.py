from __future__ import annotations

import os
import selectors
import shutil
from contextlib import ExitStack
from pathlib import Path

from inotify_simple import INotify, flags

from afl_run.config import Config
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


def build_main_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    return _build_fuzzer_command(
        paths,
        build_common_no_cmplog_args(config.engine, paths)
        + build_instance_flags(config.engine, None, MAIN_NAME),
        "-M",
        MAIN_NAME,
        paths.main,
    )


def build_cmplog_command(config: Config, paths: ResolvedPaths) -> tuple[str, ...]:
    if paths.cmplog is None:
        raise ValueError("CmpLog harness is not configured")
    return _build_fuzzer_command(
        paths,
        build_cmplog_args(config.engine, paths)
        + build_instance_flags(config.engine, None, CMPLOG_NAME),
        "-S",
        CMPLOG_NAME,
        paths.main,
    )


def build_worker_specs(
    config: Config,
    paths: ResolvedPaths,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    specs: list[tuple[str, tuple[str, ...]]] = []
    common_args = build_common_no_cmplog_args(config.engine, paths)
    for index in range(1, config.execution.n_workers + 1):
        name = f"w{index}"
        args = common_args + build_instance_flags(config.engine, "worker", name)
        specs.append((name, _build_fuzzer_command(paths, args, "-S", name, paths.main)))
    if paths.laf is not None:
        args = common_args + build_instance_flags(config.engine, None, "laf")
        specs.append(("laf", _build_fuzzer_command(paths, args, "-S", "laf", paths.laf)))
    if paths.asan_main is not None:
        asan_args = build_asan_args(config.engine, paths)
        for index in range(1, config.engine.asan_instances + 1):
            name = f"asan{index}"
            args = asan_args + build_instance_flags(config.engine, "asan", name)
            specs.append(
                (name, _build_fuzzer_command(paths, args, "-S", name, paths.asan_main))
            )
    return tuple(specs)


def build_campaign_specs(
    config: Config,
    paths: ResolvedPaths,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    specs = [(MAIN_NAME, build_main_command(config, paths))]
    if paths.cmplog is not None:
        specs.append((CMPLOG_NAME, build_cmplog_command(config, paths)))
    specs.extend(build_worker_specs(config, paths))
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
