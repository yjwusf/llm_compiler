`default_nettype none

module e1_h1_full_checkpoint_module_dpi_full_checkpoint_top;
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
    if (e1_h1_full_dpi_expect_u32("full_checkpoint_top", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "full_checkpoint_top mismatch %s", signal_name);
    end
  endtask

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_graph_slots_o;
  logic [31:0] issued_linear_commands_o;
  logic [31:0] issued_control_ops_o;
  logic [31:0] active_layer_o;
  logic [3:0] active_slot_o;
  logic [1:0] graph_cycle_phase_o;
  logic [2:0] linear_cycle_phase_o;
  logic [1:0] control_cycle_phase_o;
  logic launch_linear_o;
  logic launch_control_o;
  logic linear_busy_o;
  logic control_busy_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic array_done_o;
  logic array_debug_busy_o;
  logic debug_scheduler_cmd_valid_o;
  logic debug_array_cmd_valid_o;
  logic debug_array_cmd_ready_o;
  logic [31:0] debug_cmd_input_addr_o;
  logic [31:0] debug_cmd_weight_addr_o;
  logic [31:0] debug_cmd_output_addr_o;
  logic [15:0] debug_cmd_rows_o;
  logic [15:0] debug_cmd_cols_o;
  logic [15:0] debug_cmd_depth_o;
  logic [31:0] debug_linear_layer_o;
  logic [2:0] debug_linear_op_index_o;
  logic [8:0] debug_linear_input_tile_o;
  logic [8:0] debug_linear_output_tile_o;

  e1_h1_tinyllama_full_checkpoint_top #(
    .SmokeMaxTilesPerLinearSlot(1)
  ) u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_graph_slots_o(issued_graph_slots_o),
    .issued_linear_commands_o(issued_linear_commands_o),
    .issued_control_ops_o(issued_control_ops_o),
    .active_layer_o(active_layer_o),
    .active_slot_o(active_slot_o),
    .graph_cycle_phase_o(graph_cycle_phase_o),
    .linear_cycle_phase_o(linear_cycle_phase_o),
    .control_cycle_phase_o(control_cycle_phase_o),
    .launch_linear_o(launch_linear_o),
    .launch_control_o(launch_control_o),
    .linear_busy_o(linear_busy_o),
    .control_busy_o(control_busy_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o),
    .debug_scheduler_cmd_valid_o(debug_scheduler_cmd_valid_o),
    .debug_array_cmd_valid_o(debug_array_cmd_valid_o),
    .debug_array_cmd_ready_o(debug_array_cmd_ready_o),
    .debug_cmd_input_addr_o(debug_cmd_input_addr_o),
    .debug_cmd_weight_addr_o(debug_cmd_weight_addr_o),
    .debug_cmd_output_addr_o(debug_cmd_output_addr_o),
    .debug_cmd_rows_o(debug_cmd_rows_o),
    .debug_cmd_cols_o(debug_cmd_cols_o),
    .debug_cmd_depth_o(debug_cmd_depth_o),
    .debug_linear_layer_o(debug_linear_layer_o),
    .debug_linear_op_index_o(debug_linear_op_index_o),
    .debug_linear_input_tile_o(debug_linear_input_tile_o),
    .debug_linear_output_tile_o(debug_linear_output_tile_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "present_top_graph_slot";
      1: return "start_selected_slot_engine";
      2: return "run_selected_slot_engine";
      3: return "commit_top_graph_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    int linear_launches;
    int control_launches;
    e1_h1_full_dpi_begin("full_checkpoint_top", "bounded_all_308_graph_slots");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    linear_launches = 0;
    control_launches = 0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    for (int cycle = 0; cycle < 10000 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("full_checkpoint_top", cycle, phase_name(cycle));
      stream_valid_i = 1'b1;
      stream_data_i = 64'h5000 + cycle[15:0];
      if (launch_linear_o) linear_launches++;
      if (launch_control_o) control_launches++;
      tick();
      start_i = 1'b0;
    end
    expect_u32("issued_graph_slots_o", 0, 308, issued_graph_slots_o);
    expect_u32("issued_linear_commands_o", 0, 154, issued_linear_commands_o);
    expect_u32("issued_control_ops_o", 0, 154, issued_control_ops_o);
    expect_u32("linear_launches", 0, 154, linear_launches[31:0]);
    expect_u32("control_launches", 0, 154, control_launches[31:0]);
    if (!done_o) $fatal(1, "full checkpoint top did not finish");
    if (error_o) $fatal(1, "full checkpoint top reported error");
    $finish;
  end
endmodule

`default_nettype wire
