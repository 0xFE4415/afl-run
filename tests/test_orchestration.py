from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from afl_run.config import Config, PathConfig
from afl_run.orchestration import (
    build_cmplog_command,
    build_master_command,
    build_worker_commands,
    prepare_shared_memory,
    wait_for_master,
)
from afl_run.paths import ResolvedPaths


def _config() -> Config:
    return Config(
        paths=PathConfig(
            main="main",
            cmplog="cmplog",
            dictionary="dict",
            seeds_dir="seeds",
            out_dir="out",
        )
    )


def _paths() -> ResolvedPaths:
    return ResolvedPaths(
        main=Path("main"),
        cmplog=Path("cmplog"),
        laf=None,
        asan_main=None,
        dictionary=Path("dict"),
        seeds_dir=Path("seeds"),
        out_dir=Path("out"),
        log_dir=Path("logs"),
    )


def test_build_master_command() -> None:
    command = build_master_command(_config(), _paths())
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
    command = build_cmplog_command(_config(), _paths())
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


def test_build_worker_commands() -> None:
    config = _config()
    config.execution.n_instances = 3
    config.engine.asan_instances = 2
    paths = _paths()
    paths.laf = Path("laf")
    paths.asan_main = Path("asan")

    commands = build_worker_commands(config, paths)

    assert [command[-4:] for command in commands] == [
        ("-S", "s1", "--", "main"),
        ("-S", "s2", "--", "main"),
        ("-S", "laf", "--", "laf"),
        ("-S", "asan1", "--", "asan"),
        ("-S", "asan2", "--", "asan"),
    ]


def test_build_worker_commands_without_optional_workers() -> None:
    commands = build_worker_commands(_config(), _paths())
    assert commands == ()


def test_prepare_shared_memory_replaces_existing(tmp_path: Path) -> None:
    root = tmp_path / "afl"
    root.mkdir()
    (root / "old").write_text("")

    prepare_shared_memory(root)

    assert root.is_dir()
    assert not (root / "old").exists()


def test_prepare_shared_memory_creates_missing(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "afl"

    prepare_shared_memory(root)

    assert root.is_dir()


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
    def __init__(self, stats: Path) -> None:
        self.stats = stats
        self.reads = 0

    def add_watch(self, path: str, mask: object) -> int:
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


def test_wait_for_master_returns_if_stats_already_exists(tmp_path: Path) -> None:
    stats = tmp_path / "fuzzer_stats"
    stats.write_text("")

    with patch("afl_run.orchestration.INotify") as notifier:
        wait_for_master(stats, _Process())

    notifier.assert_not_called()


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
