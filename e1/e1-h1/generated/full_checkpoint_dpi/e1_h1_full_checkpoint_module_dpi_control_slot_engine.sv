`default_nettype none

module e1_h1_full_checkpoint_module_dpi_control_slot_engine;
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
    if (e1_h1_full_dpi_expect_u32("control_slot_engine", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "control_slot_engine mismatch %s", signal_name);
    end
  endtask

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("control_slot_engine", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "control_slot_engine phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic [31:0] layer_i;
  logic [2:0] control_op_index_i;
  logic [3:0] control_kind_i;
  logic busy_o;
  logic done_o;
  logic control_valid_o;
  logic control_ready_i;
  logic control_commit_o;
  logic [31:0] layer_o;
  logic [2:0] control_op_index_o;
  logic [3:0] control_kind_o;
  logic [31:0] issued_control_ops_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_control_slot_engine u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .layer_i(layer_i),
    .control_op_index_i(control_op_index_i),
    .control_kind_i(control_kind_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .control_valid_o(control_valid_o),
    .control_ready_i(control_ready_i),
    .control_commit_o(control_commit_o),
    .layer_o(layer_o),
    .control_op_index_o(control_op_index_o),
    .control_kind_o(control_kind_o),
    .issued_control_ops_o(issued_control_ops_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "issue_selected_control_slot";
      1: return "read_selected_control_metadata";
      2: return "execute_selected_control_slot";
      3: return "commit_selected_control_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("control_slot_engine", "single_control_slot");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    layer_i = 32'd3;
    control_op_index_i = 3'd2;
    control_kind_i = 4'd3;
    control_ready_i = 1'b1;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 16 && !done_o; cycle++) begin
      if (int'(cycle_phase_o) == (contract_cycle % 4)) begin
        e1_h1_full_dpi_cycle("control_slot_engine", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("cycle_phase_o", contract_cycle, contract_cycle % 4, int'(cycle_phase_o));
        contract_cycle++;
      end
      control_ready_i = 1'b1;
      tick();
    end
    expect_u32("issued_control_ops_o", 0, 1, issued_control_ops_o);
    expect_u32("layer_o", 0, 3, layer_o);
    expect_u32("control_op_index_o", 0, 2, {29'd0, control_op_index_o});
    expect_u32("control_kind_o", 0, 3, {28'd0, control_kind_o});
    if (!done_o) $fatal(1, "control slot engine did not finish");
    $finish;
  end
endmodule

`default_nettype wire
