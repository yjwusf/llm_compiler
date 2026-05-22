# Codex Assumptions

This file records details that Codex may assume while implementing the project.
Humans may review this file sporadically. Once an assumption becomes part of
the stable project contract, move it into `docs/l1/`.

## Current Assumptions

- StableHLO-compatible MLIR is the first frontend target.
- JSON is the first architecture configuration format.
- Generated SoC tops are manifest-driven. The Wujian100-style reference means
  a visible named-IP top-level composition, not importing Wujian100 RTL.
- SystemVerilog source will eventually use a matching `rtl/l1/`, `rtl/l2/`,
  and `rtl/l3/` directory layout.
- Initial SystemVerilog modules are allowed to be mocks if their interfaces,
  C++ behavior, Verilator path, and module VIP are documented.
- The C++ model is the behavioral reference for mocked modules.
- L1.5 harnesses use C++ models or mocks for every component surrounding the
  single SystemVerilog module under test.
- Initial performance counters are collected in C++ harness code.
- Valid/ready streaming interfaces are the default for tensor-like data unless
  a module document says otherwise.
- External model data enters through Ethernet over RGMII by default.
- The RGMII PHY is external to generated RTL. The repository models only the
  digital MAC-side RGMII boundary.
- Off-chip DRAM is not the default source of input model data.
- RGMII and Ethernet verification are digital-only and do not require
  mixed-signal simulation.
- Pipeline length is configurable through JSON and lowered into SystemVerilog
  parameters.
- SRAM sizing is configurable through JSON and lowered into SystemVerilog
  parameters or target-specific memory macro bindings.
- FPGA and ASIC/OpenROAD targets share architecture semantics and differ only
  in backend packaging, constraints, and implementation-specific bindings.
- Formal verification is planned after module-level C++/VIP/Verilator tests are
  established.

## Assumption Rules

- Do not hide behavior in code comments only. Add behavior-affecting
  assumptions here or in L1.
- If an assumption changes a module interface, update the L3 module document.
- If a test relies on an assumption, reference this file or the promoted L1
  section in the test name, fixture name, or test metadata.
