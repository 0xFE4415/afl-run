# afl-fuzz-runner

Python launcher for AFL++ fuzzing campaigns against the decompiler harness,
replacing the original `run.sh`. Configuration is modelled with pydantic and
driven via a Click CLI (with optional JSON config overrides).

## Requirements

- Python >= 3.13
- [uv](https://github.com/astral-sh/uv)
- AFL++ built harnesses (`MAIN`, `CMPLOG`, `asan_main`, `laf`)

## Setup

```sh
uv venv
uv pip install -e ".[all]"
```

## Development

All linters, type checks and tests are orchestrated with nox:

```sh
nox -s check      # ruff + ty (strict) + pytest
nox -s ruff       # ruff only
nox -s ty         # ty (strict) only
nox -s test       # pytest only
```

Or directly via the venv:

```sh
uv run ruff check .
uv run ty check --error all
uv run pytest
```

## Configuration

All settings are described by the pydantic models in `src/afl_run/config.py`
(`Config` and its category models) and supplied at runtime as a JSON file:

```sh
afl-run config.json
```

See `config.json` for the full default layout. Environment variables for the
child `afl-fuzz` processes are kept as key/value pairs under
`env.variables`.

## License

TODO
