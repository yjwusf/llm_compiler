`default_nettype none

module e1_h1_tinyllama_control_scheduler (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        control_valid_o,
  input  logic        control_ready_i,
  output logic        control_commit_o,
  output logic [31:0] layer_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  layer_op_slot_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] issued_control_ops_o,
  output logic [1:0]  cycle_phase_o
);

  localparam int unsigned LayerCount = 22;
  localparam int unsigned ControlOpsPerLayer = 7;
  localparam logic [31:0] TotalControlOps = 32'd154;

  typedef enum logic [1:0] {
    StateIdle,
    StateRun,
    StateDone
  } state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [2:0]  control_op_index_q;
  logic [31:0] issued_control_ops_q;
  logic [1:0]  phase_q;

  function automatic logic [3:0] control_kind_for(input logic [2:0] control_op_index);
    unique case (control_op_index)
      3'd0: control_kind_for = 4'd1;
      3'd1: control_kind_for = 4'd2;
      3'd2: control_kind_for = 4'd3;
      3'd3: control_kind_for = 4'd4;
      3'd4: control_kind_for = 4'd1;
      3'd5: control_kind_for = 4'd5;
      3'd6: control_kind_for = 4'd4;
      default: control_kind_for = 4'd0;
    endcase
  endfunction

  function automatic logic [3:0] layer_slot_for(input logic [2:0] control_op_index);
    unique case (control_op_index)
      3'd0: layer_slot_for = 4'd0;
      3'd1: layer_slot_for = 4'd4;
      3'd2: layer_slot_for = 4'd5;
      3'd3: layer_slot_for = 4'd7;
      3'd4: layer_slot_for = 4'd8;
      3'd5: layer_slot_for = 4'd11;
      3'd6: layer_slot_for = 4'd13;
      default: layer_slot_for = 4'd0;
    endcase
  endfunction

  function automatic logic is_last_control(
      input logic [31:0] layer,
      input logic [2:0] control_op_index);
    is_last_control = layer == (LayerCount - 1) && control_op_index == 3'd6;
  endfunction

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign control_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign control_commit_o = state_q == StateRun && phase_q == 2'd3;
  assign layer_o = layer_q;
  assign control_op_index_o = control_op_index_q;
  assign layer_op_slot_o = layer_slot_for(control_op_index_q);
  assign control_kind_o = control_kind_for(control_op_index_q);
  assign issued_control_ops_o = issued_control_ops_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      control_op_index_q <= 3'd0;
      issued_control_ops_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= 32'd0;
            control_op_index_q <= 3'd0;
            issued_control_ops_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !control_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd3) begin
            issued_control_ops_q <= issued_control_ops_q + 32'd1;
            if (is_last_control(layer_q, control_op_index_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 2'd0;
              if (control_op_index_q + 3'd1 < ControlOpsPerLayer) begin
                control_op_index_q <= control_op_index_q + 3'd1;
              end else begin
                control_op_index_q <= 3'd0;
                layer_q <= layer_q + 32'd1;
              end
            end
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

  logic unused_total_control_ops;
  assign unused_total_control_ops = TotalControlOps[0];

endmodule

`default_nettype wire
