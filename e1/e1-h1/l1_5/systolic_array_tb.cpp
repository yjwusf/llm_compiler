#include "Ve1_h1_systolic_array.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void tick(Ve1_h1_systolic_array& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  dut.eval();
  dut.clk_i = 1;
  dut.eval();
  ++cycles;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_systolic_array dut;
  std::uint64_t cycles = 0;
  std::uint64_t array_commands = 0;
  std::uint64_t input_transfers = 0;
  std::uint64_t output_transfers = 0;
  std::uint64_t stall_cycles = 0;
  std::uint64_t error_events = 0;

  dut.rst_ni = 0;
  dut.cmd_valid_i = 0;
  dut.cmd_input_addr_i = 0;
  dut.cmd_weight_addr_i = 0;
  dut.cmd_output_addr_i = 0;
  dut.cmd_rows_i = 0;
  dut.cmd_cols_i = 0;
  dut.cmd_depth_i = 0;
  dut.input_valid_i = 0;
  dut.input_data_i = 0;
  tick(dut, cycles);

  dut.rst_ni = 1;
  dut.cmd_valid_i = 1;
  dut.cmd_input_addr_i = 0x10000;
  dut.cmd_weight_addr_i = 0x40000;
  dut.cmd_output_addr_i = 0x80000;
  dut.cmd_rows_i = 16;
  dut.cmd_cols_i = 16;
  dut.cmd_depth_i = 16;
  assert(dut.cmd_ready_o == 1);
  tick(dut, cycles);
  ++array_commands;

  dut.cmd_valid_i = 0;
  for (int i = 0; i < 5; ++i) {
    dut.input_valid_i = 1;
    dut.input_data_i = static_cast<std::uint64_t>(i + 1);
    dut.eval();
    if (dut.input_ready_o) {
      ++input_transfers;
    } else {
      ++stall_cycles;
    }
    if (dut.done_o) {
      ++output_transfers;
    }
    tick(dut, cycles);
  }

  assert(cycles > 0);
  assert(array_commands == 1);
  assert(input_transfers >= 4);
  assert(output_transfers <= 1);
  assert(error_events == 0);
  return 0;
}
