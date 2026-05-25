`default_nettype none

module e1_h1_full_checkpoint_module_dpi_control_scheduler;
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
    if (e1_h1_full_dpi_expect_u32("control_scheduler", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "control_scheduler mismatch %s", signal_name);
    end
  endtask

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("control_scheduler", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "control_scheduler phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic control_valid_o;
  logic control_ready_i;
  logic control_commit_o;
  logic [31:0] layer_o;
  logic [2:0] control_op_index_o;
  logic [3:0] layer_op_slot_o;
  logic [3:0] control_kind_o;
  logic [31:0] issued_control_ops_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_control_scheduler u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .control_valid_o(control_valid_o),
    .control_ready_i(control_ready_i),
    .control_commit_o(control_commit_o),
    .layer_o(layer_o),
    .control_op_index_o(control_op_index_o),
    .layer_op_slot_o(layer_op_slot_o),
    .control_kind_o(control_kind_o),
    .issued_control_ops_o(issued_control_ops_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "issue_control_op";
      1: return "read_control_metadata";
      2: return "execute_control_op";
      3: return "commit_control_op";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("control_scheduler", "all_154_control_ops");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    control_ready_i = 1'b1;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 900 && !done_o; cycle++) begin
      if (int'(cycle_phase_o) == (contract_cycle % 4)) begin
        e1_h1_full_dpi_cycle("control_scheduler", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("cycle_phase_o", contract_cycle, contract_cycle % 4, int'(cycle_phase_o));
        contract_cycle++;
      end
      tick();
    end
    expect_u32("issued_control_ops_o", 0, 154, issued_control_ops_o);
    if (!done_o) $fatal(1, "control scheduler did not finish");
    $finish;
  end
endmodule

`default_nettype wire
