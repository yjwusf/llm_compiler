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
   - Output: on-chip SRAM allocation, tile plan, movement schedule, and
     architecture-owned pipeline depths.
7. `e1_plan_device_program`
   - Input: operation binding and memory plan.
   - Output: legible device program under `e1/code/program/` and host MMIO
     smoke report for the first tile command.
8. `e1_generate_chip_model`
   - Input: architecture config, pass metadata, and module docs.
   - Output: C++ generated chip model under `e1/code/chip_model/`.
9. `e1_generate_l1_5_harnesses`
   - Input: module specs and C++ chip model.
   - Output: one hybrid run per SystemVerilog module, with all other behavior
     supplied by C++, plus a module-local VIP manifest for each DUT.
10. `e1_lower_to_hardware_graph`
    - Input: architecture-bound MLIR and memory plan.
    - Output: hardware graph with stable module interfaces.
11. `e1_select_implementations`
    - Input: hardware graph, IP manifests, and module VIPs.
    - Output: implementation matrix and gathered flists for active `imp2`
      candidate RTL, with `imp1` retained as the mock reference.
12. `e1_emit_systemverilog`
    - Input: hardware graph.
    - Output: mocked or real SystemVerilog modules and generated top-level
      pipeline registers from the architecture JSON.
13. `e1_package_targets`
    - Input: SystemVerilog, C++ models, tests, and target config.
    - Output: FPGA package and ASIC/OpenROAD package.
14. `e1_check_tinyllama_imp2_coverage`
    - Input: TinyLlama StableHLO fixture, binding, active implementation
      matrix, device-program smoke, and C++ chip-model smoke.
    - Output: proof that every StableHLO op in the reduced TinyLlama fixture is
      bound to active `imp2` RTL, plus an explicit non-claim for full checkpoint
      execution.
15. `e1_end_to_end_smoke`
    - Input: all prior pass artifacts.
    - Output: one evidence report tying StableHLO, E1-H1 binding, device code,
      C++ chip model, generated SystemVerilog top, and target packages together.

## End State

E1 is complete when TinyLlama-derived reduced workloads can run through:

- StableHLO inspection.
- E1-H1 architecture binding.
- TinyLlama fixture operation coverage through active `imp2` RTL.
- C++ chip model execution.
- Legible device program execution.
- L1.5 hybrid runs for every module.
- Module-local VIP manifests that target exactly one SystemVerilog module.
- Implementation matrix showing active `imp2` candidates and `imp1` mock
  references.
- Generated SystemVerilog mocks.
- End-to-end smoke evidence that references the exact artifacts used.
- Verilator module tests.
- FPGA and ASIC/OpenROAD packaging smoke tests.

## Current Executable Scaffold

Run:

```sh
python3 e1/tools/run_e1_pipeline.py --clean
```

This creates `e1/generated/pipeline/` from the pinned TinyLlama manifest,
fetch/export reports, reduced StableHLO fixture, E1-H1 architecture JSON, IP
manifests, module docs, and generated SoC top. It is intentionally
deterministic and network-free so it can run in E1-H1 tests.
