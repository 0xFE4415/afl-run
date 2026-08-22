from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from helpers import relative_paths

from afl_run.config import Config, PathConfig
from afl_run.orchestration import (
    build_cmplog_command,
    build_master_command,
    build_worker_specs,
    reset_output_directory,
    wait_for_master,
)


def _config() -> Config:
    return Config(
        engine={"memory_limit_mb": 1024},
        paths=PathConfig(
            main="main",
            cmplog="cmplog",
            dictionary="dict",
            seeds_dir="seeds",
            out_dir="out",
        )
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
        "1024",
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
        "cmplog",
    )


def test_build_worker_specs() -> None:
    config = _config()
    config.execution.n_instances = 3
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
    config.execution.n_instances = 2
    paths = relative_paths()
    paths.laf = Path("laf")

    specs = build_worker_specs(config, paths)

    assert [name for name, _ in specs] == ["s1", "laf"]
    assert [command[-4:] for _, command in specs] == [
        ("-S", "s1", "--", "main"),
        ("-S", "laf", "--", "laf"),
    ]


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


@pytest.mark.parametrize("root", [Path("/"), Path.cwd()])
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
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> _Selector:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def register(self, fileobj: object, event: int, data: str) -> None:
        return None

    def select(self) -> list[tuple[_EventKey, object]]:
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
    stats.parent.mkdir()
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


def test_wait_for_master_creates_missing_stats_parent(tmp_path: Path) -> None:
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
