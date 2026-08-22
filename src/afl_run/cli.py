from __future__ import annotations

from pathlib import Path

import click

from afl_run.config import Config


@click.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
def main(config_path: str) -> None:
    cfg = Config.model_validate_json(Path(config_path).read_text())
    click.echo(cfg.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
