# AFL Sample

This directory contains a small CMake-based C++ application for local testing.
The input-processing logic is exposed as `process_input(std::istream&)` in
`lib.cpp`, which is built as an object library. A fuzz harness can call it
directly without depending on the command-line file handling in `main`.

## Build

```sh
cmake -S sample -B sample/build
cmake --build sample/build
```

## Run

```sh
printf 'hello\nworld\n' > sample/input.txt
sample/build/afl_sample sample/input.txt
```
