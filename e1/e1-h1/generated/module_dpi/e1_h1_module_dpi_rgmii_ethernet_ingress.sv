`default_nettype none

module e1_h1_module_dpi_rgmii_ethernet_ingress;
  import "DPI-C" function void e1_h1_module_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_module_dpi_compare_u32(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );

  logic clk_i;
  logic rst_ni;
  logic rgmii_rx_clk_i;
  logic [3:0] rgmii_rxd_i;
  logic rgmii_rx_ctl_i;
  logic stream_ready_i;
  logic stream_valid_imp1, stream_valid_imp2;
  logic [63:0] stream_data_imp1, stream_data_imp2;
  logic stream_last_imp1, stream_last_imp2;
  logic stream_error_imp1, stream_error_imp2;

  e1_h1_imp1_rgmii_ethernet_ingress_ref u_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .rgmii_rx_clk_i(rgmii_rx_clk_i),
    .rgmii_rxd_i(rgmii_rxd_i),
    .rgmii_rx_ctl_i(rgmii_rx_ctl_i),
    .stream_valid_o(stream_valid_imp1),
    .stream_ready_i(stream_ready_i),
    .stream_data_o(stream_data_imp1),
    .stream_last_o(stream_last_imp1),
    .stream_error_o(stream_error_imp1)
  );

  e1_h1_rgmii_ethernet_ingress u_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .rgmii_rx_clk_i(rgmii_rx_clk_i),
    .rgmii_rxd_i(rgmii_rxd_i),
    .rgmii_rx_ctl_i(rgmii_rx_ctl_i),
    .stream_valid_o(stream_valid_imp2),
    .stream_ready_i(stream_ready_i),
    .stream_data_o(stream_data_imp2),
    .stream_last_o(stream_last_imp2),
    .stream_error_o(stream_error_imp2)
  );

  task automatic tick_core;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic tick_rgmii;
    rgmii_rx_clk_i = 1'b0; #1;
    rgmii_rx_clk_i = 1'b1; #1;
  endtask

  task automatic check32(input string name, input int cycle, input logic [31:0] imp1, input logic [31:0] imp2);
    if (e1_h1_module_dpi_compare_u32(name, cycle, int'(imp1), int'(imp2)) == 0) begin
      $fatal(1, "rgmii_ethernet_ingress mismatch %s cycle %0d", name, cycle);
    end
  endtask

  task automatic check_outputs(input int cycle);
    check32("stream_valid_o", cycle, {31'd0, stream_valid_imp1}, {31'd0, stream_valid_imp2});
    check32("stream_data_o.lo", cycle, stream_data_imp1[31:0], stream_data_imp2[31:0]);
    check32("stream_data_o.hi", cycle, stream_data_imp1[63:32], stream_data_imp2[63:32]);
    check32("stream_last_o", cycle, {31'd0, stream_last_imp1}, {31'd0, stream_last_imp2});
    check32("stream_error_o", cycle, {31'd0, stream_error_imp1}, {31'd0, stream_error_imp2});
  endtask

  initial begin
    e1_h1_module_dpi_begin("rgmii_ethernet_ingress", "module_only_rgmii_ingress");
    clk_i = 1'b0;
    rgmii_rx_clk_i = 1'b0;
    rst_ni = 1'b0;
    rgmii_rxd_i = 4'd0;
    rgmii_rx_ctl_i = 1'b0;
    stream_ready_i = 1'b0;
    tick_core();
    tick_rgmii();
    rst_ni = 1'b1;
    for (int cycle = 0; cycle < 10; cycle++) begin
      e1_h1_module_dpi_cycle("rgmii_ethernet_ingress", cycle, "drive_rgmii");
      rgmii_rx_ctl_i = (cycle >= 1 && cycle <= 5);
      stream_ready_i = (cycle >= 7);
      rgmii_rxd_i = cycle[3:0];
      tick_rgmii();
      e1_h1_module_dpi_cycle("rgmii_ethernet_ingress", cycle, "sample_stream");
      check_outputs(cycle);
    end
    $finish;
  end
endmodule

`default_nettype wire
