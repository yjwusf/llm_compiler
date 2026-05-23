// SPDX-License-Identifier: Apache-2.0
// Generic DPI smoke probe for E1-H1 implementation-equivalence harnesses.
module e1_h1_imp_equiv_probe;
  import "DPI-C" function void e1_h1_dpi_begin(
    input string ip_name,
    input string vip_case
  );
  import "DPI-C" function int e1_h1_dpi_compare_i(
    input string signal_name,
    input int cycle,
    input int imp1_value,
    input int imp2_value
  );

  initial begin
    e1_h1_dpi_begin("dpi_probe", "self_equivalence");
    if (e1_h1_dpi_compare_i("sample", 0, 32'h1234, 32'h1234) == 0) begin
      $fatal(1, "E1-H1 DPI equivalence compare failed");
    end
  end
endmodule
