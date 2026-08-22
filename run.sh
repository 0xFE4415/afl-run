#!/usr/bin/env bash
# Launch AFL++ against the decompiler harness with cmplog + an x86 dictionary.
# Usage:
#   ./fuzz/run.sh            # resume fuzz/out, single instance (recommended on this VM)
#   ./fuzz/run.sh fresh      # wipe fuzz/out and reseed from fuzz/seeds
#   ./fuzz/run.sh N          # N instances (1 master -M + N-1 workers -S)
#   ./fuzz/run.sh N fresh
#
# Notes for this target (verified):
#   - -z          skip deterministic (AFL++ 4.35c equivalent of old -d): the engine
#                 has heavy coverage non-determinism (pointer-keyed container ordering)
#                 -> deterministic is pure waste.
#   - -t TIMEOUT  decompiler can take 50-500 ms (plain) or 250-5000 ms (ASAN/UBSAN).
#                 Default 2500; override with TIMEOUT= env var (e.g. TIMEOUT=8000 for ASAN).
#   - -c ...      cmplog-instrumented binary; AFL runs it only on a subset of inputs
#                 to harvest CMP/TEST operands (RedQueen). Main loop uses the fast binary.
#   - -x x86.dict instruction tokens to help the mutator build decodable bytes.
#   - Instances: this host exposes 16 cores; for a ~9ms/iter target, fewer
#                 instances with less sync/calibration contention often beat 1/core.
#                 Override count with the first arg (e.g. ./fuzz/run.sh 6).
#   - ASAN workers: ASAN_N (default 2) extra instances run the ASAN harness
#                 (ASAN_MAIN, default build-asan-opt/afl_harness) live with
#                 TIMEOUT_ASAN (default 2x TIMEOUT) so heap-UAF classes
#                 (C3/C14/C15/C16) are caught during mutation, not just replay.
#                 ASAN_N=0 disables. No cmplog for these (no ASAN cmplog build).
#   - ASAN run:   TIMEOUT=8000 SEEDS_DIR=fuzz/seeds MAIN=build-asan-opt/afl_harness \
#                 CMPLOG=build-asan-opt-cmp/afl_harness ./fuzz/run.sh 4 fresh
#
# Stop with:  pkill afl-fuzz
#
# Seed corpus selection:
#   - Default -i is fuzz/seeds (the curated ~60 seeds from gen_seeds.sh).
#   - For a fresh restart from a culled/merged corpus, export SEEDS_DIR, e.g.
#       SEEDS_DIR=fuzz/seeds_fresh ./fuzz/run.sh 12 fresh
#     seeds_fresh = afl-cmin output + prior distinct crashes + a hang sample.
set -euo pipefail

# Track background fuzzers so we can detect one that fails to start.
declare -a FUZZ_PIDS=()
declare -a FUZZ_LOGS=()

# Launch a fuzzer in the background, recording its pid + log for health checks.
launch_fuzzer() {
  local log="$1"; shift
  "$@" >"$log" 2>&1 &
  local pid=$!
  FUZZ_PIDS+=("$pid")
  FUZZ_LOGS+=("$log")
  echo "started pid $pid -> $log"
}

# Abort if any tracked fuzzer has already exited (failed to start): show the
# dead one's log and kill the rest so we don't leave a partial swarm running.
abort_if_any_died() {
  local i
  for i in "${!FUZZ_PIDS[@]}"; do
    if ! kill -0 "${FUZZ_PIDS[$i]}" 2>/dev/null; then
      echo "ERROR: fuzzer pid ${FUZZ_PIDS[$i]} failed to start (log ${FUZZ_LOGS[$i]}):" >&2
      tail -n 40 "${FUZZ_LOGS[$i]}" >&2
      local p
      for p in "${FUZZ_PIDS[@]}"; do kill "$p" 2>/dev/null; done
      exit 1
    fi
  done
}

echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
echo core | sudo tee /proc/sys/kernel/core_pattern
#sudo afl-system-config

N="${N:-1}"; FRESH=0
for a in "$@"; do
  case "$a" in
    ''|*[0-9]*) N="${a:-$N}" ;;
    fresh) FRESH=1 ;;
  esac
done

HERE="$(dirname "$0")"
CPP="$(cd "$HERE/.." && pwd)"

export SLEIGHHOME="${SLEIGHHOME:-/home/user/ghidra}"
export AFL_MAP_SIZE="${AFL_MAP_SIZE:-262144}"
export AFL_NO_AUTODICT=1
export AFL_FORKSRV_INIT_TMOUT=60000
export AFL_AUTORESUME=1
# Shorten calibration + skip re-cal of inputs already flagged variable. The
# engine has intrinsic coverage nondeterminism (pointer-keyed containers) ->
# re-cal never converges; without these, calibration_time grew to 4h on main.
# export AFL_CAL_FAST=1

#export AFL_PRELOAD=/usr/lib64/afl/libdislocator.so
#export AFL_ALIGNED_ALLOC=1
# jemalloc temporarily disabled (stability test): per-thread arenas vary alloc
# addresses across runs, which leaks nondeterminism into the engine's
# pointer-keyed containers. System malloc (sbrk/monotonic) is more deterministic.
#export AFL_PRELOAD=/lib64/libjemalloc.so.2
export ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0:abort_on_error=1:symbolize=0:fast_unwind_on_malloc=1:malloc_context_size=0}"
#export UBSAN_OPTIONS="${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1}"

# Override via env: MAIN=/path/to/harness CMPLOG=/path/to/cmplog_harness
# e.g. MAIN=build-asan-opt/afl_harness CMPLOG=build-asan-opt-cmp/afl_harness ./fuzz/run.sh 12 fresh
MAIN="${MAIN:-$CPP/build-afl/afl_harness}"
CMPLOG="${CMPLOG:-$CPP/build-afl-cmp/afl_harness}"
# FUZZ_SCENARIO env var (unset=decompile, or "jumpload"/"paramid"/"java") selects extra
# engine paths; export it before launching if you want a non-default scenario.
DICT="$HERE/x86.dict"
SEEDS="${SEEDS_DIR:-$HERE/seeds}"
OUT="${OUT_DIR:-$HERE/out}"

for x in "$MAIN" "$CMPLOG"; do
  [ -x "$x" ] || { echo "missing $x (build it first)"; exit 1; }
done
[ -f "$DICT" ] || { echo "missing $DICT"; exit 1; }
[ -d "$SEEDS" ] || { echo "missing $SEEDS"; exit 1; }

if [ "$FRESH" = "1" ]; then echo "wiping $OUT"; rm -rf "$OUT"; fi
#cd "$CPP"

TIMEOUT="${TIMEOUT:-2500}"
# -L 0: MOpt particle-swarm mutation scheduler — known to break long-campaign
# coverage plateaus by cycling mutation strategies instead of fixed havoc.
# -p rare
COMMON=(-G 4096 -m 1024 -t "$TIMEOUT" -c "$CMPLOG" -x "$DICT" -z)
COMMON_NO_CMPLOG=(-G 4096 -m 1024 -t "$TIMEOUT" -x "$DICT" -z)
# ASAN target runs slower: timeout scales 2x with the plain TIMEOUT.
# No cmplog (no ASAN cmplog build).
TIMEOUT_ASAN="${TIMEOUT_ASAN:-$((TIMEOUT * 2))}"
COMMON_ASAN=(-G 4096 -t "$TIMEOUT_ASAN" -x "$DICT" -z)

if [ "$N" -le 1 ]; then
  echo RUNNING:  afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON[@]}" -- "$MAIN"
  exec afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON[@]}" -- "$MAIN"
fi

LOG_DIR="${LOG_DIR:-logs}"
mkdir -p $LOG_DIR
echo "created log dir: ${PWD}/$LOG_DIR"
# multi-instance: 1 master + (N-1) workers, all sharing -o


rm -rf /dev/shm/afl/
mkdir -p /dev/shm/afl/$LOG_DIR
# Multi-instance: master runs without -c (the slow cmplog binary is delegated
# to a dedicated -S cmplog worker below), freeing its wall-time for the
# sync/calibration work that only the -M master does.
echo "RUNNING: afl-fuzz -i $SEEDS -o $OUT ${COMMON_NO_CMPLOG[@]} -M main -- $MAIN # (MAIN)"

mkdir -p /dev/shm/afl/$LOG_DIR/M
launch_fuzzer "$LOG_DIR/main.log" \
  env AFL_TMPDIR=/dev/shm/afl/$LOG_DIR/M afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON_NO_CMPLOG[@]}" -M main -- "$MAIN"

MASTER_PID="${FUZZ_PIDS[-1]}"
echo "started master (main), pid $MASTER_PID"

# Wait until the master (-M main) has created its instance dir + fuzzer_stats.
# Gate on $OUT/main (the actual -M name), NOT $OUT/default — a fresh wipe never
# creates default/ (it only existed in past runs as a leftover first-launch
# instance), so gating on default/ would deadlock and the workers would never spawn.
# Bail out (instead of spinning forever) if the master dies during startup.
while [ ! -f "$OUT/main/fuzzer_stats" ]; do
    abort_if_any_died
    sleep 1
done

# Dedicated cmplog worker: owns RedQueen (-c) CMP/TEST operand harvesting and
# pushes finds into the shared out dir. This is the only instance that runs the
# cmplog-instrumented binary, so its cost no longer taxes the master.
echo "RUNNING: afl-fuzz -i $SEEDS -o ${OUT} ${COMMON[@]} -S cmplog -- $CMPLOG  # (CMPLOG)"
sleep 0.1
mkdir -p /dev/shm/afl/$LOG_DIR/cmp
launch_fuzzer "$LOG_DIR/cmplog.log" \
  env AFL_TMPDIR=/dev/shm/afl/$LOG_DIR/cmp afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON[@]}" -S cmplog -- "$CMPLOG"
echo "started cmplog worker, pid ${FUZZ_PIDS[-1]}"

for i in $(seq 1 $((N-1))); do
  echo "RUNNING: afl-fuzz -i $SEEDS -o ${OUT} ${COMMON_NO_CMPLOG[@]} -S s${i} -- $MAIN # (WORKER)"
  sleep 0.1
  mkdir -p "/dev/shm/afl/$LOG_DIR/s${i}"
  launch_fuzzer "$LOG_DIR/s${i}.log" \
    env AFL_TMPDIR="/dev/shm/afl/$LOG_DIR/s${i}" afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON_NO_CMPLOG[@]}" -S "s$i" -- "$MAIN"
  echo "started worker s$i, pid ${FUZZ_PIDS[-1]}"
done

# LAF-Intel worker: extra worker with finer CMP coverage (auto-derived from MAIN).
LAF="${LAF:-${MAIN//-opt/-laf}}"
if [ -x "$LAF" ]; then
  echo "RUNNING: afl-fuzz -i $SEEDS -o ${OUT} ${COMMON_NO_CMPLOG[@]} -S laf -- $LAF  #(LAF)"
  sleep 0.1
  mkdir -p /dev/shm/afl/$LOG_DIR/laf
  launch_fuzzer "$LOG_DIR/laf.log" \
    env AFL_TMPDIR=/dev/shm/afl/$LOG_DIR/laf afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON_NO_CMPLOG[@]}" -S laf -- "$LAF"
  echo "started laf worker, pid ${FUZZ_PIDS[-1]}"
else
  echo "NOTE: LAF binary not found at $LAF, skipping (set LAF= env to override)"
fi

# ASAN workers: run the ASAN-instrumented harness live so heap-UAF classes
# (C3/C14/C15/C16) are caught during mutation, not only in replay.
ASAN_N="${ASAN_N:-2}"
ASAN_MAIN="${ASAN_MAIN:-$CPP/build-asan-opt/afl_harness}"
if [ "$ASAN_N" -gt 0 ]; then
  if [ -x "$ASAN_MAIN" ]; then
    for j in $(seq 1 $ASAN_N); do
      echo "RUNNING: afl-fuzz -i $SEEDS -o ${OUT} ${COMMON_ASAN[@]} -S asan$j -- $ASAN_MAIN # (ASAN)"
      sleep 0.1
      mkdir -p "/dev/shm/afl/$LOG_DIR/asan$j"
      launch_fuzzer "$LOG_DIR/asan$j.log" \
        env AFL_TMPDIR="/dev/shm/afl/$LOG_DIR/asan$j" \
            ASAN_OPTIONS=abort_on_error=1:detect_leaks=0:symbolize=0:malloc_context_size=0:fast_unwind_on_malloc=1 \
            afl-fuzz -i "$SEEDS" -o "$OUT" "${COMMON_ASAN[@]}" -S "asan$j" -- "$ASAN_MAIN"
      echo "started asan$j worker, pid ${FUZZ_PIDS[-1]}"
    done
  else
    echo "NOTE: ASAN harness not found at $ASAN_MAIN, skipping (set ASAN_MAIN= env to override)"
  fi
fi
echo "Monitor: afl-whatsup $OUT"
echo "Stop: pkill afl-fuzz"

# Give every fuzzer a moment to initialise, then abort if any already died
# (e.g. bad args, missing binary, or a bad input dir) rather than leaving a
# partial swarm running.
sleep 2
abort_if_any_died

wait
