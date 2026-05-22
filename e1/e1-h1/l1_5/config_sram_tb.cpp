#include "Ve1_h1_config_sram.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void tick(Ve1_h1_config_sram& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  dut.eval();
  dut.clk_i = 1;
  dut.eval();
  ++cycles;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_config_sram dut;
  std::uint64_t cycles = 0;
  std::uint64_t input_transfers = 0;
  std::uint64_t output_transfers = 0;
  std::uint64_t stall_cycles = 0;
  std::uint64_t error_events = 0;

  dut.rst_ni = 0;
  tick(dut, cycles);
  dut.rst_ni = 1;
  tick(dut, cycles);
  tick(dut, cycles);

  assert(cycles == 3);
  assert(input_transfers == 0);
  assert(output_transfers == 0);
  assert(stall_cycles == 0);
  assert(error_events == 0);
  return 0;
}
