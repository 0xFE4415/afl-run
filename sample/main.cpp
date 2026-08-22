#include <fstream>
#include <iostream>

#include "lib.h"

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: afl_sample <input-file>\n";
        return 2;
    }

    std::ifstream input(argv[1]);
    if (!input) {
        std::cerr << "unable to open input file\n";
        return 2;
    }

    const InputStats stats = process_input(input);

    std::cout << "lines=" << stats.lines << " bytes=" << stats.bytes << '\n';
    return 0;
}
