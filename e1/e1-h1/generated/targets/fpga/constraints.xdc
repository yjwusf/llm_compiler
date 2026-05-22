# E1-H1 FPGA smoke constraints.
# Digital-only RGMII boundary. External PHY owns analog signaling.
create_clock -name clk_i -period 10.000 [get_ports clk_i]
create_clock -name rgmii_rx_clk_i -period 8.000 [get_ports rgmii_rx_clk_i]
set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd_i[*] rgmii_rx_ctl_i rgmii_rx_clk_i}]
set_false_path -from [get_ports rgmii_rx_clk_i] -to [get_ports clk_i]
