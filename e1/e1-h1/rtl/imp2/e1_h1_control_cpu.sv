`default_nettype none

module e1_h1_control_cpu (
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

  state_e state_q;
  state_e state_d;

  always_comb begin
    state_d = state_q;
    cmd_valid_o = 1'b0;
    debug_halted_o = 1'b0;

    unique case (state_q)
      StateReset: begin
        state_d = StateIssue;
      end
      StateIssue: begin
        cmd_valid_o = 1'b1;
        if (cmd_ready_i) begin
          state_d = StateWait;
        end
      end
      StateWait: begin
        if (array_done_i || array_error_i) begin
          state_d = StateHalted;
        end
      end
      StateHalted: begin
        debug_halted_o = 1'b1;
      end
      default: begin
        state_d = StateReset;
      end
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateReset;
    end else begin
      state_q <= state_d;
    end
  end

  assign cmd_input_addr_o  = 32'h0001_0000;
  assign cmd_weight_addr_o = 32'h0004_0000;
  assign cmd_output_addr_o = 32'h0008_0000;
  assign cmd_rows_o        = 16'd16;
  assign cmd_cols_o        = 16'd16;
  assign cmd_depth_o       = 16'd16;

endmodule

`default_nettype wire
