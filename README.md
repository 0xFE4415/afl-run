# afl-fuzz-runner

Python launcher for AFL++ fuzzing campaigns. Configuration is modelled with
pydantic and supplied as JSON through a Click CLI.

## Requirements

- Python >= 3.13
- [uv](https://github.com/astral-sh/uv)
- Linux with AFL++ and built harnesses

## Setup

```sh
uv venv
uv pip install -e ".[all]"
```

## Development

All linters, type checks and tests are orchestrated with nox:

```sh
nox -s check      # ruff + ty (strict) + pytest + 100% branch coverage
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

All paths must be supplied in the JSON configuration; no paths are derived or
rewritten. See `config.json` for the configuration layout. Environment
variables for child `afl-fuzz` processes are key/value pairs under
`env.variables`.

Example:

```json
{
  "execution": {
    "n_instances": 4,
    "fresh": false,
    "log_dir": "logs"
  },
  "paths": {
    "main": "build-afl/afl_harness",
    "cmplog": "build-afl-cmp/afl_harness",
    "laf": "build-afl-laf/afl_harness",
    "asan_main": "build-asan/afl_harness",
    "dictionary": "x86.dict",
    "seeds_dir": "seeds",
    "out_dir": "out"
  },
  "engine": {
    "timeout_ms": 2500,
    "timeout_asan_ms": null,
    "memory_limit_mb": 1024,
    "max_input_length": 4096,
    "skip_deterministic": true,
    "asan_instances": 2,
    "asan_timeout_scale": 2,
    "afl_tmpdir": null
  },
  "env": {
    "variables": {
      "SLEIGHHOME": "ghidra",
      "AFL_MAP_SIZE": "262144"
    }
  },
  "host": {
    "randomize_va_space": "0",
    "core_pattern": "core"
  }
}
```
