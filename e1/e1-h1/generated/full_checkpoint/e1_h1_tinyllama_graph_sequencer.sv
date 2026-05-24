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

  localparam int unsigned LayerCount = 22;
  localparam int unsigned SlotsPerLayer = 14;
  localparam logic [31:0] TotalGraphSlots = 32'd308;

  typedef enum logic [1:0] {
    StateIdle,
    StateRun,
    StateDone
  } state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [3:0]  layer_slot_q;
  logic [31:0] issued_graph_slots_q;
  logic [1:0]  phase_q;

  function automatic logic [0:0] is_linear_slot(input logic [3:0] slot);
    unique case (slot)
      4'd0: is_linear_slot = 1'd0;
      4'd1: is_linear_slot = 1'd1;
      4'd2: is_linear_slot = 1'd1;
      4'd3: is_linear_slot = 1'd1;
      4'd4: is_linear_slot = 1'd0;
      4'd5: is_linear_slot = 1'd0;
      4'd6: is_linear_slot = 1'd1;
      4'd7: is_linear_slot = 1'd0;
      4'd8: is_linear_slot = 1'd0;
      4'd9: is_linear_slot = 1'd1;
      4'd10: is_linear_slot = 1'd1;
      4'd11: is_linear_slot = 1'd0;
      4'd12: is_linear_slot = 1'd1;
      4'd13: is_linear_slot = 1'd0;
      default: is_linear_slot = 1'd0;
    endcase
  endfunction

  function automatic logic [2:0] linear_index_for(input logic [3:0] slot);
    unique case (slot)
      4'd0: linear_index_for = 3'd0;
      4'd1: linear_index_for = 3'd0;
      4'd2: linear_index_for = 3'd1;
      4'd3: linear_index_for = 3'd2;
      4'd4: linear_index_for = 3'd0;
      4'd5: linear_index_for = 3'd0;
      4'd6: linear_index_for = 3'd3;
      4'd7: linear_index_for = 3'd0;
      4'd8: linear_index_for = 3'd0;
      4'd9: linear_index_for = 3'd4;
      4'd10: linear_index_for = 3'd5;
      4'd11: linear_index_for = 3'd0;
      4'd12: linear_index_for = 3'd6;
      4'd13: linear_index_for = 3'd0;
      default: linear_index_for = 3'd0;
    endcase
  endfunction

  function automatic logic [2:0] control_index_for(input logic [3:0] slot);
    unique case (slot)
      4'd0: control_index_for = 3'd0;
      4'd1: control_index_for = 3'd0;
      4'd2: control_index_for = 3'd0;
      4'd3: control_index_for = 3'd0;
      4'd4: control_index_for = 3'd1;
      4'd5: control_index_for = 3'd2;
      4'd6: control_index_for = 3'd0;
      4'd7: control_index_for = 3'd3;
      4'd8: control_index_for = 3'd4;
      4'd9: control_index_for = 3'd0;
      4'd10: control_index_for = 3'd0;
      4'd11: control_index_for = 3'd5;
      4'd12: control_index_for = 3'd0;
      4'd13: control_index_for = 3'd6;
      default: control_index_for = 3'd0;
    endcase
  endfunction

  function automatic logic [3:0] control_kind_for(input logic [3:0] slot);
    unique case (slot)
      4'd0: control_kind_for = 4'd1;
      4'd1: control_kind_for = 4'd0;
      4'd2: control_kind_for = 4'd0;
      4'd3: control_kind_for = 4'd0;
      4'd4: control_kind_for = 4'd2;
      4'd5: control_kind_for = 4'd3;
      4'd6: control_kind_for = 4'd0;
      4'd7: control_kind_for = 4'd4;
      4'd8: control_kind_for = 4'd1;
      4'd9: control_kind_for = 4'd0;
      4'd10: control_kind_for = 4'd0;
      4'd11: control_kind_for = 4'd5;
      4'd12: control_kind_for = 4'd0;
      4'd13: control_kind_for = 4'd4;
      default: control_kind_for = 4'd0;
    endcase
  endfunction

  function automatic logic [31:0] tile_count_for(input logic [3:0] slot);
    unique case (slot)
      4'd0: tile_count_for = 32'd0;
      4'd1: tile_count_for = 32'd16384;
      4'd2: tile_count_for = 32'd2048;
      4'd3: tile_count_for = 32'd2048;
      4'd4: tile_count_for = 32'd0;
      4'd5: tile_count_for = 32'd0;
      4'd6: tile_count_for = 32'd16384;
      4'd7: tile_count_for = 32'd0;
      4'd8: tile_count_for = 32'd0;
      4'd9: tile_count_for = 32'd45056;
      4'd10: tile_count_for = 32'd45056;
      4'd11: tile_count_for = 32'd0;
      4'd12: tile_count_for = 32'd45056;
      4'd13: tile_count_for = 32'd0;
      default: tile_count_for = 32'd0;
    endcase
  endfunction

  function automatic logic is_last_slot(input logic [31:0] layer, input logic [3:0] slot);
    is_last_slot = layer == (LayerCount - 1) && slot == 4'd13;
  endfunction

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign slot_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign launch_control_o = state_q == StateRun && phase_q == 2'd1 && !is_linear_slot(layer_slot_q);
  assign launch_linear_o = state_q == StateRun && phase_q == 2'd1 && is_linear_slot(layer_slot_q);
  assign layer_o = layer_q;
  assign layer_slot_o = layer_slot_q;
  assign linear_op_index_o = linear_index_for(layer_slot_q);
  assign control_op_index_o = control_index_for(layer_slot_q);
  assign control_kind_o = control_kind_for(layer_slot_q);
  assign linear_tile_count_o = tile_count_for(layer_slot_q);
  assign issued_graph_slots_o = issued_graph_slots_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      layer_slot_q <= 4'd0;
      issued_graph_slots_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= 32'd0;
            layer_slot_q <= 4'd0;
            issued_graph_slots_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !slot_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd2 && !op_done_i) begin
            phase_q <= 2'd2;
          end else if (phase_q == 2'd3) begin
            issued_graph_slots_q <= issued_graph_slots_q + 32'd1;
            if (is_last_slot(layer_q, layer_slot_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 2'd0;
              if (layer_slot_q + 4'd1 < SlotsPerLayer) begin
                layer_slot_q <= layer_slot_q + 4'd1;
              end else begin
                layer_slot_q <= 4'd0;
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

  logic unused_total_graph_slots;
  assign unused_total_graph_slots = TotalGraphSlots[0];

endmodule

`default_nettype wire
