`default_nettype none

module e1_h1_tinyllama_linear_scheduler (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
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
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic [31:0] issued_commands_o,
  output logic [2:0]  cycle_phase_o
);

  localparam int unsigned LayerCount = 22;
  localparam int unsigned LinearOpCount = 7;
  localparam logic [31:0] TotalTileCommands = 32'd3784704;
  localparam logic [31:0] InputBase = 32'h0100_0000;
  localparam logic [31:0] WeightBase = 32'h1000_0000;
  localparam logic [31:0] OutputBase = 32'h3000_0000;
  localparam logic [31:0] LayerInputStride = 32'h0010_0000;
  localparam logic [31:0] LayerWeightStride = 32'h0100_0000;
  localparam logic [31:0] LayerOutputStride = 32'h0010_0000;
  localparam logic [31:0] OpInputStride = 32'h0001_0000;
  localparam logic [31:0] OpWeightStride = 32'h0010_0000;
  localparam logic [31:0] OpOutputStride = 32'h0001_0000;
  localparam logic [31:0] TileBytes = 32'd64;

  typedef enum logic [1:0] {
    StateIdle,
    StateRun,
    StateDone,
    StateError
  } state_e;

  state_e state_q;
  logic [2:0]  phase_q;
  logic [31:0] layer_q;
  logic [2:0]  op_index_q;
  logic [8:0]  input_tile_q;
  logic [8:0]  output_tile_q;
  logic [31:0] issued_commands_q;

  function automatic logic [31:0] zext3(input logic [2:0] value);
    zext3 = {29'd0, value};
  endfunction

  function automatic logic [31:0] zext9(input logic [8:0] value);
    zext9 = {23'd0, value};
  endfunction

  function automatic logic [8:0] input_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
      3'd0: input_tiles_for = 9'd128;
      3'd1: input_tiles_for = 9'd128;
      3'd2: input_tiles_for = 9'd128;
      3'd3: input_tiles_for = 9'd128;
      3'd4: input_tiles_for = 9'd128;
      3'd5: input_tiles_for = 9'd128;
      3'd6: input_tiles_for = 9'd352;
      default: input_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic [8:0] output_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
      3'd0: output_tiles_for = 9'd128;
      3'd1: output_tiles_for = 9'd16;
      3'd2: output_tiles_for = 9'd16;
      3'd3: output_tiles_for = 9'd128;
      3'd4: output_tiles_for = 9'd352;
      3'd5: output_tiles_for = 9'd352;
      3'd6: output_tiles_for = 9'd128;
      default: output_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic is_last_command(
      input logic [31:0] layer,
      input logic [2:0] op_index,
      input logic [8:0] input_tile,
      input logic [8:0] output_tile);
    is_last_command =
        layer == (LayerCount - 1) &&
        op_index == 3'd6 &&
        input_tile == (input_tiles_for(op_index) - 9'd1) &&
        output_tile == (output_tiles_for(op_index) - 9'd1);
  endfunction

  assign cmd_input_addr_o =
      InputBase + layer_q * LayerInputStride + zext3(op_index_q) * OpInputStride +
      zext9(input_tile_q) * TileBytes;
  assign cmd_weight_addr_o =
      WeightBase + layer_q * LayerWeightStride + zext3(op_index_q) * OpWeightStride +
      ((zext9(output_tile_q) * zext9(input_tiles_for(op_index_q))) + zext9(input_tile_q)) *
      TileBytes;
  assign cmd_output_addr_o =
      OutputBase + layer_q * LayerOutputStride + zext3(op_index_q) * OpOutputStride +
      zext9(output_tile_q) * TileBytes;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign error_o = state_q == StateError;
  assign cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign layer_o = layer_q;
  assign op_index_o = op_index_q;
  assign input_tile_o = input_tile_q;
  assign output_tile_o = output_tile_q;
  assign issued_commands_o = issued_commands_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      phase_q <= 3'd0;
      layer_q <= 32'd0;
      op_index_q <= 3'd0;
      input_tile_q <= 9'd0;
      output_tile_q <= 9'd0;
      issued_commands_q <= 32'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            phase_q <= 3'd0;
            layer_q <= 32'd0;
            op_index_q <= 3'd0;
            input_tile_q <= 9'd0;
            output_tile_q <= 9'd0;
            issued_commands_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && !cmd_ready_i) begin
            phase_q <= 3'd2;
          end else if (phase_q == 3'd6 && array_error_i) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_i) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (is_last_command(layer_q, op_index_q, input_tile_q, output_tile_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
              if (zext9(input_tile_q) + 32'd1 < zext9(input_tiles_for(op_index_q))) begin
                input_tile_q <= input_tile_q + 9'd1;
              end else begin
                input_tile_q <= 9'd0;
                if (zext9(output_tile_q) + 32'd1 < zext9(output_tiles_for(op_index_q))) begin
                  output_tile_q <= output_tile_q + 9'd1;
                end else begin
                  output_tile_q <= 9'd0;
                  if (zext3(op_index_q) + 32'd1 < LinearOpCount) begin
                    op_index_q <= op_index_q + 3'd1;
                  end else begin
                    op_index_q <= 3'd0;
                    layer_q <= layer_q + 32'd1;
                  end
                end
              end
            end
          end else begin
            if (phase_q == 3'd2 && cmd_ready_i) begin
              issued_commands_q <= issued_commands_q + 32'd1;
            end
            phase_q <= phase_q + 3'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        StateError: begin
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

  logic unused_total_commands;
  assign unused_total_commands = TotalTileCommands[0];

endmodule

`default_nettype wire
