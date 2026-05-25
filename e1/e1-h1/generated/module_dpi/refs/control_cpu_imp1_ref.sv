`default_nettype none

module e1_h1_imp1_control_cpu_ref (
  input  logic        clk_i,
  input  logic        rst_ni,
  output logic        cmd_valid_o,
  input  logic        cmd_ready_i,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  input  logic        array_done_i,
  input  logic        array_error_i,
  output logic        debug_halted_o
);
  typedef enum logic [1:0] {
    StateReset,
    StateIssue,
    StateWait,
    StateHalted
  } state_e;

  state_e state_q, state_d;

  always_comb begin
    state_d = state_q;
    cmd_valid_o = 1'b0;
    debug_halted_o = 1'b0;
    unique case (state_q)
      StateReset: state_d = StateIssue;
      StateIssue: begin
        cmd_valid_o = 1'b1;
        if (cmd_ready_i) state_d = StateWait;
      end
      StateWait: begin
        if (array_done_i || array_error_i) state_d = StateHalted;
      end
      StateHalted: debug_halted_o = 1'b1;
      default: state_d = StateReset;
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) state_q <= StateReset;
    else state_q <= state_d;
  end

  assign cmd_input_addr_o  = 32'h0001_0000;
  assign cmd_weight_addr_o = 32'h0004_0000;
  assign cmd_output_addr_o = 32'h0008_0000;
  assign cmd_rows_o        = 16'd16;
  assign cmd_cols_o        = 16'd16;
  assign cmd_depth_o       = 16'd16;
endmodule

`default_nettype wire
