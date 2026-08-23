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


def test_build_environment_uses_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("FROM_PROCESS", "yes")
    cfg = Config(paths=minimal_path_config(), env=EnvConfig(variables={"CUSTOM": "yes"}))

    environment = build_environment(cfg)

    assert environment["FROM_PROCESS"] == "yes"
    assert environment["CUSTOM"] == "yes"


_valid_names = st.text(
    st.characters(exclude_characters="=\x00", exclude_categories=("Cs",)),
    min_size=1,
    max_size=20,
)


@given(
    base=st.dictionaries(_valid_names, st.text(max_size=20)),
    variables=st.dictionaries(_valid_names, st.text(max_size=20)),
)
def test_build_environment_merges_arbitrary_mappings(
    base: dict[str, str],
    variables: dict[str, str],
) -> None:
    cfg = Config(paths=minimal_path_config(), env=EnvConfig(variables=variables))

    environment = build_environment(cfg, base)

    assert environment == {**base, **variables}
