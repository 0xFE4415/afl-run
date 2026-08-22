#include "lib.h"

#include <string>

InputStats process_input(std::istream& input) {
    std::string line;
    std::size_t bytes = 0;
    std::size_t lines = 0;
    while (std::getline(input, line)) {
        bytes += line.size();
        ++lines;
    }
    return {bytes, lines};
}
