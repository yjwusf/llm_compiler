#include "Ve1_h1_stream_sram.h"
#include "verilated.h"
#include <iostream>

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_stream_sram top;
  top.array_ready_i = 0;
  top.clk_i = 0;
  top.rst_ni = 0;
  top.stream_data_i = 0;
  top.stream_error_i = 0;
  top.stream_last_i = 0;
  top.stream_valid_i = 0;
  for (int cycle = 0; cycle < 8; ++cycle) {
    top.rst_ni = cycle > 0;
    top.stream_valid_i = cycle == 1 || cycle == 3;
    top.stream_last_i = cycle == 3;
    top.stream_error_i = 0;
    top.array_ready_i = cycle >= 2;
    top.eval();
    top.clk_i = 1;
    top.eval();
    top.clk_i = 0;
    top.eval();
  }
  std::cout << "E1_H1_IMP1_MOCK_RUNTIME module=e1_h1_stream_sram cycles=8\n";
  top.final();
  return 0;
}
