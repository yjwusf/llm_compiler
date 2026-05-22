#include "Ve1_h1_control_cpu.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void eval(Ve1_h1_control_cpu& dut) {
  dut.eval();
}

void tick(Ve1_h1_control_cpu& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  eval(dut);
  dut.clk_i = 1;
  eval(dut);
  ++cycles;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_control_cpu dut;
  std::uint64_t cycles = 0;
  std::uint64_t array_commands = 0;
  std::uint64_t stall_cycles = 0;
  std::uint64_t error_events = 0;

  dut.rst_ni = 0;
  dut.cmd_ready_i = 0;
  dut.array_done_i = 0;
  dut.array_error_i = 0;
  tick(dut, cycles);
  tick(dut, cycles);

  dut.rst_ni = 1;
  tick(dut, cycles);
  assert(dut.cmd_valid_o == 1);
  assert(dut.cmd_rows_o == 16);
  assert(dut.cmd_cols_o == 16);
  assert(dut.cmd_depth_o == 16);

  if (dut.cmd_valid_o && !dut.cmd_ready_i) {
    ++stall_cycles;
  }

  dut.cmd_ready_i = 1;
  tick(dut, cycles);
  if (dut.cmd_valid_o && dut.cmd_ready_i) {
    ++array_commands;
  }

  dut.cmd_ready_i = 0;
  dut.array_done_i = 1;
  tick(dut, cycles);
  dut.array_done_i = 0;
  tick(dut, cycles);

  assert(dut.debug_halted_o == 1);
  assert(cycles > 0);
  assert(array_commands <= 1);
  assert(error_events == 0);
  assert(stall_cycles >= 1);
  return 0;
}
