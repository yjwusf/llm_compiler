`default_nettype none

module e1_h1_imp1_control_cpu_ref (
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

  state_e state_q, state_d;

  always_comb begin
    state_d = state_q;
    cmd_valid_o = 1'b0;
    debug_halted_o = 1'b0;
    unique case (state_q)
      StateReset: state_d = StateIssue;
      StateIssue: begin
        cmd_valid_o = 1'b1;
        if (cmd_ready_i) state_d = StateWait;
      end
      StateWait: begin
        if (array_done_i || array_error_i) state_d = StateHalted;
      end
      StateHalted: debug_halted_o = 1'b1;
      default: state_d = StateReset;
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) state_q <= StateReset;
    else state_q <= state_d;
  end

  assign cmd_input_addr_o  = 32'h0001_0000;
  assign cmd_weight_addr_o = 32'h0004_0000;
  assign cmd_output_addr_o = 32'h0008_0000;
  assign cmd_rows_o        = 16'd16;
  assign cmd_cols_o        = 16'd16;
  assign cmd_depth_o       = 16'd16;
endmodule

module e1_h1_imp1_systolic_array_ref #(
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
  output logic        debug_busy_o,
  output logic [31:0] result_digest_o
);
  logic [7:0] cycles_q;
  logic       busy_q;
  logic [31:0] result_digest_q;

  assign cmd_ready_o = !busy_q;
  assign input_ready_o = busy_q;
  assign debug_busy_o = busy_q;
  assign done_o = busy_q && (cycles_q == 8'd1);
  assign error_o = 1'b0;
  assign result_digest_o = result_digest_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q <= 1'b0;
      cycles_q <= '0;
      result_digest_q <= '0;
    end else if (!busy_q && cmd_valid_i) begin
      busy_q <= 1'b1;
      cycles_q <= 8'd4;
      result_digest_q <= cmd_input_addr_i
          ^ cmd_weight_addr_i
          ^ cmd_output_addr_i
          ^ {cmd_rows_i, cmd_cols_i}
          ^ {16'd0, cmd_depth_i};
    end else if (busy_q) begin
      if (input_valid_i && cycles_q != 8'd0) begin
        result_digest_q <= {result_digest_q[30:0], result_digest_q[31]}
            ^ input_data_i[31:0]
            ^ input_data_i[63:32]
            ^ {24'd0, cycles_q};
        cycles_q <= cycles_q - 8'd1;
      end
      if (cycles_q == 8'd1 && input_valid_i) busy_q <= 1'b0;
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

module e1_h1_imp1_config_sram_ref #(
  parameter int unsigned SIZE_BYTES = 524288,
  parameter int unsigned DATA_WIDTH = 128,
  parameter int unsigned BANKS = 8
) (
  input logic clk_i,
  input logic rst_ni
);
  logic initialized_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) initialized_q <= 1'b0;
    else initialized_q <= 1'b1;
  end
  localparam int unsigned UnusedConfig = SIZE_BYTES + DATA_WIDTH + BANKS;
endmodule

module e1_h1_imp1_rgmii_ethernet_ingress_ref (
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
