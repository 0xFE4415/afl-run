#pragma once

#include <cstddef>
#include <istream>

struct InputStats {
    std::size_t bytes;
    std::size_t lines;
};

InputStats process_input(std::istream& input);
