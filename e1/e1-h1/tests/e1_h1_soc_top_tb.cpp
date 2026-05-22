#include "Ve1_h1_soc_top.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void eval(Ve1_h1_soc_top& dut) {
  dut.eval();
}

void tick_core(Ve1_h1_soc_top& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  eval(dut);
  dut.clk_i = 1;
  eval(dut);
  ++cycles;
}

void tick_rx(Ve1_h1_soc_top& dut, std::uint64_t& rx_cycles) {
  dut.rgmii_rx_clk_i = 0;
  eval(dut);
  dut.rgmii_rx_clk_i = 1;
  eval(dut);
  ++rx_cycles;
}

void tick_both(Ve1_h1_soc_top& dut, std::uint64_t& cycles, std::uint64_t& rx_cycles) {
  tick_rx(dut, rx_cycles);
  tick_core(dut, cycles);
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_soc_top dut;
  std::uint64_t cycles = 0;
  std::uint64_t rx_cycles = 0;
  std::uint64_t busy_cycles = 0;
  std::uint64_t halted_cycles = 0;

  dut.clk_i = 0;
  dut.rgmii_rx_clk_i = 0;
  dut.rgmii_rx_ctl_i = 0;
  dut.rgmii_rxd_i = 0;
  dut.rst_ni = 0;
  for (int i = 0; i < 4; ++i) {
    tick_both(dut, cycles, rx_cycles);
  }

  dut.rst_ni = 1;
  for (int i = 0; i < 128; ++i) {
    dut.rgmii_rx_ctl_i = 1;
    dut.rgmii_rxd_i = static_cast<std::uint8_t>((i & 0x0f) + 1);
    tick_both(dut, cycles, rx_cycles);
    if (dut.debug_array_busy_o) {
      ++busy_cycles;
    }
    if (dut.debug_halted_o) {
      ++halted_cycles;
    }
  }

  dut.rgmii_rx_ctl_i = 0;
  for (int i = 0; i < 32; ++i) {
    tick_both(dut, cycles, rx_cycles);
    if (dut.debug_array_busy_o) {
      ++busy_cycles;
    }
    if (dut.debug_halted_o) {
      ++halted_cycles;
    }
  }

  assert(cycles > 0);
  assert(rx_cycles > 0);
  assert(busy_cycles > 0);
  assert(halted_cycles > 0);
  assert(dut.debug_halted_o == 1);
  return 0;
}
