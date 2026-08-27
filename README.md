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

Formatting, linting, type checks and tests are orchestrated with nox. The
formatter, linter and type checker all cover both `src` and `tests`:

Markdownlint is required for all Markdown files and runs as part of CI.

```sh
uv run nox -s check       # format + ruff + ty (strict) + pytest + 100% branch coverage
uv run nox -s format      # ruff formatter check for src and tests
uv run nox -s ruff        # ruff linter for src and tests
uv run nox -s ty          # ty (strict) type check for src and tests
uv run nox -s test        # pytest only
```

Or directly via the venv:

```sh
uv run ruff format --check src tests
uv run ruff check src tests
uv run ty check --error all src tests
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
starting a campaign. It follows the same campaign setup path without launching
processes:

```sh
afl-run --dry-run config.json
```

Use `--fresh` to remove the existing campaign output before starting. The
configuration is rejected when `out_dir` equals or contains the configured
seed, log, or AFL temporary directory.

Use `--no-sleep` to disable the `Ctrl-Z` sleep behaviour described below.

All paths must be supplied in the JSON configuration; no paths are derived or
rewritten. The configuration layout is shown in the example below. Environment
variables for child `afl-fuzz` processes are key/value pairs under
`env.variables`.

`execution.n_workers` controls the number of standard worker instances. The
main instance is always started separately. CmpLog, LAF, and ASAN instances
are started only when their corresponding paths are configured. For example,
`0` with no optional paths starts only the main instance, while `4` starts the
main instance plus workers `w1`, `w2`, `w3`, and `w4`.

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
    "asan": "build-asan/afl_harness",
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
    "asan_timeout_scale": 2.0,
    "afl_tmpdir": null,
    "additional_flags": [],
    "flags": {
      "main": ["-p", "explore"],
      "cmplog": ["-L", "0"],
      "worker": ["-p", "rare"]
    }
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
On resume, a leftover main `fuzzer_stats` file from the previous run is
ignored: readiness requires the file to be rewritten after launch. While
waiting, the runner monitors the main instance and reports slow startup
(CPU time, RSS, log size) every 5 minutes; it aborts if the instance makes
no progress for 5 minutes (for example, wedged on a kernel resource).

While a campaign is running, press `Ctrl-C` to stop it gracefully. The runner
prints an `afl-whatsup` monitoring command and `pkill afl-fuzz` as an emergency
fallback after all fuzzer commands start.

Press `Ctrl-Z` to put every fuzzer to sleep (sent `SIGSTOP`) without interrupting
the runner: the campaign stays alive and resumes monitoring as soon as a fuzzer is
paused, so it is not reported as crashed. Press `Ctrl-Z` again to wake them
(`SIGCONT`) and continue fuzzing. This is enabled by default; pass `--no-sleep`
to disable it. Each fuzzer is started in its own session so the terminal
`Ctrl-Z` is handled only by the runner, never delivered directly to the fuzzers.

### AFL++ Tuning

Campaign-wide AFL++ tuning flags can be supplied as strings in
`engine.additional_flags`. They are appended to every main, CmpLog, and
worker command, for example `"additional_flags": ["-Z"]`. Environment-based
options such as `AFL_FINAL_SYNC=1` and `AFL_TESTCACHE_SIZE` can be supplied
through `env.variables`.

Per-fuzzer flags are supplied as lists of strings in `engine.flags`, keyed by
either a role or a concrete fuzzer name. Roles apply to every instance of that
kind: `worker` (all `w1`..`wn` workers) and `asan` (all `asan1`..`asanN`
instances). Concrete names (`main`, `cmplog`, `laf`, `w1`, `asan2`, ...) target
a single fuzzer. The categories are independent: for example `worker` flags do
not apply to the `laf`, `main`, or `cmplog` fuzzer or to ASAN instances. Flags
are appended after `additional_flags`, with role flags before instance flags,
so the final order is common, role, instance. Referencing a fuzzer that is not
configured (for example `w3` when `n_workers` is 2, or `cmplog` without a
CmpLog harness) is rejected.
