from __future__ import annotations

import json

import click

from afl_run.config import Config


@click.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
def main(config_path: str) -> None:
    with open(config_path) as fh:
        cfg = Config.model_validate(json.load(fh))
    click.echo(cfg.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
