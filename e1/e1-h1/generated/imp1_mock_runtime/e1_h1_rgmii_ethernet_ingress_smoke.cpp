#include "Ve1_h1_rgmii_ethernet_ingress.h"
#include "verilated.h"
#include <iostream>

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Ve1_h1_rgmii_ethernet_ingress top;
  top.clk_i = 0;
  top.rgmii_rx_clk_i = 0;
  top.rgmii_rx_ctl_i = 0;
  top.rgmii_rxd_i = 0;
  top.rst_ni = 0;
  top.stream_ready_i = 0;
  for (int cycle = 0; cycle < 8; ++cycle) {
    top.rst_ni = cycle > 0;
    top.stream_ready_i = cycle >= 2;
    top.rgmii_rx_ctl_i = cycle >= 1 && cycle <= 4;
    top.rgmii_rxd_i = cycle & 0xf;
    top.eval();
    top.clk_i = 1;
    top.eval();
    top.clk_i = 0;
    top.eval();
    top.rgmii_rx_clk_i = 1;
    top.eval();
    top.rgmii_rx_clk_i = 0;
    top.eval();
  }
  std::cout << "E1_H1_IMP1_MOCK_RUNTIME module=e1_h1_rgmii_ethernet_ingress cycles=8\n";
  top.final();
  return 0;
}
