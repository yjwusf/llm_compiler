`default_nettype none

module e1_h1_systolic_array #(
  parameter int unsigned ROWS = 16,
  parameter int unsigned COLS = 16,
  parameter int unsigned DATA_WIDTH = 16,
  parameter int unsigned ACCUMULATOR_WIDTH = 32
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        cmd_valid_i,
  output logic        cmd_ready_o,
  input  logic [31:0] cmd_input_addr_i,
  input  logic [31:0] cmd_weight_addr_i,
  input  logic [31:0] cmd_output_addr_i,
  input  logic [15:0] cmd_rows_i,
  input  logic [15:0] cmd_cols_i,
  input  logic [15:0] cmd_depth_i,
  input  logic        input_valid_i,
  output logic        input_ready_o,
  input  logic [63:0] input_data_i,
  output logic        done_o,
  output logic        error_o,
  output logic        debug_busy_o
);

  logic [7:0] beats_remaining_q;
  logic       busy_q;

  assign cmd_ready_o = !busy_q;
  assign input_ready_o = busy_q;
  assign debug_busy_o = busy_q;
  assign done_o = busy_q && (beats_remaining_q == 8'd1);
  assign error_o = 1'b0;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q <= 1'b0;
      beats_remaining_q <= '0;
    end else if (!busy_q && cmd_valid_i) begin
      busy_q <= 1'b1;
      beats_remaining_q <= 8'd4;
    end else if (busy_q) begin
      if (input_valid_i && beats_remaining_q != 8'd0) begin
        beats_remaining_q <= beats_remaining_q - 8'd1;
      end
      if (beats_remaining_q == 8'd1 && input_valid_i) begin
        busy_q <= 1'b0;
      end
    end
  end

  logic [207:0] unused_command;
  assign unused_command = {
    cmd_input_addr_i,
    cmd_weight_addr_i,
    cmd_output_addr_i,
    cmd_rows_i,
    cmd_cols_i,
    cmd_depth_i,
    input_data_i
  };

  localparam int unsigned UnusedConfig =
      ROWS + COLS + DATA_WIDTH + ACCUMULATOR_WIDTH;

endmodule

`default_nettype wire
