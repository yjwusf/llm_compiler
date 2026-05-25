#include "Ve1_h1_systolic_array.h"
#include "verilated.h"
#include <iostream>

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_systolic_array top;
  top.clk_i = 0;
  top.cmd_cols_i = 0;
  top.cmd_depth_i = 0;
  top.cmd_input_addr_i = 0;
  top.cmd_output_addr_i = 0;
  top.cmd_rows_i = 0;
  top.cmd_valid_i = 0;
  top.cmd_weight_addr_i = 0;
  top.input_data_i = 0;
  top.input_valid_i = 0;
  top.rst_ni = 0;
  for (int cycle = 0; cycle < 8; ++cycle) {
    top.rst_ni = cycle > 0;
    top.cmd_valid_i = cycle == 1;
    top.input_valid_i = cycle >= 3 && cycle <= 6;
    top.eval();
    top.clk_i = 1;
    top.eval();
    top.clk_i = 0;
    top.eval();
  }
  std::cout << "E1_H1_IMP1_MOCK_RUNTIME module=e1_h1_systolic_array cycles=8\n";
  top.final();
  return 0;
}
