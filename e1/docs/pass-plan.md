# E1 End-To-End Pass Plan

This plan maps the E1 TinyLlama experiment from frontend capture to generated
hardware and device code.

## Passes

1. `e1_fetch_model`
   - Input: pinned TinyLlama manifest.
   - Output: local model cache entry and checksum record.
2. `e1_export_stablehlo`
   - Input: cached TinyLlama model.
   - Output: StableHLO-compatible MLIR and frontend diagnostics.
3. `e1_inspect_stablehlo`
   - Input: StableHLO MLIR.
   - Output: operation, shape, dtype, layout, and unsupported-op reports.
4. `e1_normalize_stablehlo`
   - Input: StableHLO MLIR.
   - Output: normalized MLIR using the subset supported by this compiler.
5. `e1_bind_e1_h1`
   - Input: normalized MLIR and `e1/e1-h1/config/architecture.json`.
   - Output: model operations bound to CPU, SRAM, Ethernet ingress, and
     systolic-array resources.
6. `e1_plan_memory`
   - Input: bound operations and E1-H1 SRAM configuration.
   - Output: on-chip SRAM allocation, tile plan, and movement schedule.
7. `e1_plan_device_program`
   - Input: operation binding and memory plan.
   - Output: legible device program under `e1/code/program/`.
8. `e1_generate_chip_model`
   - Input: architecture config, pass metadata, and module docs.
   - Output: C++ generated chip model under `e1/code/chip_model/`.
9. `e1_generate_l1_5_harnesses`
   - Input: module specs and C++ chip model.
   - Output: one hybrid run per SystemVerilog module, with all other behavior
     supplied by C++.
10. `e1_lower_to_hardware_graph`
    - Input: architecture-bound MLIR and memory plan.
    - Output: hardware graph with stable module interfaces.
11. `e1_emit_systemverilog`
    - Input: hardware graph.
    - Output: mocked or real SystemVerilog modules.
12. `e1_package_targets`
    - Input: SystemVerilog, C++ models, tests, and target config.
    - Output: FPGA package and ASIC/OpenROAD package.

## End State

E1 is complete when TinyLlama-derived reduced workloads can run through:

- StableHLO inspection.
- E1-H1 architecture binding.
- C++ chip model execution.
- Legible device program execution.
- L1.5 hybrid runs for every module.
- Generated SystemVerilog mocks.
- Verilator module tests.
- FPGA and ASIC/OpenROAD packaging smoke tests.
