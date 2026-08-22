from __future__ import annotations

from helpers import minimal_path_config
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import Config, EnvConfig
from afl_run.environment import build_environment


def test_build_environment_preserves_base_and_applies_config() -> None:
    cfg = Config(
        paths=minimal_path_config(),
        env=EnvConfig(variables={"AFL_MAP_SIZE": "123", "CUSTOM": "yes"}),
    )

    environment = build_environment(cfg, {"PATH": "/bin", "CUSTOM": "no"})

    assert environment["PATH"] == "/bin"
    assert environment["AFL_MAP_SIZE"] == "123"
    assert environment["CUSTOM"] == "yes"
    assert environment["AFL_NO_AUTODICT"] == "1"
    assert environment["AFL_AUTORESUME"] == "1"


def test_build_environment_merges_user_variables_over_defaults() -> None:
    cfg = Config(paths=minimal_path_config(), env=EnvConfig(variables={"CUSTOM": "yes"}))

    environment = build_environment(cfg, {})

    assert environment["CUSTOM"] == "yes"
    assert environment["AFL_MAP_SIZE"] == "262144"
    assert "ASAN_OPTIONS" in environment


def test_build_environment_uses_process_environment() -> None:
    cfg = Config(paths=minimal_path_config(), env=EnvConfig(variables={"CUSTOM": "yes"}))

    environment = build_environment(cfg)

    assert environment["CUSTOM"] == "yes"


@given(
    base=st.dictionaries(st.text(min_size=1, max_size=20), st.text(max_size=20)),
    variables=st.dictionaries(st.text(min_size=1, max_size=20), st.text(max_size=20)),
)
def test_build_environment_merges_arbitrary_mappings(
    base: dict[str, str],
    variables: dict[str, str],
) -> None:
    cfg = Config(paths=minimal_path_config(), env=EnvConfig(variables=variables))

    environment = build_environment(cfg, base)

    assert environment == {**base, **EnvConfig.DEFAULT_VARIABLES, **variables}
