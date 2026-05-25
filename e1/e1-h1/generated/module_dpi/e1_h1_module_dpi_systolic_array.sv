`default_nettype none

module e1_h1_module_dpi_systolic_array;
  import "DPI-C" function void e1_h1_module_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_case(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_module_dpi_phase_signal(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );
  import "DPI-C" function int e1_h1_module_dpi_compare_u32(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );
  import "DPI-C" function int e1_h1_module_dpi_expect_systolic_digest(
    input int cycle,
    input int actual_value
  );

  logic [7:0] probe_cycle_phase_o;
  logic clk_i;
  logic rst_ni;
  logic cmd_valid_i;
  logic [31:0] cmd_input_addr_i;
  logic [31:0] cmd_weight_addr_i;
  logic [31:0] cmd_output_addr_i;
  logic [15:0] cmd_rows_i;
  logic [15:0] cmd_cols_i;
  logic [15:0] cmd_depth_i;
  logic input_valid_i;
  logic [63:0] input_data_i;
  logic cmd_ready_imp1, cmd_ready_imp2;
  logic input_ready_imp1, input_ready_imp2;
  logic done_imp1, done_imp2;
  logic error_imp1, error_imp2;
  logic debug_busy_imp1, debug_busy_imp2;
  logic [31:0] result_digest_imp1, result_digest_imp2;

  e1_h1_imp1_systolic_array_ref u_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(cmd_valid_i),
    .cmd_ready_o(cmd_ready_imp1),
    .cmd_input_addr_i(cmd_input_addr_i),
    .cmd_weight_addr_i(cmd_weight_addr_i),
    .cmd_output_addr_i(cmd_output_addr_i),
    .cmd_rows_i(cmd_rows_i),
    .cmd_cols_i(cmd_cols_i),
    .cmd_depth_i(cmd_depth_i),
    .input_valid_i(input_valid_i),
    .input_ready_o(input_ready_imp1),
    .input_data_i(input_data_i),
    .done_o(done_imp1),
    .error_o(error_imp1),
    .debug_busy_o(debug_busy_imp1),
    .result_digest_o(result_digest_imp1)
  );

  e1_h1_systolic_array u_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(cmd_valid_i),
    .cmd_ready_o(cmd_ready_imp2),
    .cmd_input_addr_i(cmd_input_addr_i),
    .cmd_weight_addr_i(cmd_weight_addr_i),
    .cmd_output_addr_i(cmd_output_addr_i),
    .cmd_rows_i(cmd_rows_i),
    .cmd_cols_i(cmd_cols_i),
    .cmd_depth_i(cmd_depth_i),
    .input_valid_i(input_valid_i),
    .input_ready_o(input_ready_imp2),
    .input_data_i(input_data_i),
    .done_o(done_imp2),
    .error_o(error_imp2),
    .debug_busy_o(debug_busy_imp2),
    .result_digest_o(result_digest_imp2)
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic check1(input string name, input int cycle, input logic imp1, input logic imp2);
    if (e1_h1_module_dpi_compare_u32(name, cycle, int'({31'd0, imp1}), int'({31'd0, imp2})) == 0) begin
      $fatal(1, "systolic_array mismatch %s cycle %0d", name, cycle);
    end
  endtask

  task automatic check32(input string name, input int cycle, input logic [31:0] imp1, input logic [31:0] imp2);
    if (e1_h1_module_dpi_compare_u32(name, cycle, int'(imp1), int'(imp2)) == 0) begin
      $fatal(1, "systolic_array mismatch %s cycle %0d", name, cycle);
    end
  endtask

  task automatic check_outputs(input int cycle);
    check1("cmd_ready_o", cycle, cmd_ready_imp1, cmd_ready_imp2);
    check1("input_ready_o", cycle, input_ready_imp1, input_ready_imp2);
    check1("done_o", cycle, done_imp1, done_imp2);
    check1("error_o", cycle, error_imp1, error_imp2);
    check1("debug_busy_o", cycle, debug_busy_imp1, debug_busy_imp2);
    check32("result_digest_o", cycle, result_digest_imp1, result_digest_imp2);
    if (e1_h1_module_dpi_expect_systolic_digest(cycle, int'(result_digest_imp2)) == 0) begin
      $fatal(1, "systolic_array digest mismatch cycle %0d", cycle);
    end
  endtask

  function automatic string phase_name(input int cycle);
    case (cycle)
      0: return "array_idle";
      1: return "accept_array_command";
      2: return "enter_busy";
      3: return "consume_input_beat_0";
      4: return "consume_input_beat_1";
      5: return "consume_input_beat_2";
      6: return "completion_pulse";
      7: return "return_ready";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_module_dpi_begin("systolic_array", "module_only_systolic_array");
    e1_h1_module_dpi_case("systolic_array", "command_acceptance");
    e1_h1_module_dpi_case("systolic_array", "input_burst");
    e1_h1_module_dpi_case("systolic_array", "completion_pulse");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    cmd_valid_i = 1'b0;
    cmd_input_addr_i = 32'h0001_0000;
    cmd_weight_addr_i = 32'h0004_0000;
    cmd_output_addr_i = 32'h0008_0000;
    cmd_rows_i = 16'd16;
    cmd_cols_i = 16'd16;
    cmd_depth_i = 16'd16;
    input_valid_i = 1'b0;
    input_data_i = 64'd0;
    tick();
    rst_ni = 1'b1;
    for (int cycle = 0; cycle < 8; cycle++) begin
      probe_cycle_phase_o = cycle[7:0];
      e1_h1_module_dpi_cycle("systolic_array", cycle, phase_name(cycle));
      if (e1_h1_module_dpi_phase_signal("systolic_array", "probe_cycle_phase_o", cycle, cycle, int'(probe_cycle_phase_o)) == 0) begin
        $fatal(1, "systolic_array phase signal mismatch cycle %0d", cycle);
      end
      cmd_valid_i = (cycle == 1);
      input_valid_i = (cycle >= 3 && cycle <= 6);
      input_data_i = 64'h1000 + cycle[7:0];
      tick();
      check_outputs(cycle);
    end
    $finish;
  end
endmodule

`default_nettype wire
