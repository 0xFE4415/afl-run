from __future__ import annotations

from dataclasses import asdict
from json import dumps
from pathlib import Path

import click

from afl_run.config import Config
from afl_run.paths import resolve_paths


@click.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
def main(config_path: str) -> None:
    cfg = Config.model_validate_json(Path(config_path).read_text())
    resolved = resolve_paths(cfg)
    click.echo(dumps(asdict(resolved), indent=2, default=str))
