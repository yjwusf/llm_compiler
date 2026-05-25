`default_nettype none

module e1_h1_module_dpi_accumulator_sram;
  import "DPI-C" function void e1_h1_module_dpi_begin(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_case(input string module_name, input string vip_case);
  import "DPI-C" function void e1_h1_module_dpi_cycle(input string module_name, input int cycle, input string phase);
  import "DPI-C" function int e1_h1_module_dpi_phase_signal(
    input string module_name,
    input string signal_name,
    input int cycle,
    input int expected,
    input int actual
  );
  import "DPI-C" function int e1_h1_module_dpi_compare_u32(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );

  logic [7:0] probe_cycle_phase_o;
  logic clk_i;
  logic rst_ni;

  e1_h1_imp1_config_sram_ref #(
    .SIZE_BYTES(524288),
    .DATA_WIDTH(256),
    .BANKS(8)
  ) u_imp1 (
    .clk_i(clk_i),
    .rst_ni(rst_ni)
  );

  e1_h1_config_sram #(
    .SIZE_BYTES(524288),
    .DATA_WIDTH(256),
    .BANKS(8)
  ) u_imp2 (
    .clk_i(clk_i),
    .rst_ni(rst_ni)
  );

  task automatic tick;
    clk_i = 1'b0; #1;
    clk_i = 1'b1; #1;
  endtask

  task automatic check_initialized(input int cycle);
    if (e1_h1_module_dpi_compare_u32("initialized_q", cycle, int'({31'd0, u_imp1.initialized_q}), int'({31'd0, u_imp2.initialized_q})) == 0) begin
      $fatal(1, "accumulator_sram mismatch initialized_q cycle %0d", cycle);
    end
  endtask

  function automatic string phase_name(input int cycle);
    case (cycle)
      0: return "initialization_latch";
      1: return "initialized_hold_0";
      2: return "initialized_hold_1";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_module_dpi_begin("accumulator_sram", "module_only_config_sram");
    e1_h1_module_dpi_case("accumulator_sram", "reset_empty");
    e1_h1_module_dpi_case("accumulator_sram", "single_config_read");
    e1_h1_module_dpi_case("accumulator_sram", "wide_accumulator_word");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    tick();
    check_initialized(-1);
    rst_ni = 1'b1;
    for (int cycle = 0; cycle < 3; cycle++) begin
      probe_cycle_phase_o = cycle[7:0];
      e1_h1_module_dpi_cycle("accumulator_sram", cycle, phase_name(cycle));
      if (e1_h1_module_dpi_phase_signal("accumulator_sram", "probe_cycle_phase_o", cycle, cycle, int'(probe_cycle_phase_o)) == 0) begin
        $fatal(1, "accumulator_sram phase signal mismatch cycle %0d", cycle);
      end
      tick();
      check_initialized(cycle);
    end
    $finish;
  end
endmodule

`default_nettype wire
