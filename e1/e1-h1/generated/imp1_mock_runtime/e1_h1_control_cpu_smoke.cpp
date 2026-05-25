#include "Ve1_h1_control_cpu.h"
#include "verilated.h"
#include <iostream>

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_control_cpu top;
  top.array_done_i = 0;
  top.array_error_i = 0;
  top.clk_i = 0;
  top.cmd_ready_i = 0;
  top.rst_ni = 0;
  for (int cycle = 0; cycle < 8; ++cycle) {
    top.rst_ni = cycle > 0;
    top.cmd_ready_i = cycle >= 2;
    top.array_done_i = cycle >= 5;
    top.array_error_i = 0;
    top.eval();
    top.clk_i = 1;
    top.eval();
    top.clk_i = 0;
    top.eval();
  }
  std::cout << "E1_H1_IMP1_MOCK_RUNTIME module=e1_h1_control_cpu cycles=8\n";
  top.final();
  return 0;
}
