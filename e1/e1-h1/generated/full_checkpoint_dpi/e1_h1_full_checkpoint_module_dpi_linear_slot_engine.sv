`default_nettype none

module e1_h1_full_checkpoint_module_dpi_linear_slot_engine;
  import "DPI-C" function void e1_h1_full_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_full_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_full_dpi_expect_u32(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic expect_u32(input string signal_name, input int cycle, input int expected, input logic [31:0] actual);
    if (e1_h1_full_dpi_expect_u32("linear_slot_engine", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "linear_slot_engine mismatch %s", signal_name);
    end
  endtask

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic [31:0] layer_i;
  logic [2:0] op_index_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_commands_o;
  logic [31:0] expected_commands_o;
  logic [2:0] cycle_phase_o;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic scheduler_cmd_valid_o;
  logic array_cmd_valid_o;
  logic array_cmd_ready_o;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic [63:0] buffer_array_data_o;
  logic array_done_o;
  logic array_debug_busy_o;

  e1_h1_tinyllama_linear_slot_engine #(
    .SmokeMaxTilesPerLinearSlot(2)
  ) u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .layer_i(layer_i),
    .op_index_i(op_index_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_commands_o(issued_commands_o),
    .expected_commands_o(expected_commands_o),
    .cycle_phase_o(cycle_phase_o),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .scheduler_cmd_valid_o(scheduler_cmd_valid_o),
    .array_cmd_valid_o(array_cmd_valid_o),
    .array_cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o)
  );

  initial begin
    e1_h1_full_dpi_begin("linear_slot_engine", "bounded_two_tile_linear_slot");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    layer_i = 32'd0;
    op_index_i = 3'd0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    for (int cycle = 0; cycle < 128 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("linear_slot_engine", cycle, "slot_local_scheduler_latch_array");
      stream_valid_i = 1'b1;
      stream_data_i = 64'h4000 + cycle[15:0];
      tick();
      start_i = 1'b0;
    end
    expect_u32("issued_commands_o", 0, 2, issued_commands_o);
    expect_u32("expected_commands_o", 0, 2, expected_commands_o);
    if (!done_o) $fatal(1, "linear slot engine did not finish");
    if (error_o) $fatal(1, "linear slot engine reported error");
    $finish;
  end
endmodule

`default_nettype wire
