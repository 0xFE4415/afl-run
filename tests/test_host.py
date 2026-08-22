from __future__ import annotations

import subprocess
from unittest.mock import call, patch

import pytest

from afl_run.config import HostConfig
from afl_run.host import CORE_PATTERN, RANDOMIZE_VA_SPACE, configure_host


def test_configure_host() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0", "core\n"]),
        patch("afl_run.host.subprocess.run") as run,
    ):
        configure_host(HostConfig(randomize_va_space="1", core_pattern="core.%p"))

    assert run.call_args_list == [
        call(
            ["sudo", "-n", "true"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
        call(
            ["sudo", "tee", str(RANDOMIZE_VA_SPACE)],
            input="1\n",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        ),
        call(
            ["sudo", "tee", str(CORE_PATTERN)],
            input="core.%p\n",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        ),
    ]


def test_configure_host_skips_unchanged_values() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0\n", "core\n"]),
        patch("afl_run.host.subprocess.run") as run,
    ):
        configure_host(HostConfig(randomize_va_space="0", core_pattern="core"))

    run.assert_not_called()


def test_configure_host_aborts_without_passwordless_sudo() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch(
            "afl_run.host.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["sudo", "-n", "true"]),
        ) as run,
    ):
        with pytest.raises(RuntimeError, match="passwordless sudo"):
            configure_host(HostConfig(randomize_va_space="0", core_pattern="core"))

    run.assert_called_once_with(
        ["sudo", "-n", "true"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_configure_host_aborts_when_sudo_is_missing() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=FileNotFoundError("sudo")),
    ):
        with pytest.raises(RuntimeError, match="passwordless sudo"):
            configure_host(HostConfig(randomize_va_space="0", core_pattern="core"))


def test_configure_host_reports_write_failure() -> None:
    def run_effect(command: list[str], **kwargs: object) -> None:
        if command[1] == "-n":
            return None
        raise subprocess.CalledProcessError(1, command)

    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=run_effect),
    ):
        with pytest.raises(RuntimeError, match="failed to write"):
            configure_host(HostConfig(randomize_va_space="0", core_pattern="core"))
