#include "Ve1_h1_stream_sram.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void tick(Ve1_h1_stream_sram& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  dut.eval();
  dut.clk_i = 1;
  dut.eval();
  ++cycles;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_stream_sram dut;
  std::uint64_t cycles = 0;
  std::uint64_t input_transfers = 0;
  std::uint64_t output_transfers = 0;
  std::uint64_t stall_cycles = 0;
  std::uint64_t error_events = 0;

  dut.rst_ni = 0;
  dut.stream_valid_i = 0;
  dut.stream_data_i = 0;
  dut.stream_last_i = 0;
  dut.stream_error_i = 0;
  dut.array_ready_i = 0;
  tick(dut, cycles);

  dut.rst_ni = 1;
  dut.stream_valid_i = 1;
  dut.stream_data_i = 0x1234;
  dut.eval();
  if (dut.stream_valid_i && dut.stream_ready_o) {
    ++input_transfers;
  }
  tick(dut, cycles);
  assert(dut.array_valid_o == 1);
  assert(dut.array_data_o == 0x1234);

  if (dut.array_valid_o && !dut.array_ready_i) {
    ++stall_cycles;
  }
  dut.array_ready_i = 1;
  dut.eval();
  if (dut.array_valid_o && dut.array_ready_i) {
    ++output_transfers;
  }
  tick(dut, cycles);

  assert(cycles > 0);
  assert(input_transfers >= 1);
  assert(output_transfers <= 1);
  assert(stall_cycles >= 1);
  assert(error_events == 0);
  return 0;
}
