# afl-run

Run AFL++ fuzzing campaigns with a JSON configuration file.

> This project is tailored to personal needs. Pull requests and new issues
> are welcome, though.

## Requirements

- Python >= 3.13
- [uv](https://github.com/astral-sh/uv)
- Linux kernel >= 5.3 with AFL++ and built harnesses

## Setup

```sh
uv venv
uv pip install -e ".[all]"
```

## Development

All linters, type checks and tests are orchestrated with nox:

Markdownlint is required for all Markdown files and runs as part of CI.

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

The campaign can be bounded from the command line with a floating-point
timeout in seconds:

```sh
afl-run --timeout 3600.5 config.json
```

Use `--dry-run` to validate the configuration and print the commands without
starting a campaign:

```sh
afl-run --dry-run config.json
```

Use `--fresh` to remove the existing campaign output before starting. The
configuration is rejected when `out_dir` equals or contains the configured
seed, log, or AFL temporary directory.

All paths must be supplied in the JSON configuration; no paths are derived or
rewritten. The configuration layout is shown in the example below. Environment
variables for child `afl-fuzz` processes are key/value pairs under
`env.variables`.

`execution.n_workers` controls the number of standard worker instances. The
master is always started separately. CmpLog, LAF, and ASAN instances are
started only when their corresponding paths are configured. For example, `0`
with no optional paths starts only the master, while `4` starts the master plus
workers `s1`, `s2`, `s3`, and `s4`.

Example:

```json
{
  "execution": {
    "n_workers": 4,
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
    "memory_limit_mb": 1024,
    "memory_limit_cmplog_mb": null,
    "memory_limit_asan_mb": 0,
    "max_input_length": 4096,
    "skip_deterministic": false,
    "asan_instances": 2,
    "asan_timeout_scale": 2,
    "afl_tmpdir": null,
    "additional_flags": []
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

`memory_limit_cmplog_mb` is independent from `memory_limit_mb` and defaults to
unlimited (`null`). This is recommended because CmpLog can map substantially
more memory; set it explicitly if the campaign requires a limit.

Set `engine.skip_deterministic` to `true` to pass AFL++'s `-z` option. It is
disabled by default for compatibility with older AFL++ releases.

### Host Configuration

Before launching AFL++, the runner may set `kernel.randomize_va_space` and
`kernel.core_pattern` using `sudo`. If either value needs changing,
passwordless sudo is required; the runner uses `sudo -n` and aborts before
writing anything when it is unavailable. If both values are already correct,
sudo is not invoked.

These settings affect the whole system and are not restored when the campaign
ends. Consider using AFL++'s supported `afl-system-config` tool to manage the
host configuration instead.

When `--fresh` is not supplied, existing per-fuzzer logs are appended to so
that resumed campaigns retain earlier output. Fresh campaigns truncate logs.
On resume, an existing master `fuzzer_stats` file is treated as readiness for
the master, so a stale file can hide a master startup failure until the normal
fuzzer health check runs.

While a campaign is running, press `Ctrl-C` to stop it gracefully. The runner
prints an `afl-whatsup` monitoring command and `pkill afl-fuzz` as an emergency
fallback after all fuzzer commands start.

### AFL++ Tuning

Campaign-wide AFL++ tuning flags can be supplied as strings in
`engine.additional_flags`. They are appended to every master, CmpLog, and
worker command, for example `"additional_flags": ["-Z"]`. Environment-based
options such as `AFL_FINAL_SYNC=1` and `AFL_TESTCACHE_SIZE` can be supplied
through `env.variables`. Worker-specific tuning remains a future extension.
