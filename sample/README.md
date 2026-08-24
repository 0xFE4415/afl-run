# AFL Sample

This directory contains a small CMake-based C++ application and an AFL++
stdin harness. The processing logic is exposed as
`process_input(std::istream&)` in `lib.cpp`, which is built as an object
library and reused by both executables.

Run the commands below from the repository root.

## 1. Build the regular application

This build is useful for manually checking the file-based application:

```sh
cmake -S sample -B sample/build
cmake --build sample/build
printf 'hello\nworld\n' > sample/input.txt
sample/build/afl_sample sample/input.txt
```

Expected output:

```text
lines=2 bytes=10
```

## 2. Build the instrumented harness

AFL++ requires compile-time instrumentation. Use `afl-c++` instead of the
system compiler:

```sh
cmake -S sample -B sample/build-afl -DCMAKE_CXX_COMPILER=afl-c++
cmake --build sample/build-afl
```

This produces `sample/build-afl/afl_sample_harness`. The harness reads stdin
and passes it to `process_input`.

## 3. Create seed input

```sh
mkdir -p sample/seeds
printf 'seed\n' > sample/seeds/initial.txt
```

## 4. Run AFL++ directly

Use a temporary output directory for a short smoke test:

```sh
timeout --signal=SIGTERM --kill-after=1s 0.3s \
  afl-fuzz \
  -i sample/seeds \
  -o sample/fuzz/direct \
  -- sample/build-afl/afl_sample_harness
```

If AFL++ reports that all CPU cores are occupied, disable CPU affinity for
the smoke test:

```sh
AFL_NO_AFFINITY=1 timeout --signal=SIGTERM --kill-after=1s 0.3s \
  afl-fuzz \
  -i sample/seeds \
  -o sample/fuzz/direct \
  -- sample/build-afl/afl_sample_harness
```

## 5. Run through afl-run

`config.json` is the minimal runner configuration. It only needs the main
harness, seed directory, and output directory under `paths`; CmpLog is not
started unless a dedicated `cmplog` harness is configured, and no dictionary
is required.

Run a 0.3-second campaign through the Python CLI:

```sh
AFL_NO_AFFINITY=1 uv run afl-run --fresh --timeout 0.3 sample/config.json
```

The runner starts the configured AFL++ processes, logs their commands, and
terminates the campaign and child processes when the timeout expires.
Generated build, log, and campaign-output directories are ignored by Git.

## 6. Build advanced AFL++ variants

The advanced configuration uses separately compiled binaries so each AFL++
mode is active. Build the regular harness, then build dedicated CmpLog, LAF,
and ASAN variants:

```sh
cmake -S sample -B sample/build-afl -DCMAKE_CXX_COMPILER=afl-c++
cmake --build sample/build-afl

AFL_LLVM_CMPLOG=1 cmake -S sample -B sample/build-cmplog -DCMAKE_CXX_COMPILER=afl-c++
AFL_LLVM_CMPLOG=1 cmake --build sample/build-cmplog

AFL_LLVM_LAF_ALL=1 cmake -S sample -B sample/build-laf -DCMAKE_CXX_COMPILER=afl-c++
AFL_LLVM_LAF_ALL=1 cmake --build sample/build-laf

AFL_USE_ASAN=1 cmake -S sample -B sample/build-asan -DCMAKE_CXX_COMPILER=afl-c++
AFL_USE_ASAN=1 cmake --build sample/build-asan
```

`advanced-config.json` starts one main instance, one CmpLog instance, one LAF
instance, one ASAN instance, and two standard workers (`w1` and `w2`).

## 7. Run an advanced campaign

```sh
AFL_NO_AFFINITY=1 uv run afl-run --fresh --timeout 3600 sample/advanced-config.json
```
