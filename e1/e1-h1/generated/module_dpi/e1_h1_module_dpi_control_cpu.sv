`default_nettype none

module e1_h1_module_dpi_control_cpu;
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
  logic cmd_ready_i;
  logic array_done_i;
  logic array_error_i;
  logic cmd_valid_imp1, cmd_valid_imp2;
  logic [31:0] cmd_input_addr_imp1, cmd_input_addr_imp2;
  logic [31:0] cmd_weight_addr_imp1, cmd_weight_addr_imp2;
  logic [31:0] cmd_output_addr_imp1, cmd_output_addr_imp2;
  logic [15:0] cmd_rows_imp1, cmd_rows_imp2;
  logic [15:0] cmd_cols_imp1, cmd_cols_imp2;
  logic [15:0] cmd_depth_imp1, cmd_depth_imp2;
  logic debug_halted_imp1, debug_halted_imp2;

  e1_h1_imp1_control_cpu_ref u_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_o(cmd_valid_imp1),
    .cmd_ready_i(cmd_ready_i),
    .cmd_input_addr_o(cmd_input_addr_imp1),
    .cmd_weight_addr_o(cmd_weight_addr_imp1),
    .cmd_output_addr_o(cmd_output_addr_imp1),
    .cmd_rows_o(cmd_rows_imp1),
    .cmd_cols_o(cmd_cols_imp1),
    .cmd_depth_o(cmd_depth_imp1),
    .array_done_i(array_done_i),
    .array_error_i(array_error_i),
    .debug_halted_o(debug_halted_imp1)
  );

  e1_h1_control_cpu u_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_o(cmd_valid_imp2),
    .cmd_ready_i(cmd_ready_i),
    .cmd_input_addr_o(cmd_input_addr_imp2),
    .cmd_weight_addr_o(cmd_weight_addr_imp2),
    .cmd_output_addr_o(cmd_output_addr_imp2),
    .cmd_rows_o(cmd_rows_imp2),
    .cmd_cols_o(cmd_cols_imp2),
    .cmd_depth_o(cmd_depth_imp2),
    .array_done_i(array_done_i),
    .array_error_i(array_error_i),
    .debug_halted_o(debug_halted_imp2)
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic check32(input string name, input int cycle, input logic [31:0] imp1, input logic [31:0] imp2);
    if (e1_h1_module_dpi_compare_u32(name, cycle, int'(imp1), int'(imp2)) == 0) begin
      $fatal(1, "control_cpu mismatch %s cycle %0d", name, cycle);
    end
  endtask

  task automatic check_outputs(input int cycle);
    check32("cmd_valid_o", cycle, {31'd0, cmd_valid_imp1}, {31'd0, cmd_valid_imp2});
    check32("cmd_input_addr_o", cycle, cmd_input_addr_imp1, cmd_input_addr_imp2);
    check32("cmd_weight_addr_o", cycle, cmd_weight_addr_imp1, cmd_weight_addr_imp2);
    check32("cmd_output_addr_o", cycle, cmd_output_addr_imp1, cmd_output_addr_imp2);
    check32("cmd_rows_o", cycle, {16'd0, cmd_rows_imp1}, {16'd0, cmd_rows_imp2});
    check32("cmd_cols_o", cycle, {16'd0, cmd_cols_imp1}, {16'd0, cmd_cols_imp2});
    check32("cmd_depth_o", cycle, {16'd0, cmd_depth_imp1}, {16'd0, cmd_depth_imp2});
    check32("debug_halted_o", cycle, {31'd0, debug_halted_imp1}, {31'd0, debug_halted_imp2});
  endtask

  initial begin
    e1_h1_module_dpi_begin("control_cpu", "module_only_control_cpu");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    cmd_ready_i = 1'b0;
    array_done_i = 1'b0;
    array_error_i = 1'b0;
    tick();
    rst_ni = 1'b1;
    for (int cycle = 0; cycle < 8; cycle++) begin
      e1_h1_module_dpi_cycle("control_cpu", cycle, "drive");
      cmd_ready_i = (cycle >= 1 && cycle <= 2);
      array_done_i = (cycle == 5);
      array_error_i = 1'b0;
      tick();
      e1_h1_module_dpi_cycle("control_cpu", cycle, "sample");
      check_outputs(cycle);
    end
    $finish;
  end
endmodule

`default_nettype wire
