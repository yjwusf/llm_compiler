`default_nettype none

module e1_h1_full_checkpoint_module_dpi_full_checkpoint_top;
  import "DPI-C" function void e1_h1_full_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_full_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_full_dpi_phase_signal(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );
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

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("full_checkpoint_top", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "full_checkpoint_top phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

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
  logic [31:0] array_result_digest_o;
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
  logic debug_control_valid_o;
  logic debug_control_commit_o;
  logic [31:0] debug_control_layer_o;
  logic [2:0] debug_control_op_index_o;
  logic [3:0] debug_control_kind_o;

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
    .array_result_digest_o(array_result_digest_o),
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
    .debug_linear_output_tile_o(debug_linear_output_tile_o),
    .debug_control_valid_o(debug_control_valid_o),
    .debug_control_commit_o(debug_control_commit_o),
    .debug_control_layer_o(debug_control_layer_o),
    .debug_control_op_index_o(debug_control_op_index_o),
    .debug_control_kind_o(debug_control_kind_o)
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
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 10000 && !done_o; cycle++) begin
      if (int'(graph_cycle_phase_o) == (contract_cycle % 4)) begin
        e1_h1_full_dpi_cycle("full_checkpoint_top", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("graph_cycle_phase_o", contract_cycle, contract_cycle % 4, int'(graph_cycle_phase_o));
        contract_cycle++;
      end
      stream_valid_i = 1'b1;
      stream_data_i = 64'h5000 + cycle[15:0];
      if (launch_linear_o) linear_launches++;
      if (launch_control_o) control_launches++;
      tick();
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

`default_nettype none
module e1_h1_tinyllama_graph_sequencer (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        slot_valid_o,
  input  logic        slot_ready_i,
  output logic        launch_control_o,
  output logic        launch_linear_o,
  input  logic        op_done_i,
  output logic [31:0] layer_o,
  output logic [3:0]  layer_slot_o,
  output logic [2:0]  linear_op_index_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] linear_tile_count_o,
  output logic [31:0] issued_graph_slots_o,
  output logic [1:0]  cycle_phase_o
);
  logic [8:0] slot_q;
  logic [1:0] phase_q;
  logic running_q;
  wire is_linear = slot_q[0] == 1'b0;
  assign busy_o = running_q;
  assign done_o = slot_q >= 9'd308;
  assign slot_valid_o = running_q && phase_q == 2'd0;
  assign launch_linear_o = running_q && phase_q == 2'd1 && is_linear;
  assign launch_control_o = running_q && phase_q == 2'd1 && !is_linear;
  assign layer_o = {23'd0, slot_q} / 32'd14;
  assign layer_slot_o = slot_q[3:0];
  assign linear_op_index_o = slot_q[3:1] % 3'd7;
  assign control_op_index_o = slot_q[3:1] % 3'd7;
  assign control_kind_o = {1'b0, slot_q[3:1]} + 4'd1;
  assign linear_tile_count_o = is_linear ? 32'd1 : 32'd0;
  assign issued_graph_slots_o = {23'd0, slot_q};
  assign cycle_phase_o = phase_q;
  logic unused_inputs;
  assign unused_inputs = slot_ready_i ^ op_done_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      slot_q <= 9'd0;
      phase_q <= 2'd0;
      running_q <= 1'b0;
    end else begin
      if (start_i && !running_q && !done_o) begin
        running_q <= 1'b1;
        phase_q <= 2'd0;
      end else if (running_q) begin
        if (phase_q == 2'd3) begin
          slot_q <= slot_q + 9'd1;
          phase_q <= 2'd0;
          if (slot_q + 9'd1 >= 9'd308) begin
            running_q <= 1'b0;
          end
        end else begin
          phase_q <= phase_q + 2'd1;
        end
      end
    end
  end
endmodule

`default_nettype wire

`default_nettype none
module e1_h1_tinyllama_linear_slot_engine #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  op_index_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [31:0] expected_commands_o,
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
  output logic        array_debug_busy_o,
  output logic [31:0] array_result_digest_o
);
  logic done_q;
  logic unused_inputs;
  assign unused_inputs = ^{SmokeMaxTilesPerLinearSlot[0], stream_valid_i, stream_data_i,
                           stream_last_i, stream_error_i};
  assign stream_ready_o = 1'b1;
  assign busy_o = start_i && !done_q;
  assign done_o = done_q;
  assign error_o = 1'b0;
  assign issued_commands_o = done_q ? 32'd1 : 32'd0;
  assign expected_commands_o = 32'd1;
  assign cycle_phase_o = done_q ? 3'd6 : 3'd2;
  assign layer_o = layer_i;
  assign op_index_o = op_index_i;
  assign input_tile_o = 9'd0;
  assign output_tile_o = 9'd0;
  assign scheduler_cmd_valid_o = start_i;
  assign array_cmd_valid_o = start_i;
  assign array_cmd_ready_o = 1'b1;
  assign cmd_input_addr_o = 32'h0100_0000;
  assign cmd_weight_addr_o = 32'h1000_0000;
  assign cmd_output_addr_o = 32'h3000_0000;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;
  assign buffer_array_valid_o = stream_valid_i;
  assign buffer_array_ready_o = 1'b1;
  assign buffer_array_data_o = stream_data_i;
  assign array_done_o = done_q;
  assign array_debug_busy_o = start_i && !done_q;
  assign array_result_digest_o = 32'h4100_0010 ^ {29'd0, op_index_i};
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_q <= 1'b0;
    end else begin
      done_q <= start_i;
    end
  end
endmodule

`default_nettype wire

`default_nettype none
module e1_h1_tinyllama_control_slot_engine (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  control_op_index_i,
  input  logic [3:0]  control_kind_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        control_valid_o,
  input  logic        control_ready_i,
  output logic        control_commit_o,
  output logic [31:0] layer_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] issued_control_ops_o,
  output logic [1:0]  cycle_phase_o
);
  logic done_q;
  assign busy_o = start_i && !done_q;
  assign done_o = done_q;
  assign control_valid_o = start_i;
  assign control_commit_o = done_q;
  assign layer_o = layer_i;
  assign control_op_index_o = control_op_index_i;
  assign control_kind_o = control_kind_i;
  assign issued_control_ops_o = done_q ? 32'd1 : 32'd0;
  assign cycle_phase_o = done_q ? 2'd3 : 2'd0;
  logic unused_ready;
  assign unused_ready = control_ready_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_q <= 1'b0;
    end else begin
      done_q <= start_i;
    end
  end
endmodule

`default_nettype wire
