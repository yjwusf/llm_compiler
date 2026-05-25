`default_nettype none

module e1_h1_full_checkpoint_module_dpi_graph_sequencer;
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
    if (e1_h1_full_dpi_expect_u32("graph_sequencer", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "graph_sequencer mismatch %s", signal_name);
    end
  endtask

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("graph_sequencer", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "graph_sequencer phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic slot_valid_o;
  logic slot_ready_i;
  logic launch_control_o;
  logic launch_linear_o;
  logic op_done_i;
  logic [31:0] layer_o;
  logic [3:0] layer_slot_o;
  logic [2:0] linear_op_index_o;
  logic [2:0] control_op_index_o;
  logic [3:0] control_kind_o;
  logic [31:0] linear_tile_count_o;
  logic [31:0] issued_graph_slots_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_graph_sequencer u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .slot_valid_o(slot_valid_o),
    .slot_ready_i(slot_ready_i),
    .launch_control_o(launch_control_o),
    .launch_linear_o(launch_linear_o),
    .op_done_i(op_done_i),
    .layer_o(layer_o),
    .layer_slot_o(layer_slot_o),
    .linear_op_index_o(linear_op_index_o),
    .control_op_index_o(control_op_index_o),
    .control_kind_o(control_kind_o),
    .linear_tile_count_o(linear_tile_count_o),
    .issued_graph_slots_o(issued_graph_slots_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "present_graph_slot";
      1: return "launch_selected_engine";
      2: return "wait_for_slot_done";
      3: return "commit_graph_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    int linear_launches;
    int control_launches;
    e1_h1_full_dpi_begin("graph_sequencer", "ordered_308_slot_graph");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    slot_ready_i = 1'b1;
    op_done_i = 1'b0;
    linear_launches = 0;
    control_launches = 0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 1600 && !done_o; cycle++) begin
      if (int'(cycle_phase_o) == (contract_cycle % 4)) begin
        e1_h1_full_dpi_cycle("graph_sequencer", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("cycle_phase_o", contract_cycle, contract_cycle % 4, int'(cycle_phase_o));
        contract_cycle++;
      end
      op_done_i = (cycle_phase_o == 2'd2);
      if (launch_linear_o) linear_launches++;
      if (launch_control_o) control_launches++;
      tick();
    end
    expect_u32("issued_graph_slots_o", 0, 308, issued_graph_slots_o);
    expect_u32("linear_launches", 0, 154, linear_launches[31:0]);
    expect_u32("control_launches", 0, 154, control_launches[31:0]);
    if (!done_o) $fatal(1, "graph sequencer did not finish");
    $finish;
  end
endmodule

`default_nettype wire
