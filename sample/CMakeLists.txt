cmake_minimum_required(VERSION 3.16)

project(afl_sample LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(afl_sample_lib OBJECT lib.cpp)

add_executable(afl_sample main.cpp $<TARGET_OBJECTS:afl_sample_lib>)
add_executable(afl_sample_harness harness.cpp $<TARGET_OBJECTS:afl_sample_lib>)

target_compile_options(afl_sample PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)
