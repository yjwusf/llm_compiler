`default_nettype none

module e1_h1_full_checkpoint_module_dpi_linear_scheduler;
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
    if (e1_h1_full_dpi_expect_u32("linear_scheduler", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "linear_scheduler mismatch %s", signal_name);
    end
  endtask

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("linear_scheduler", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "linear_scheduler phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic cmd_valid_o;
  logic cmd_ready_i;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic array_done_i;
  logic array_error_i;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic [31:0] issued_commands_o;
  logic [2:0] cycle_phase_o;

  e1_h1_tinyllama_linear_scheduler u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .cmd_valid_o(cmd_valid_o),
    .cmd_ready_i(cmd_ready_i),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .array_done_i(array_done_i),
    .array_error_i(array_error_i),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 8)
      0: return "setup_tile_command";
      1: return "assert_scheduler_valid";
      2: return "accept_command_handshake";
      3: return "wait_for_array_progress_0";
      4: return "wait_for_array_progress_1";
      5: return "wait_for_array_progress_2";
      6: return "sample_array_done";
      7: return "advance_tile_counters";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("linear_scheduler", "first_four_tile_commands");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    cmd_ready_i = 1'b1;
    array_done_i = 1'b0;
    array_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 128 && issued_commands_o < 32'd4; cycle++) begin
      if (int'(cycle_phase_o) == (contract_cycle % 8)) begin
        e1_h1_full_dpi_cycle("linear_scheduler", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("cycle_phase_o", contract_cycle, contract_cycle % 8, int'(cycle_phase_o));
        contract_cycle++;
      end
      array_done_i = (cycle_phase_o == 3'd6);
      tick();
      array_done_i = 1'b0;
    end
    expect_u32("issued_commands_o", 0, 4, issued_commands_o);
    expect_u32("cmd_rows_o", 0, 16, {16'd0, cmd_rows_o});
    expect_u32("cmd_cols_o", 0, 16, {16'd0, cmd_cols_o});
    expect_u32("cmd_depth_o", 0, 16, {16'd0, cmd_depth_o});
    if (error_o) $fatal(1, "linear scheduler reported error");
    $finish;
  end
endmodule

`default_nettype wire
