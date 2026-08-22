from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from afl_run.config import (
    Config,
    EngineConfig,
    EnvConfig,
    ExecutionConfig,
    HostConfig,
    PathConfig,
)
from afl_run.paths import resolve_paths


@pytest.mark.parametrize("path", ["/no/such/dir/abc123", "/tmp/does-not-exist-xyz"])
def test_afl_tmpdir_rejects_nonexistent(path: str, tmp_path: Path) -> None:
    main = tmp_path / "main"
    cmplog = tmp_path / "cmplog"
    dictionary = tmp_path / "dict"
    main.write_text("")
    cmplog.write_text("")
    dictionary.write_text("")
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    cfg = Config(
        paths=PathConfig(
            main=str(main),
            cmplog=str(cmplog),
            dictionary=str(dictionary),
            seeds_dir=str(seeds),
            out_dir=str(tmp_path / "out"),
        ),
        engine=EngineConfig(afl_tmpdir=path),
    )

    with pytest.raises(ValueError):
        resolve_paths(cfg)


@given(
    execution=st.builds(
        ExecutionConfig,
        n_workers=st.integers(min_value=0, max_value=64),
        log_dir=st.sampled_from(["logs", "var/logs"]),
    ),
    paths=st.builds(
        PathConfig,
        main=st.just("main"),
        cmplog=st.none() | st.just("cmplog"),
        laf=st.none() | st.just("laf"),
        asan_main=st.none() | st.just("asan"),
        dictionary=st.none() | st.just("dict"),
        seeds_dir=st.just("seeds"),
        out_dir=st.just("out"),
    ),
    engine=st.builds(
        EngineConfig,
        timeout_ms=st.integers(min_value=0, max_value=100_000),
        timeout_asan_ms=st.none() | st.integers(min_value=0, max_value=100_000),
        memory_limit_mb=st.none() | st.integers(min_value=0, max_value=100_000),
        memory_limit_cmplog_mb=st.none() | st.integers(min_value=0, max_value=100_000),
        memory_limit_asan_mb=st.none() | st.integers(min_value=0, max_value=100_000),
        max_input_length=st.integers(min_value=0, max_value=100_000),
        skip_deterministic=st.booleans(),
        asan_instances=st.integers(min_value=0, max_value=64),
        asan_timeout_scale=st.integers(min_value=0, max_value=64),
        afl_tmpdir=st.none() | st.just("tmpdir"),
        additional_flags=st.lists(
            st.from_regex(r"--?[a-z]{1,6}", fullmatch=True), max_size=3
        ).map(tuple),
    ),
    env=st.builds(
        EnvConfig,
        variables=st.dictionaries(
            st.from_regex(r"[A-Z][A-Z0-9_]{0,9}", fullmatch=True),
            st.from_regex(r"[a-z0-9]{1,8}", fullmatch=True),
            max_size=3,
        ),
    ),
    host=st.builds(
        HostConfig,
        randomize_va_space=st.sampled_from(["0", "1", "2"]),
        core_pattern=st.sampled_from(["core", "core.%p"]),
    ),
)
def test_config_roundtrip(
    execution: ExecutionConfig,
    paths: PathConfig,
    engine: EngineConfig,
    env: EnvConfig,
    host: HostConfig,
) -> None:
    cfg = Config(execution=execution, paths=paths, engine=engine, env=env, host=host)
    restored = Config.model_validate(cfg.model_dump())
    assert restored == cfg
