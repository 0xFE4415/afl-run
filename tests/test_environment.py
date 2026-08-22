from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import Config, EnvConfig
from afl_run.environment import build_environment


def test_build_environment_preserves_base_and_applies_config() -> None:
    cfg = Config(env=EnvConfig(variables={"AFL_MAP_SIZE": "123", "CUSTOM": "yes"}))

    environment = build_environment(cfg, {"PATH": "/bin", "CUSTOM": "no"})

    assert environment == {"PATH": "/bin", "AFL_MAP_SIZE": "123", "CUSTOM": "yes"}


def test_build_environment_uses_process_environment() -> None:
    cfg = Config(env=EnvConfig(variables={"CUSTOM": "yes"}))

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
    cfg = Config(env=EnvConfig(variables=variables))

    environment = build_environment(cfg, base)

    assert environment == {**base, **variables}
