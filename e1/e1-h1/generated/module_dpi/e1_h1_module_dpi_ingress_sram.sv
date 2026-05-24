`default_nettype none

module e1_h1_module_dpi_ingress_sram;
  import "DPI-C" function void e1_h1_module_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_module_dpi_compare_u32(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );

  logic clk_i;
  logic rst_ni;
  logic stream_valid_i;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic array_ready_i;
  logic stream_ready_imp1, stream_ready_imp2;
  logic array_valid_imp1, array_valid_imp2;
  logic [63:0] array_data_imp1, array_data_imp2;

  e1_h1_imp1_stream_sram_ref u_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_imp1),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(array_valid_imp1),
    .array_ready_i(array_ready_i),
    .array_data_o(array_data_imp1)
  );

  e1_h1_stream_sram u_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_imp2),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(array_valid_imp2),
    .array_ready_i(array_ready_i),
    .array_data_o(array_data_imp2)
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic check32(input string name, input int cycle, input logic [31:0] imp1, input logic [31:0] imp2);
    if (e1_h1_module_dpi_compare_u32(name, cycle, int'(imp1), int'(imp2)) == 0) begin
      $fatal(1, "ingress_sram mismatch %s cycle %0d", name, cycle);
    end
  endtask

  task automatic check_outputs(input int cycle);
    check32("stream_ready_o", cycle, {31'd0, stream_ready_imp1}, {31'd0, stream_ready_imp2});
    check32("array_valid_o", cycle, {31'd0, array_valid_imp1}, {31'd0, array_valid_imp2});
    check32("array_data_o.lo", cycle, array_data_imp1[31:0], array_data_imp2[31:0]);
    check32("array_data_o.hi", cycle, array_data_imp1[63:32], array_data_imp2[63:32]);
  endtask

  initial begin
    e1_h1_module_dpi_begin("ingress_sram", "module_only_latched_buffer");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    array_ready_i = 1'b0;
    tick();
    rst_ni = 1'b1;
    for (int cycle = 0; cycle < 6; cycle++) begin
      e1_h1_module_dpi_cycle("ingress_sram", cycle, "drive_latch_boundary");
      stream_valid_i = (cycle == 0 || cycle == 3 || cycle == 4);
      stream_data_i = 64'h2000 + cycle[7:0];
      stream_last_i = (cycle == 4);
      stream_error_i = (cycle == 4);
      array_ready_i = (cycle >= 2);
      tick();
      e1_h1_module_dpi_cycle("ingress_sram", cycle, "sample_latched_output");
      check_outputs(cycle);
    end
    $finish;
  end
endmodule

`default_nettype wire
