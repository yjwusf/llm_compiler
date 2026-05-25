`default_nettype none

module e1_h1_full_checkpoint_module_dpi_linear_tile_engine;
  import "DPI-C" function void e1_h1_full_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_full_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_full_dpi_phase_signal(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );
  import "DPI-C" function int e1_h1_full_dpi_expect_u32(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic expect_u32(input string signal_name, input int cycle, input int expected, input logic [31:0] actual);
    if (e1_h1_full_dpi_expect_u32("linear_tile_engine", signal_name, cycle, expected, int'(actual)) == 0) begin
      $fatal(1, "linear_tile_engine mismatch %s", signal_name);
    end
  endtask

  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);
    if (e1_h1_full_dpi_phase_signal("linear_tile_engine", signal_name, cycle, expected, actual) == 0) begin
      $fatal(1, "linear_tile_engine phase signal mismatch %s", signal_name);
    end
  endtask

  int contract_cycle = 0;

  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_commands_o;
  logic [2:0] cycle_phase_o;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic scheduler_cmd_valid_o;
  logic array_cmd_valid_o;
  logic array_cmd_ready_o;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic [63:0] buffer_array_data_o;
  logic array_done_o;
  logic array_debug_busy_o;
  logic [31:0] array_result_digest_o;

  e1_h1_tinyllama_linear_tile_engine u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .scheduler_cmd_valid_o(scheduler_cmd_valid_o),
    .array_cmd_valid_o(array_cmd_valid_o),
    .array_cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o),
    .array_result_digest_o(array_result_digest_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 8)
      0: return "setup_tile_engine";
      1: return "scheduler_valid_visible";
      2: return "array_command_handshake";
      3: return "latch_to_array_beat_0";
      4: return "latch_to_array_beat_1";
      5: return "latch_to_array_beat_2";
      6: return "array_done_pulse";
      7: return "return_ready";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("linear_tile_engine", "first_four_composed_tile_commands");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 256 && issued_commands_o < 32'd4; cycle++) begin
      if (int'(cycle_phase_o) == (contract_cycle % 8)) begin
        e1_h1_full_dpi_cycle("linear_tile_engine", contract_cycle, phase_name(contract_cycle));
        expect_phase_signal("cycle_phase_o", contract_cycle, contract_cycle % 8, int'(cycle_phase_o));
        contract_cycle++;
      end
      stream_valid_i = 1'b1;
      stream_data_i = 64'h3000 + cycle[15:0];
      tick();
    end
    expect_u32("issued_commands_o", 0, 4, issued_commands_o);
    if (error_o) $fatal(1, "linear tile engine reported error");
    $finish;
  end
endmodule

`default_nettype wire

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
  typedef enum logic [1:0] {StateIdle, StateRun, StateDone, StateError} state_e;
  state_e state_q;
  logic [2:0] phase_q;
  logic [31:0] issued_q;
  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign error_o = state_q == StateError;
  assign cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign cmd_input_addr_o = 32'h0100_0000 + issued_q * 32'd64;
  assign cmd_weight_addr_o = 32'h1000_0000 + issued_q * 32'd64;
  assign cmd_output_addr_o = 32'h3000_0000 + issued_q * 32'd64;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;
  assign layer_o = 32'd0;
  assign op_index_o = 3'd0;
  assign input_tile_o = issued_q[8:0];
  assign output_tile_o = 9'd0;
  assign issued_commands_o = issued_q;
  assign cycle_phase_o = phase_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      phase_q <= 3'd0;
      issued_q <= 32'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            phase_q <= 3'd0;
            issued_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && cmd_ready_i) begin
            issued_q <= issued_q + 32'd1;
          end
          if (phase_q == 3'd6 && array_error_i) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_i) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (issued_q >= 32'd4) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
            end
          end else begin
            phase_q <= phase_q + 3'd1;
          end
        end
        StateDone: begin
          if (!start_i) state_q <= StateIdle;
        end
        default: state_q <= StateIdle;
      endcase
    end
  end
endmodule

`default_nettype wire

`default_nettype none
module e1_h1_stream_sram (
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
  logic unused_inputs;
  assign unused_inputs = clk_i ^ rst_ni ^ stream_last_i ^ stream_error_i;
  assign stream_ready_o = array_ready_i;
  assign array_valid_o = stream_valid_i;
  assign array_data_o = stream_data_i;
endmodule

`default_nettype wire

`default_nettype none
module e1_h1_systolic_array (
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
  logic [3:0] done_shift_q;
  logic [31:0] digest_q;
  logic unused_payload;
  assign cmd_ready_o = 1'b1;
  assign input_ready_o = 1'b1;
  assign done_o = done_shift_q[3];
  assign error_o = 1'b0;
  assign debug_busy_o = |done_shift_q;
  assign result_digest_o = digest_q;
  assign unused_payload = ^{
      cmd_input_addr_i,
      cmd_weight_addr_i,
      cmd_output_addr_i,
      cmd_rows_i,
      cmd_cols_i,
      cmd_depth_i,
      input_valid_i,
      input_data_i
  };
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_shift_q <= 4'b0000;
      digest_q <= '0;
    end else begin
      done_shift_q <= {done_shift_q[2:0], cmd_valid_i};
      if (cmd_valid_i) begin
        digest_q <= cmd_input_addr_i
            ^ cmd_weight_addr_i
            ^ cmd_output_addr_i
            ^ {cmd_rows_i, cmd_cols_i}
            ^ {16'd0, cmd_depth_i};
      end else if (input_valid_i) begin
        digest_q <= {digest_q[30:0], digest_q[31]}
            ^ input_data_i[31:0]
            ^ input_data_i[63:32];
      end
    end
  end
endmodule

`default_nettype wire
