`default_nettype none

module e1_h1_imp1_stream_sram_ref #(
  parameter int unsigned SIZE_BYTES = 262144,
  parameter int unsigned DATA_WIDTH = 128,
  parameter int unsigned BANKS = 4
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        array_valid_o,
  input  logic        array_ready_i,
  output logic [63:0] array_data_o
);
  logic        valid_q;
  logic [63:0] data_q;

  assign stream_ready_o = !valid_q || array_ready_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      valid_q <= 1'b0;
      data_q <= '0;
    end else begin
      if (stream_ready_o && stream_valid_i) begin
        valid_q <= !stream_error_i;
        data_q <= stream_data_i;
      end else if (array_ready_i) begin
        valid_q <= 1'b0;
      end
    end
  end

  assign array_valid_o = valid_q;
  assign array_data_o = data_q;
  logic unused_last;
  assign unused_last = stream_last_i;
  localparam int unsigned UnusedConfig = SIZE_BYTES + DATA_WIDTH + BANKS;
endmodule

`default_nettype wire
