from __future__ import annotations

import logging
import subprocess
from unittest.mock import call, patch

import pytest

from afl_run.config import HostConfig
from afl_run.host import CORE_PATTERN, RANDOMIZE_VA_SPACE, configured_host


def _tee(path: object, value: str) -> object:
    return call(
        ["sudo", "tee", str(path)],
        input=f"{value}\n",
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )


_SUDO_CHECK = call(
    ["sudo", "-n", "true"],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)


def test_configured_host_applies_then_restores_previous_values() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0", "core\n"]),
        patch("afl_run.host.subprocess.run") as run,
    ):
        with configured_host(HostConfig(randomize_va_space="1", core_pattern="core.%p")):
            assert run.call_args_list == [
                _SUDO_CHECK,
                _tee(RANDOMIZE_VA_SPACE, "1"),
                _tee(CORE_PATTERN, "core.%p"),
            ]

    assert run.call_args_list[3:] == [
        _tee(CORE_PATTERN, "core"),
        _tee(RANDOMIZE_VA_SPACE, "0"),
    ]


def test_configured_host_restores_when_body_raises() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0\n", "core\n"]),
        patch("afl_run.host.subprocess.run") as run,
        pytest.raises(KeyError),
    ):
        with configured_host(HostConfig(randomize_va_space="2", core_pattern="core")):
            raise KeyError

    assert run.call_args_list == [
        _SUDO_CHECK,
        _tee(RANDOMIZE_VA_SPACE, "2"),
        _tee(RANDOMIZE_VA_SPACE, "0"),
    ]


def test_configured_host_skips_unchanged_values() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0\n", "core\n"]),
        patch("afl_run.host.subprocess.run") as run,
    ):
        with configured_host(HostConfig(randomize_va_space="0", core_pattern="core")):
            pass

    run.assert_not_called()


def test_configured_host_aborts_without_passwordless_sudo() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch(
            "afl_run.host.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["sudo", "-n", "true"]),
        ) as run,
    ):
        with pytest.raises(RuntimeError, match="passwordless sudo"):
            with configured_host(HostConfig(randomize_va_space="0", core_pattern="core")):
                pass

    run.assert_called_once_with(
        ["sudo", "-n", "true"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_configured_host_aborts_when_sudo_is_missing() -> None:
    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=FileNotFoundError("sudo")),
    ):
        with pytest.raises(RuntimeError, match="passwordless sudo"):
            with configured_host(HostConfig(randomize_va_space="0", core_pattern="core")):
                pass


def test_configured_host_reports_write_failure() -> None:
    def run_effect(command: list[str], **kwargs: object) -> None:
        if command[1] == "-n":
            return None
        raise subprocess.CalledProcessError(1, command)

    with (
        patch("afl_run.host.Path.read_text", side_effect=["1\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=run_effect),
    ):
        with pytest.raises(RuntimeError, match="failed to write"):
            with configured_host(HostConfig(randomize_va_space="0", core_pattern="core")):
                pass


def test_configured_host_restores_first_setting_when_second_write_fails() -> None:
    written: list[str] = []

    def run_effect(command: list[str], **kwargs: object) -> None:
        if command[1] == "-n":
            return None
        if str(CORE_PATTERN) in command and kwargs["input"] == "core.%p\n":
            raise subprocess.CalledProcessError(1, command)
        written.append(f"{command[2]}={kwargs['input']!s}".strip())

    with (
        patch("afl_run.host.Path.read_text", side_effect=["0\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=run_effect),
    ):
        with pytest.raises(RuntimeError, match="failed to write"):
            with configured_host(HostConfig(randomize_va_space="1", core_pattern="core.%p")):
                pass

    assert written == [f"{RANDOMIZE_VA_SPACE}=1", f"{RANDOMIZE_VA_SPACE}=0"]


def test_configured_host_logs_and_continues_when_restore_fails(caplog) -> None:
    def run_effect(command: list[str], **kwargs: object) -> None:
        if command[1] == "-n":
            return None
        if kwargs["input"] in ("0\n", "core\n"):
            raise subprocess.CalledProcessError(1, command)

    caplog.set_level(logging.ERROR, logger="afl_run.host")
    with (
        patch("afl_run.host.Path.read_text", side_effect=["0\n", "core\n"]),
        patch("afl_run.host.subprocess.run", side_effect=run_effect),
    ):
        with configured_host(HostConfig(randomize_va_space="1", core_pattern="core.%p")):
            pass

    assert f"could not restore {RANDOMIZE_VA_SPACE}" in caplog.text
    assert f"could not restore {CORE_PATTERN}" in caplog.text
