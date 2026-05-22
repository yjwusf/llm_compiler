`default_nettype none

module e1_h1_rgmii_ethernet_ingress (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        rgmii_rx_clk_i,
  input  logic [3:0]  rgmii_rxd_i,
  input  logic        rgmii_rx_ctl_i,
  output logic        stream_valid_o,
  input  logic        stream_ready_i,
  output logic [63:0] stream_data_o,
  output logic        stream_last_o,
  output logic        stream_error_o
);

  logic [63:0] data_q;
  logic        valid_q;

  always_ff @(posedge rgmii_rx_clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      data_q <= '0;
      valid_q <= 1'b0;
    end else if (rgmii_rx_ctl_i) begin
      data_q <= {data_q[59:0], rgmii_rxd_i};
      valid_q <= 1'b1;
    end else if (stream_ready_i) begin
      valid_q <= 1'b0;
    end
  end

  assign stream_valid_o = valid_q;
  assign stream_data_o = data_q;
  assign stream_last_o = valid_q && !rgmii_rx_ctl_i;
  assign stream_error_o = 1'b0;

  logic unused_clk;
  assign unused_clk = clk_i;

endmodule

`default_nettype wire
