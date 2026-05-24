`default_nettype none

module e1_h1_tinyllama_linear_tile_engine (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [2:0]  cycle_phase_o,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic        scheduler_cmd_valid_o,
  output logic        array_cmd_valid_o,
  output logic        array_cmd_ready_o,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic [63:0] buffer_array_data_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o
);

  logic scheduler_done;
  logic scheduler_error;
  logic array_error;

  e1_h1_tinyllama_linear_scheduler u_scheduler (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(scheduler_done),
    .error_o(scheduler_error),
    .cmd_valid_o(scheduler_cmd_valid_o),
    .cmd_ready_i(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .array_done_i(array_done_o),
    .array_error_i(array_error),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o)
  );

  assign array_cmd_valid_o = scheduler_cmd_valid_o && (cycle_phase_o == 3'd2);

  e1_h1_stream_sram u_latch_buffer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(buffer_array_valid_o),
    .array_ready_i(buffer_array_ready_o),
    .array_data_o(buffer_array_data_o)
  );

  e1_h1_systolic_array u_systolic_array (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_o),
    .cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_i(cmd_input_addr_o),
    .cmd_weight_addr_i(cmd_weight_addr_o),
    .cmd_output_addr_i(cmd_output_addr_o),
    .cmd_rows_i(cmd_rows_o),
    .cmd_cols_i(cmd_cols_o),
    .cmd_depth_i(cmd_depth_o),
    .input_valid_i(buffer_array_valid_o),
    .input_ready_o(buffer_array_ready_o),
    .input_data_i(buffer_array_data_o),
    .done_o(array_done_o),
    .error_o(array_error),
    .debug_busy_o(array_debug_busy_o)
  );

  assign done_o = scheduler_done;
  assign error_o = scheduler_error || array_error;

endmodule

`default_nettype wire
