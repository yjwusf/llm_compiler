`default_nettype none

module e1_h1_tinyllama_full_checkpoint_top #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
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
  output logic [31:0] issued_graph_slots_o,
  output logic [31:0] issued_linear_commands_o,
  output logic [31:0] issued_control_ops_o,
  output logic [31:0] active_layer_o,
  output logic [3:0]  active_slot_o,
  output logic [1:0]  graph_cycle_phase_o,
  output logic [2:0]  linear_cycle_phase_o,
  output logic [1:0]  control_cycle_phase_o,
  output logic        launch_linear_o,
  output logic        launch_control_o,
  output logic        linear_busy_o,
  output logic        control_busy_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o,
  output logic        debug_scheduler_cmd_valid_o,
  output logic        debug_array_cmd_valid_o,
  output logic        debug_array_cmd_ready_o,
  output logic [31:0] debug_cmd_input_addr_o,
  output logic [31:0] debug_cmd_weight_addr_o,
  output logic [31:0] debug_cmd_output_addr_o,
  output logic [15:0] debug_cmd_rows_o,
  output logic [15:0] debug_cmd_cols_o,
  output logic [15:0] debug_cmd_depth_o,
  output logic [31:0] debug_linear_layer_o,
  output logic [2:0]  debug_linear_op_index_o,
  output logic [8:0]  debug_linear_input_tile_o,
  output logic [8:0]  debug_linear_output_tile_o
);

  logic        graph_slot_valid;
  logic        graph_busy;
  logic        graph_op_done;
  logic [2:0]  graph_linear_op_index;
  logic [2:0]  graph_control_op_index;
  logic [3:0]  graph_control_kind;
  logic [31:0] graph_linear_tile_count;
  logic [31:0] linear_slot_issued_commands;
  logic [31:0] linear_slot_expected_commands;
  logic        linear_done;
  logic        linear_error;
  logic        control_done;
  logic        active_is_linear;
  logic        control_valid;
  logic        control_commit;
  logic [31:0] control_slot_issued_ops;
  logic [63:0] buffer_array_data;
  logic        scheduler_cmd_valid;
  logic        array_cmd_valid;
  logic        array_cmd_ready;
  logic [31:0] cmd_input_addr;
  logic [31:0] cmd_weight_addr;
  logic [31:0] cmd_output_addr;
  logic [15:0] cmd_rows;
  logic [15:0] cmd_cols;
  logic [15:0] cmd_depth;
  logic [31:0] linear_layer;
  logic [2:0]  linear_op_index;
  logic [8:0]  linear_input_tile;
  logic [8:0]  linear_output_tile;
  logic [31:0] control_layer;
  logic [2:0]  control_op_index;
  logic [3:0]  control_kind;

  assign active_is_linear = graph_linear_tile_count != 32'd0;
  assign graph_op_done = active_is_linear ? linear_done : control_done;
  assign busy_o = graph_busy || linear_busy_o || control_busy_o;
  assign error_o = linear_error;
  assign debug_scheduler_cmd_valid_o = scheduler_cmd_valid;
  assign debug_array_cmd_valid_o = array_cmd_valid;
  assign debug_array_cmd_ready_o = array_cmd_ready;
  assign debug_cmd_input_addr_o = cmd_input_addr;
  assign debug_cmd_weight_addr_o = cmd_weight_addr;
  assign debug_cmd_output_addr_o = cmd_output_addr;
  assign debug_cmd_rows_o = cmd_rows;
  assign debug_cmd_cols_o = cmd_cols;
  assign debug_cmd_depth_o = cmd_depth;
  assign debug_linear_layer_o = linear_layer;
  assign debug_linear_op_index_o = linear_op_index;
  assign debug_linear_input_tile_o = linear_input_tile;
  assign debug_linear_output_tile_o = linear_output_tile;

  e1_h1_tinyllama_graph_sequencer u_graph_sequencer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(graph_busy),
    .done_o(done_o),
    .slot_valid_o(graph_slot_valid),
    .slot_ready_i(1'b1),
    .launch_control_o(launch_control_o),
    .launch_linear_o(launch_linear_o),
    .op_done_i(graph_op_done),
    .layer_o(active_layer_o),
    .layer_slot_o(active_slot_o),
    .linear_op_index_o(graph_linear_op_index),
    .control_op_index_o(graph_control_op_index),
    .control_kind_o(graph_control_kind),
    .linear_tile_count_o(graph_linear_tile_count),
    .issued_graph_slots_o(issued_graph_slots_o),
    .cycle_phase_o(graph_cycle_phase_o)
  );

  e1_h1_tinyllama_linear_slot_engine #(
    .SmokeMaxTilesPerLinearSlot(SmokeMaxTilesPerLinearSlot)
  ) u_linear_slot_engine (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(launch_linear_o),
    .layer_i(active_layer_o),
    .op_index_i(graph_linear_op_index),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(linear_busy_o),
    .done_o(linear_done),
    .error_o(linear_error),
    .issued_commands_o(linear_slot_issued_commands),
    .expected_commands_o(linear_slot_expected_commands),
    .cycle_phase_o(linear_cycle_phase_o),
    .layer_o(linear_layer),
    .op_index_o(linear_op_index),
    .input_tile_o(linear_input_tile),
    .output_tile_o(linear_output_tile),
    .scheduler_cmd_valid_o(scheduler_cmd_valid),
    .array_cmd_valid_o(array_cmd_valid),
    .array_cmd_ready_o(array_cmd_ready),
    .cmd_input_addr_o(cmd_input_addr),
    .cmd_weight_addr_o(cmd_weight_addr),
    .cmd_output_addr_o(cmd_output_addr),
    .cmd_rows_o(cmd_rows),
    .cmd_cols_o(cmd_cols),
    .cmd_depth_o(cmd_depth),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o)
  );

  e1_h1_tinyllama_control_slot_engine u_control_slot_engine (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(launch_control_o),
    .layer_i(active_layer_o),
    .control_op_index_i(graph_control_op_index),
    .control_kind_i(graph_control_kind),
    .busy_o(control_busy_o),
    .done_o(control_done),
    .control_valid_o(control_valid),
    .control_ready_i(1'b1),
    .control_commit_o(control_commit),
    .layer_o(control_layer),
    .control_op_index_o(control_op_index),
    .control_kind_o(control_kind),
    .issued_control_ops_o(control_slot_issued_ops),
    .cycle_phase_o(control_cycle_phase_o)
  );

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      issued_linear_commands_o <= 32'd0;
      issued_control_ops_o <= 32'd0;
    end else if (start_i && issued_graph_slots_o == 32'd0) begin
      issued_linear_commands_o <= 32'd0;
      issued_control_ops_o <= 32'd0;
    end else begin
      if (graph_cycle_phase_o == 2'd2 && active_is_linear && linear_done) begin
        issued_linear_commands_o <= issued_linear_commands_o + linear_slot_issued_commands;
      end
      if (graph_cycle_phase_o == 2'd2 && !active_is_linear && control_done) begin
        issued_control_ops_o <= issued_control_ops_o + control_slot_issued_ops;
      end
    end
  end

  logic [139:0] unused_debug;
  assign unused_debug = {
    graph_busy,
    graph_slot_valid,
    linear_slot_expected_commands,
    control_valid,
    control_commit,
    buffer_array_data,
    scheduler_cmd_valid,
    control_layer,
    control_op_index,
    control_kind
  };

endmodule

`default_nettype wire
