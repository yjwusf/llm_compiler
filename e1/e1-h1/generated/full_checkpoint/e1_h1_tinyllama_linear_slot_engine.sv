`default_nettype none

module e1_h1_tinyllama_linear_slot_engine #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  op_index_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [31:0] expected_commands_o,
  output logic [2:0]  cycle_phase_o,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic        scheduler_cmd_valid_o,
  output logic        array_cmd_valid_o,
  output logic        array_cmd_ready_o,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic [63:0] buffer_array_data_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o
);

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
  localparam logic [31:0] SmokeMaxTiles = 32'(SmokeMaxTilesPerLinearSlot);

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
  logic        array_error;

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

  function automatic logic [31:0] natural_commands_for(input logic [2:0] op_index);
    natural_commands_for = zext9(input_tiles_for(op_index)) * zext9(output_tiles_for(op_index));
  endfunction

  function automatic logic [31:0] effective_commands_for(input logic [2:0] op_index);
    logic [31:0] natural_commands;
    natural_commands = natural_commands_for(op_index);
    if (SmokeMaxTiles != 32'd0 && SmokeMaxTiles < natural_commands) begin
      effective_commands_for = SmokeMaxTiles;
    end else begin
      effective_commands_for = natural_commands;
    end
  endfunction

  function automatic logic is_last_natural_command(
      input logic [2:0] op_index,
      input logic [8:0] input_tile,
      input logic [8:0] output_tile);
    is_last_natural_command =
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
  assign scheduler_cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign array_cmd_valid_o = scheduler_cmd_valid_o && phase_q == 3'd2;
  assign issued_commands_o = issued_commands_q;
  assign expected_commands_o = effective_commands_for(op_index_q);
  assign cycle_phase_o = phase_q;
  assign layer_o = layer_q;
  assign op_index_o = op_index_q;
  assign input_tile_o = input_tile_q;
  assign output_tile_o = output_tile_q;

  e1_h1_stream_sram u_latch_buffer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(buffer_array_valid_o),
    .array_ready_i(buffer_array_ready_o),
    .array_data_o(buffer_array_data_o)
  );

  e1_h1_systolic_array u_systolic_array (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_o),
    .cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_i(cmd_input_addr_o),
    .cmd_weight_addr_i(cmd_weight_addr_o),
    .cmd_output_addr_i(cmd_output_addr_o),
    .cmd_rows_i(cmd_rows_o),
    .cmd_cols_i(cmd_cols_o),
    .cmd_depth_i(cmd_depth_o),
    .input_valid_i(buffer_array_valid_o),
    .input_ready_o(buffer_array_ready_o),
    .input_data_i(buffer_array_data_o),
    .done_o(array_done_o),
    .error_o(array_error),
    .debug_busy_o(array_debug_busy_o)
  );

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
            layer_q <= layer_i;
            op_index_q <= op_index_i;
            input_tile_q <= 9'd0;
            output_tile_q <= 9'd0;
            issued_commands_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && !array_cmd_ready_o) begin
            phase_q <= 3'd2;
          end else if (phase_q == 3'd6 && array_error) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_o) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (issued_commands_q >= effective_commands_for(op_index_q) ||
                is_last_natural_command(op_index_q, input_tile_q, output_tile_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
              if (zext9(input_tile_q) + 32'd1 < zext9(input_tiles_for(op_index_q))) begin
                input_tile_q <= input_tile_q + 9'd1;
              end else begin
                input_tile_q <= 9'd0;
                output_tile_q <= output_tile_q + 9'd1;
              end
            end
          end else begin
            if (phase_q == 3'd2 && array_cmd_ready_o) begin
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

endmodule

`default_nettype wire
