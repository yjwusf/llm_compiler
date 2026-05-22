#include "Ve1_h1_rgmii_ethernet_ingress.h"
#include "verilated.h"

#include <cassert>
#include <cstdint>

namespace {

void tick_core(Ve1_h1_rgmii_ethernet_ingress& dut, std::uint64_t& cycles) {
  dut.clk_i = 0;
  dut.eval();
  dut.clk_i = 1;
  dut.eval();
  ++cycles;
}

void tick_rx(Ve1_h1_rgmii_ethernet_ingress& dut, std::uint64_t& cycles) {
  dut.rgmii_rx_clk_i = 0;
  dut.eval();
  dut.rgmii_rx_clk_i = 1;
  dut.eval();
  ++cycles;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_rgmii_ethernet_ingress dut;
  std::uint64_t cycles = 0;
  std::uint64_t rx_cycles = 0;
  std::uint64_t input_transfers = 0;
  std::uint64_t output_transfers = 0;
  std::uint64_t error_events = 0;

  dut.rst_ni = 0;
  dut.stream_ready_i = 1;
  dut.rgmii_rx_ctl_i = 0;
  dut.rgmii_rxd_i = 0;
  tick_core(dut, cycles);
  tick_rx(dut, rx_cycles);

  dut.rst_ni = 1;
  for (std::uint8_t nibble = 1; nibble <= 4; ++nibble) {
    dut.rgmii_rx_ctl_i = 1;
    dut.rgmii_rxd_i = nibble;
    tick_rx(dut, rx_cycles);
    ++input_transfers;
  }

  dut.rgmii_rx_ctl_i = 0;
  tick_core(dut, cycles);
  assert(dut.stream_valid_o == 1);
  assert(dut.stream_error_o == 0);
  if (dut.stream_valid_o && dut.stream_ready_i) {
    ++output_transfers;
  }
  tick_rx(dut, rx_cycles);

  assert(cycles > 0);
  assert(rx_cycles > 0);
  assert(input_transfers == 4);
  assert(output_transfers >= 1);
  assert(error_events == 0);
  return 0;
}
