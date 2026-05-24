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

  typedef enum logic [1:0] {
    StateIdle,
    StateRun,
    StateDone
  } state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [2:0]  control_op_index_q;
  logic [3:0]  control_kind_q;
  logic [31:0] issued_control_ops_q;
  logic [1:0]  phase_q;

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign control_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign control_commit_o = state_q == StateRun && phase_q == 2'd3;
  assign layer_o = layer_q;
  assign control_op_index_o = control_op_index_q;
  assign control_kind_o = control_kind_q;
  assign issued_control_ops_o = issued_control_ops_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      control_op_index_q <= 3'd0;
      control_kind_q <= 4'd0;
      issued_control_ops_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= layer_i;
            control_op_index_q <= control_op_index_i;
            control_kind_q <= control_kind_i;
            issued_control_ops_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !control_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd3) begin
            issued_control_ops_q <= 32'd1;
            state_q <= StateDone;
          end else begin
            phase_q <= phase_q + 2'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

endmodule

`default_nettype wire
