`default_nettype none

module e1_h1_config_sram #(
  parameter int unsigned SIZE_BYTES = 524288,
  parameter int unsigned DATA_WIDTH = 128,
  parameter int unsigned BANKS = 8
) (
  input logic clk_i,
  input logic rst_ni
);

  logic initialized_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      initialized_q <= 1'b0;
    end else begin
      initialized_q <= 1'b1;
    end
  end

  localparam int unsigned UnusedConfig = SIZE_BYTES + DATA_WIDTH + BANKS;

endmodule

`default_nettype wire
