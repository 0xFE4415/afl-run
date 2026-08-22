from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from helpers import relative_paths
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import Config, EngineConfig, ExecutionConfig, PathConfig
from afl_run.orchestration import (
    build_cmplog_command,
    build_master_command,
    build_worker_specs,
    reset_output_directory,
    wait_for_master,
)


def _config() -> Config:
    return Config(
        paths=PathConfig(
            main="main",
            cmplog="cmplog",
            dictionary="dict",
            seeds_dir="seeds",
            out_dir="out",
        ),
        engine={
            "memory_limit_mb": 1024,
            "memory_limit_cmplog_mb": 2048,
            "skip_deterministic": True,
        },
    )


def test_build_master_command() -> None:
    command = build_master_command(_config(), relative_paths())
    assert command == (
        "afl-fuzz",
        "-i",
        "seeds",
        "-o",
        "out",
        "-G",
        "4096",
        "-m",
        "1024",
        "-t",
        "2500",
        "-x",
        "dict",
        "-z",
        "-M",
        "main",
        "--",
        "main",
    )


def test_build_cmplog_command() -> None:
    command = build_cmplog_command(_config(), relative_paths())
    assert command == (
        "afl-fuzz",
        "-i",
        "seeds",
        "-o",
        "out",
        "-G",
        "4096",
        "-m",
        "2048",
        "-t",
        "2500",
        "-c",
        "cmplog",
        "-x",
        "dict",
        "-z",
        "-S",
        "cmplog",
        "--",
        "main",
    )


def test_build_cmplog_command_requires_harness() -> None:
    paths = relative_paths()
    paths.cmplog = None

    with pytest.raises(ValueError, match="not configured"):
        build_cmplog_command(_config(), paths)


def test_build_worker_specs() -> None:
    config = _config()
    config.execution.n_workers = 2
    config.engine.asan_instances = 2
    paths = relative_paths()
    paths.laf = Path("laf")
    paths.asan_main = Path("asan")

    specs = build_worker_specs(config, paths)

    assert [command[-4:] for _, command in specs] == [
        ("-S", "s1", "--", "main"),
        ("-S", "s2", "--", "main"),
        ("-S", "laf", "--", "laf"),
        ("-S", "asan1", "--", "asan"),
        ("-S", "asan2", "--", "asan"),
    ]


def test_build_worker_specs_pairs_names_with_commands() -> None:
    config = _config()
    config.execution.n_workers = 1
    paths = relative_paths()
    paths.laf = Path("laf")

    specs = build_worker_specs(config, paths)

    assert [name for name, _ in specs] == ["s1", "laf"]
    assert [command[-4:] for _, command in specs] == [
        ("-S", "s1", "--", "main"),
        ("-S", "laf", "--", "laf"),
    ]


@given(
    n_workers=st.integers(min_value=0, max_value=24),
    asan_instances=st.integers(min_value=0, max_value=12),
    with_laf=st.booleans(),
    with_asan=st.booleans(),
)
def test_build_worker_specs_matches_configured_instances(
    n_workers: int,
    asan_instances: int,
    with_laf: bool,
    with_asan: bool,
) -> None:
    laf_path = Path("laf") if with_laf else None
    asan_path = Path("asan") if with_asan else None
    config = Config(
        execution=ExecutionConfig(n_workers=n_workers),
        engine=EngineConfig(asan_instances=asan_instances),
        paths=PathConfig(main="main", seeds_dir="seeds", out_dir="out"),
    )
    paths = relative_paths()
    paths.laf = laf_path
    paths.asan_main = asan_path

    specs = build_worker_specs(config, paths)

    expected_names = [f"s{index}" for index in range(1, n_workers + 1)]
    expected_targets: list[Path] = [paths.main] * n_workers
    if laf_path is not None:
        expected_names.append("laf")
        expected_targets.append(laf_path)
    if asan_path is not None:
        expected_names.extend(f"asan{index}" for index in range(1, asan_instances + 1))
        expected_targets.extend([asan_path] * asan_instances)

    assert [name for name, _ in specs] == expected_names
    assert len(specs) == len(set(expected_names))
    for (name, command), target in zip(specs, expected_targets, strict=True):
        assert command[:5] == ("afl-fuzz", "-i", "seeds", "-o", "out")
        assert command[command.index("-S") + 1] == name
        assert command[-2:] == ("--", str(target))


def test_reset_output_directory_replaces_existing(tmp_path: Path) -> None:
    root = tmp_path / "afl"
    root.mkdir()
    (root / "old").write_text("")

    reset_output_directory(root)

    assert root.is_dir()
    assert not (root / "old").exists()


def test_reset_output_directory_creates_missing(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "afl"

    reset_output_directory(root)

    assert root.is_dir()


@pytest.mark.parametrize("root", [Path("/"), Path.cwd(), Path("."), Path(".."), Path.cwd().parent])
def test_reset_output_directory_refuses_unsafe_roots(root: Path) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        reset_output_directory(root)


def test_reset_output_directory_refuses_non_directory(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.write_text("")

    with pytest.raises(ValueError, match="regular directory"):
        reset_output_directory(root)


class _EventKey:
    def __init__(self, data: str) -> None:
        self.data = data


class _Selector:
    def __init__(self, events: list[str], on_select: Callable[[], None] | None = None) -> None:
        self.events = events
        self.on_select = on_select

    def __enter__(self) -> _Selector:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def register(self, fileobj: object, event: int, data: str) -> None:
        return None

    def select(self) -> list[tuple[_EventKey, object]]:
        if self.on_select is not None:
            self.on_select()
        return [(_EventKey(self.events.pop(0)), object())]


class _Notifier:
    def __init__(self, stats: Path, *, create_on_watch: bool = False) -> None:
        self.stats = stats
        self.create_on_watch = create_on_watch
        self.reads = 0

    def add_watch(self, path: str, mask: object) -> int:
        if self.create_on_watch:
            self.stats.write_text("")
        return 1

    def fileno(self) -> int:
        return 10

    def read(self) -> None:
        self.reads += 1
        if self.reads == 2:
            self.stats.write_text("")

    def close(self) -> None:
        return None


class _Process:
    pid = 123


def test_wait_for_master_waits_until_stats_exists(tmp_path: Path) -> None:
    stats = tmp_path / "main" / "fuzzer_stats"
    notifier = _Notifier(stats)
    selector = _Selector(["filesystem", "filesystem"])

    with (
        patch("afl_run.orchestration.INotify", return_value=notifier),
        patch("afl_run.orchestration.selectors.DefaultSelector", return_value=selector),
        patch("afl_run.orchestration.os.pidfd_open", return_value=42),
        patch("afl_run.orchestration.os.close"),
    ):
        wait_for_master(stats, _Process())

    assert stats.is_file()


def test_wait_for_master_returns_if_stats_already_exists(tmp_path: Path) -> None:
    stats = tmp_path / "fuzzer_stats"
    stats.write_text("")

    with patch("afl_run.orchestration.INotify") as notifier:
        wait_for_master(stats, _Process())

    notifier.assert_not_called()


def test_wait_for_master_rechecks_after_installing_watch(tmp_path: Path) -> None:
    stats = tmp_path / "main" / "fuzzer_stats"
    stats.parent.mkdir()
    notifier = _Notifier(stats, create_on_watch=True)

    with (
        patch("afl_run.orchestration.INotify", return_value=notifier),
        patch("afl_run.orchestration.os.pidfd_open") as pidfd_open,
    ):
        wait_for_master(stats, _Process())

    pidfd_open.assert_not_called()


@pytest.mark.parametrize("stats_exists", [True, False])
def test_wait_for_master_handles_reaped_master(tmp_path: Path, stats_exists: bool) -> None:
    stats = tmp_path / "main" / "fuzzer_stats"
    stats.parent.mkdir()

    def pidfd_open(_: int) -> int:
        if stats_exists:
            stats.write_text("")
        raise ProcessLookupError

    with (
        patch("afl_run.orchestration.INotify"),
        patch("afl_run.orchestration.os.pidfd_open", side_effect=pidfd_open),
    ):
        if stats_exists:
            wait_for_master(stats, _Process())
        else:
            with pytest.raises(RuntimeError, match="master exited before creating"):
                wait_for_master(stats, _Process())


def test_wait_for_master_creates_missing_master_directory(tmp_path: Path) -> None:
    stats = tmp_path / "main" / "fuzzer_stats"
    script = (
        "import sys, time; "
        "time.sleep(0.1); "
        "open(sys.argv[1], 'w').close()"
    )
    process = subprocess.Popen([sys.executable, "-c", script, str(stats)])

    try:
        wait_for_master(stats, process)
    finally:
        process.wait(timeout=5)

    assert stats.is_file()


def test_wait_for_master_raises_if_process_exits(tmp_path: Path) -> None:
    selector = _Selector(["process"])
    with (
        patch("afl_run.orchestration.INotify"),
        patch("afl_run.orchestration.selectors.DefaultSelector", return_value=selector),
        patch("afl_run.orchestration.os.pidfd_open", return_value=42),
        patch("afl_run.orchestration.os.close"),
    ):
        with pytest.raises(RuntimeError):
            wait_for_master(tmp_path / "fuzzer_stats", _Process())


def test_wait_for_master_accepts_stats_created_with_process_event(tmp_path: Path) -> None:
    stats = tmp_path / "main" / "fuzzer_stats"
    stats.parent.mkdir()

    def create_stats() -> None:
        stats.write_text("")

    selector = _Selector(["process"], on_select=create_stats)

    with (
        patch("afl_run.orchestration.INotify"),
        patch("afl_run.orchestration.selectors.DefaultSelector", return_value=selector),
        patch("afl_run.orchestration.os.pidfd_open", return_value=42),
        patch("afl_run.orchestration.os.close"),
    ):
        wait_for_master(stats, _Process())
