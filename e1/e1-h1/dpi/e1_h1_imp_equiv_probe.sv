`default_nettype none

module e1_h1_imp_equiv_probe;
  import "DPI-C" function void e1_h1_dpi_begin(
    input string ip_name,
    input string vip_case
  );
  import "DPI-C" function int e1_h1_dpi_compare_i(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );

  logic clk_i;
  logic rst_ni;

  logic cpu_cmd_ready_i;
  logic cpu_array_done_i;
  logic cpu_array_error_i;
  logic cpu_cmd_valid_imp1;
  logic cpu_cmd_valid_imp2;
  logic [31:0] cpu_cmd_input_addr_imp1;
  logic [31:0] cpu_cmd_input_addr_imp2;
  logic [31:0] cpu_cmd_weight_addr_imp1;
  logic [31:0] cpu_cmd_weight_addr_imp2;
  logic [31:0] cpu_cmd_output_addr_imp1;
  logic [31:0] cpu_cmd_output_addr_imp2;
  logic [15:0] cpu_cmd_rows_imp1;
  logic [15:0] cpu_cmd_rows_imp2;
  logic [15:0] cpu_cmd_cols_imp1;
  logic [15:0] cpu_cmd_cols_imp2;
  logic [15:0] cpu_cmd_depth_imp1;
  logic [15:0] cpu_cmd_depth_imp2;
  logic cpu_debug_halted_imp1;
  logic cpu_debug_halted_imp2;

  e1_h1_imp1_control_cpu_ref u_cpu_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_o(cpu_cmd_valid_imp1),
    .cmd_ready_i(cpu_cmd_ready_i),
    .cmd_input_addr_o(cpu_cmd_input_addr_imp1),
    .cmd_weight_addr_o(cpu_cmd_weight_addr_imp1),
    .cmd_output_addr_o(cpu_cmd_output_addr_imp1),
    .cmd_rows_o(cpu_cmd_rows_imp1),
    .cmd_cols_o(cpu_cmd_cols_imp1),
    .cmd_depth_o(cpu_cmd_depth_imp1),
    .array_done_i(cpu_array_done_i),
    .array_error_i(cpu_array_error_i),
    .debug_halted_o(cpu_debug_halted_imp1)
  );

  e1_h1_control_cpu u_cpu_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_o(cpu_cmd_valid_imp2),
    .cmd_ready_i(cpu_cmd_ready_i),
    .cmd_input_addr_o(cpu_cmd_input_addr_imp2),
    .cmd_weight_addr_o(cpu_cmd_weight_addr_imp2),
    .cmd_output_addr_o(cpu_cmd_output_addr_imp2),
    .cmd_rows_o(cpu_cmd_rows_imp2),
    .cmd_cols_o(cpu_cmd_cols_imp2),
    .cmd_depth_o(cpu_cmd_depth_imp2),
    .array_done_i(cpu_array_done_i),
    .array_error_i(cpu_array_error_i),
    .debug_halted_o(cpu_debug_halted_imp2)
  );

  logic        array_cmd_valid_i;
  logic [31:0] array_cmd_input_addr_i;
  logic [31:0] array_cmd_weight_addr_i;
  logic [31:0] array_cmd_output_addr_i;
  logic [15:0] array_cmd_rows_i;
  logic [15:0] array_cmd_cols_i;
  logic [15:0] array_cmd_depth_i;
  logic        array_input_valid_i;
  logic [63:0] array_input_data_i;
  logic        array_cmd_ready_imp1;
  logic        array_cmd_ready_imp2;
  logic        array_input_ready_imp1;
  logic        array_input_ready_imp2;
  logic        array_done_imp1;
  logic        array_done_imp2;
  logic        array_error_imp1;
  logic        array_error_imp2;
  logic        array_debug_busy_imp1;
  logic        array_debug_busy_imp2;

  e1_h1_imp1_systolic_array_ref u_array_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_i),
    .cmd_ready_o(array_cmd_ready_imp1),
    .cmd_input_addr_i(array_cmd_input_addr_i),
    .cmd_weight_addr_i(array_cmd_weight_addr_i),
    .cmd_output_addr_i(array_cmd_output_addr_i),
    .cmd_rows_i(array_cmd_rows_i),
    .cmd_cols_i(array_cmd_cols_i),
    .cmd_depth_i(array_cmd_depth_i),
    .input_valid_i(array_input_valid_i),
    .input_ready_o(array_input_ready_imp1),
    .input_data_i(array_input_data_i),
    .done_o(array_done_imp1),
    .error_o(array_error_imp1),
    .debug_busy_o(array_debug_busy_imp1)
  );

  e1_h1_systolic_array u_array_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_i),
    .cmd_ready_o(array_cmd_ready_imp2),
    .cmd_input_addr_i(array_cmd_input_addr_i),
    .cmd_weight_addr_i(array_cmd_weight_addr_i),
    .cmd_output_addr_i(array_cmd_output_addr_i),
    .cmd_rows_i(array_cmd_rows_i),
    .cmd_cols_i(array_cmd_cols_i),
    .cmd_depth_i(array_cmd_depth_i),
    .input_valid_i(array_input_valid_i),
    .input_ready_o(array_input_ready_imp2),
    .input_data_i(array_input_data_i),
    .done_o(array_done_imp2),
    .error_o(array_error_imp2),
    .debug_busy_o(array_debug_busy_imp2)
  );

  logic        stream_valid_i;
  logic [63:0] stream_data_i;
  logic        stream_last_i;
  logic        stream_error_i;
  logic        stream_array_ready_i;
  logic        stream_ready_imp1;
  logic        stream_ready_imp2;
  logic        stream_array_valid_imp1;
  logic        stream_array_valid_imp2;
  logic [63:0] stream_array_data_imp1;
  logic [63:0] stream_array_data_imp2;

  e1_h1_imp1_stream_sram_ref u_stream_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_imp1),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(stream_array_valid_imp1),
    .array_ready_i(stream_array_ready_i),
    .array_data_o(stream_array_data_imp1)
  );

  e1_h1_stream_sram u_stream_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_imp2),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(stream_array_valid_imp2),
    .array_ready_i(stream_array_ready_i),
    .array_data_o(stream_array_data_imp2)
  );

  logic        rgmii_rx_clk_i;
  logic [3:0]  rgmii_rxd_i;
  logic        rgmii_rx_ctl_i;
  logic        rgmii_stream_ready_i;
  logic        rgmii_stream_valid_imp1;
  logic        rgmii_stream_valid_imp2;
  logic [63:0] rgmii_stream_data_imp1;
  logic [63:0] rgmii_stream_data_imp2;
  logic        rgmii_stream_last_imp1;
  logic        rgmii_stream_last_imp2;
  logic        rgmii_stream_error_imp1;
  logic        rgmii_stream_error_imp2;

  e1_h1_imp1_rgmii_ethernet_ingress_ref u_rgmii_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .rgmii_rx_clk_i(rgmii_rx_clk_i),
    .rgmii_rxd_i(rgmii_rxd_i),
    .rgmii_rx_ctl_i(rgmii_rx_ctl_i),
    .stream_valid_o(rgmii_stream_valid_imp1),
    .stream_ready_i(rgmii_stream_ready_i),
    .stream_data_o(rgmii_stream_data_imp1),
    .stream_last_o(rgmii_stream_last_imp1),
    .stream_error_o(rgmii_stream_error_imp1)
  );

  e1_h1_rgmii_ethernet_ingress u_rgmii_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .rgmii_rx_clk_i(rgmii_rx_clk_i),
    .rgmii_rxd_i(rgmii_rxd_i),
    .rgmii_rx_ctl_i(rgmii_rx_ctl_i),
    .stream_valid_o(rgmii_stream_valid_imp2),
    .stream_ready_i(rgmii_stream_ready_i),
    .stream_data_o(rgmii_stream_data_imp2),
    .stream_last_o(rgmii_stream_last_imp2),
    .stream_error_o(rgmii_stream_error_imp2)
  );

  e1_h1_imp1_config_sram_ref #(
    .SIZE_BYTES(524288),
    .DATA_WIDTH(128),
    .BANKS(8)
  ) u_config_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni)
  );

  e1_h1_config_sram #(
    .SIZE_BYTES(524288),
    .DATA_WIDTH(128),
    .BANKS(8)
  ) u_config_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni)
  );

  task automatic check32(input string name, input int cycle, input logic [31:0] imp1, input logic [31:0] imp2);
    if (e1_h1_dpi_compare_i(name, cycle, int'(imp1), int'(imp2)) == 0) begin
      $fatal(1, "E1-H1 imp2 mismatch %s cycle %0d imp1=%0d imp2=%0d", name, cycle, imp1, imp2);
    end
  endtask

  task automatic check64(input string name, input int cycle, input logic [63:0] imp1, input logic [63:0] imp2);
    check32({name, ".lo"}, cycle, imp1[31:0], imp2[31:0]);
    check32({name, ".hi"}, cycle, imp1[63:32], imp2[63:32]);
  endtask

  task automatic tick_core;
    clk_i = 1'b0;
    #1;
    clk_i = 1'b1;
    #1;
  endtask

  task automatic tick_rgmii;
    rgmii_rx_clk_i = 1'b0;
    #1;
    rgmii_rx_clk_i = 1'b1;
    #1;
  endtask

  task automatic check_cpu(input int cycle);
    check32("control_cpu.cmd_valid", cycle, {31'd0, cpu_cmd_valid_imp1}, {31'd0, cpu_cmd_valid_imp2});
    check32("control_cpu.cmd_input_addr", cycle, cpu_cmd_input_addr_imp1, cpu_cmd_input_addr_imp2);
    check32("control_cpu.cmd_weight_addr", cycle, cpu_cmd_weight_addr_imp1, cpu_cmd_weight_addr_imp2);
    check32("control_cpu.cmd_output_addr", cycle, cpu_cmd_output_addr_imp1, cpu_cmd_output_addr_imp2);
    check32("control_cpu.cmd_rows", cycle, {16'd0, cpu_cmd_rows_imp1}, {16'd0, cpu_cmd_rows_imp2});
    check32("control_cpu.cmd_cols", cycle, {16'd0, cpu_cmd_cols_imp1}, {16'd0, cpu_cmd_cols_imp2});
    check32("control_cpu.cmd_depth", cycle, {16'd0, cpu_cmd_depth_imp1}, {16'd0, cpu_cmd_depth_imp2});
    check32("control_cpu.debug_halted", cycle, {31'd0, cpu_debug_halted_imp1}, {31'd0, cpu_debug_halted_imp2});
  endtask

  task automatic check_array(input int cycle);
    check32("systolic_array.cmd_ready", cycle, {31'd0, array_cmd_ready_imp1}, {31'd0, array_cmd_ready_imp2});
    check32("systolic_array.input_ready", cycle, {31'd0, array_input_ready_imp1}, {31'd0, array_input_ready_imp2});
    check32("systolic_array.done", cycle, {31'd0, array_done_imp1}, {31'd0, array_done_imp2});
    check32("systolic_array.error", cycle, {31'd0, array_error_imp1}, {31'd0, array_error_imp2});
    check32("systolic_array.debug_busy", cycle, {31'd0, array_debug_busy_imp1}, {31'd0, array_debug_busy_imp2});
  endtask

  task automatic check_stream(input int cycle);
    check32("ingress_sram.stream_ready", cycle, {31'd0, stream_ready_imp1}, {31'd0, stream_ready_imp2});
    check32("ingress_sram.array_valid", cycle, {31'd0, stream_array_valid_imp1}, {31'd0, stream_array_valid_imp2});
    check64("ingress_sram.array_data", cycle, stream_array_data_imp1, stream_array_data_imp2);
  endtask

  task automatic check_rgmii(input int cycle);
    check32("rgmii_ethernet_ingress.stream_valid", cycle, {31'd0, rgmii_stream_valid_imp1}, {31'd0, rgmii_stream_valid_imp2});
    check64("rgmii_ethernet_ingress.stream_data", cycle, rgmii_stream_data_imp1, rgmii_stream_data_imp2);
    check32("rgmii_ethernet_ingress.stream_last", cycle, {31'd0, rgmii_stream_last_imp1}, {31'd0, rgmii_stream_last_imp2});
    check32("rgmii_ethernet_ingress.stream_error", cycle, {31'd0, rgmii_stream_error_imp1}, {31'd0, rgmii_stream_error_imp2});
  endtask

  initial begin
    e1_h1_dpi_begin("e1_h1", "all_imp2_equivalence");

    clk_i = 1'b0;
    rgmii_rx_clk_i = 1'b0;
    rst_ni = 1'b0;
    cpu_cmd_ready_i = 1'b0;
    cpu_array_done_i = 1'b0;
    cpu_array_error_i = 1'b0;
    array_cmd_valid_i = 1'b0;
    array_cmd_input_addr_i = 32'h0001_0000;
    array_cmd_weight_addr_i = 32'h0004_0000;
    array_cmd_output_addr_i = 32'h0008_0000;
    array_cmd_rows_i = 16'd16;
    array_cmd_cols_i = 16'd16;
    array_cmd_depth_i = 16'd16;
    array_input_valid_i = 1'b0;
    array_input_data_i = 64'd0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    stream_array_ready_i = 1'b0;
    rgmii_rxd_i = 4'd0;
    rgmii_rx_ctl_i = 1'b0;
    rgmii_stream_ready_i = 1'b0;

    tick_core();
    tick_rgmii();
    rst_ni = 1'b1;

    for (int cycle = 0; cycle < 9; cycle++) begin
      cpu_cmd_ready_i = (cycle >= 2 && cycle <= 3);
      cpu_array_done_i = (cycle == 5);
      array_cmd_valid_i = (cycle == 1);
      array_input_valid_i = (cycle >= 3 && cycle <= 6);
      array_input_data_i = 64'h1000 + cycle[7:0];
      stream_valid_i = (cycle == 1 || cycle == 4 || cycle == 5);
      stream_array_ready_i = (cycle != 3);
      stream_data_i = 64'h2000 + cycle[7:0];
      stream_last_i = (cycle == 5);
      stream_error_i = (cycle == 5);
      tick_core();
      check_cpu(cycle);
      check_array(cycle);
      check_stream(cycle);
    end

    for (int cycle = 0; cycle < 10; cycle++) begin
      rgmii_rx_ctl_i = (cycle >= 1 && cycle <= 5);
      rgmii_stream_ready_i = (cycle >= 7);
      rgmii_rxd_i = cycle[3:0];
      tick_rgmii();
      check_rgmii(cycle);
    end

    $finish;
  end
endmodule

`default_nettype wire
