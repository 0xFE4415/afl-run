from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from afl_run.cli import main
from afl_run.config import Config, PathConfig
from afl_run.paths import ResolvedPaths, resolve_paths


def _harness(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    path.chmod(0o755)
    return path


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def _dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathConfig(
            main=str(_harness(tmp_path / "build-afl" / "afl_harness")),
            cmplog=str(_harness(tmp_path / "build-afl-cmp" / "afl_harness")),
            laf=str(_harness(tmp_path / "build-afl-laf" / "afl_harness")),
            asan_main=str(_harness(tmp_path / "build-asan" / "afl_harness")),
            dictionary=str(_file(tmp_path / "x86.dict")),
            seeds_dir=str(_dir(tmp_path / "seeds")),
            out_dir=str(_dir(tmp_path / "out")),
        )
    )


def test_resolve_all_provided(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    resolved = resolve_paths(cfg)

    assert isinstance(resolved, ResolvedPaths)
    assert resolved.main == tmp_path / "build-afl" / "afl_harness"
    assert resolved.cmplog == tmp_path / "build-afl-cmp" / "afl_harness"
    assert resolved.laf == tmp_path / "build-afl-laf" / "afl_harness"
    assert resolved.asan_main == tmp_path / "build-asan" / "afl_harness"
    assert resolved.dictionary == tmp_path / "x86.dict"
    assert resolved.seeds_dir == tmp_path / "seeds"
    assert resolved.out_dir == tmp_path / "out"
    assert resolved.log_dir == Path("logs")


def test_optional_laf_asan_absent(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.paths.laf = None
    cfg.paths.asan_main = None
    resolved = resolve_paths(cfg)
    assert resolved.laf is None
    assert resolved.asan_main is None


def test_minimal_paths_leave_cmplog_unconfigured_without_dictionary(tmp_path: Path) -> None:
    main = _harness(tmp_path / "main")
    seeds = _dir(tmp_path / "seeds")
    out = tmp_path / "out"
    cfg = Config(
        paths=PathConfig(main=str(main), seeds_dir=str(seeds), out_dir=str(out))
    )

    resolved = resolve_paths(cfg)

    assert resolved.cmplog is None
    assert resolved.dictionary is None


@pytest.mark.parametrize("field", ["main", "seeds_dir", "out_dir"])
def test_missing_required_path(field: str, tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    values = cfg.paths.model_dump()
    values[field] = None
    with pytest.raises(ValidationError):
        PathConfig.model_validate(values)


def test_main_nonexistent(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.paths.main = str(tmp_path / "nope" / "afl_harness")
    with pytest.raises(ValueError):
        resolve_paths(cfg)


def test_nonexistent_dictionary(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.paths.dictionary = str(tmp_path / "missing.dict")
    with pytest.raises(ValueError):
        resolve_paths(cfg)


def test_nonexistent_seeds(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.paths.seeds_dir = str(tmp_path / "missing_seeds")
    with pytest.raises(ValueError):
        resolve_paths(cfg)


@pytest.mark.parametrize(
    ("field", "message"),
    [("laf", "LAF harness"), ("asan_main", "ASAN harness")],
)
def test_nonexistent_optional_harness(field: str, message: str, tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    setattr(cfg.paths, field, str(tmp_path / "missing-harness"))

    with pytest.raises(ValueError, match=message):
        resolve_paths(cfg)


def test_out_dir_rejected_when_existing_file(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    out_file = _file(tmp_path / "outfile")
    cfg.paths.out_dir = str(out_file)

    with pytest.raises(ValueError, match="out_dir"):
        resolve_paths(cfg)


def test_nonexistent_afl_tmpdir(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.engine.afl_tmpdir = str(tmp_path / "missing-tmp")

    with pytest.raises(ValueError, match="afl_tmpdir"):
        resolve_paths(cfg)


def test_cli_main(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json())

    with patch("afl_run.cli._run_campaign", new=AsyncMock()) as run_campaign:
        result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code == 0
    run_campaign.assert_awaited_once()


def test_cli_reports_missing_path(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.paths.main = str(tmp_path / "missing" / "afl_harness")
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json())

    result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code != 0
    assert "missing MAIN harness" in result.output


def test_cli_reports_out_dir_not_a_directory(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    out_file = _file(tmp_path / "outfile")
    cfg.paths.out_dir = str(out_file)
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json())

    result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code != 0
    assert "out_dir is not a directory" in result.output


def test_cli_reports_overlapping_output_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"paths": {"main": "main", "seeds_dir": "out", "out_dir": "out"}}'
    )

    result = CliRunner().invoke(main, [str(config_path)])

    assert result.exit_code != 0
    assert "must not equal or contain seeds_dir" in result.output
