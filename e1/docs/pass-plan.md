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
12. `e1_generate_module_dpi`
    - Input: implementation matrix, IP manifests, and module VIPs.
    - Output: C++-generated module-DPI probes, per-module flists, and a
      construction report proving one generated probe per replaceable module.
13. `e1_emit_systemverilog`
    - Input: hardware graph.
    - Output: mocked or real SystemVerilog modules and generated top-level
      pipeline registers from the architecture JSON.
14. `e1_package_targets`
    - Input: SystemVerilog, C++ models, tests, and target config.
    - Output: FPGA package and ASIC/OpenROAD package.
15. `e1_lower_to_rtl`
    - Input: StableHLO binding, hardware graph, implementation matrix,
      module-DPI report, and target filelists.
    - Output: construction-checked RTL lowering report that maps every
      operation in the checked-in StableHLO fixture to active `imp2` RTL,
      per-module DPI proof collateral, and the documented cycle schedule.
16. `e1_check_tinyllama_imp2_coverage`
    - Input: TinyLlama StableHLO fixture, binding, active implementation
      matrix, device-program smoke, and C++ chip-model smoke.
    - Output: proof that every StableHLO op in the reduced TinyLlama fixture is
      bound to active `imp2` RTL, plus an explicit non-claim for full checkpoint
      execution.
17. `e1_run_full_tinyllama_checkpoint`
    - Input: pinned checkpoint cache, tokenizer files, and local
      `torch`/`transformers` dependencies.
    - Output: a full-checkpoint execution report. The default deterministic
      repo path runs as a preflight and records the exact missing cache or
      Python dependencies instead of claiming execution. The same pass can be
      selected in `live` mode from the pipeline CLI when the checkpoint cache
      and dependencies are present.
18. `e1_plan_full_checkpoint_rtl_lowering`
    - Input: pinned checkpoint shape, active `imp2` implementation matrix,
      module-DPI report, reduced-fixture RTL lowering report, and checkpoint
      execution artifact.
    - Output: a shape-complete full-checkpoint RTL lowering plan that maps all
      TinyLlama layers to the CPU/control path, systolic array, SRAMs, Ethernet
      ingress, and latch buffer. This is a plan artifact and explicitly does
      not claim full checkpoint RTL execution.
19. `e1_emit_full_checkpoint_command_stream`
    - Input: full-checkpoint RTL lowering plan and E1-H1 array tile shape.
    - Output: compact C++ tile-command stream descriptors for all planned
      TinyLlama linear ops, plus a host smoke proving the command count and
      boundary commands without unrolling millions of commands into source.
20. `e1_lower_full_checkpoint_command_stream_to_rtl_cycles`
    - Input: full-checkpoint command stream and generated module-DPI boundary
      report.
    - Output: a generated SystemVerilog linear tile scheduler, flist,
      Verilator C++ harness, and C++ cycle smoke. This lowers the full
      linear-tile command stream into an 8-cycle CPU/latch/array template, but
      still records a non-claim for full TinyLlama graph RTL execution.
21. `e1_wire_full_checkpoint_tile_engine`
    - Input: full-checkpoint scheduler RTL, active latch-buffer RTL, active
      systolic-array RTL, and the command-stream/cycle reports.
    - Output: a generated tile-engine RTL composition and Verilator C++
      harness that wires scheduler, latch buffer, and systolic array together
      while keeping their responsibilities separated.
22. `e1_lower_full_checkpoint_control_ops_to_rtl`
    - Input: shape-complete full-checkpoint layer plan and generated
      module-DPI report.
    - Output: generated CPU/control scheduler RTL, flist, and Verilator C++
      harness for the 154 planned non-linear TinyLlama control ops across 22
      layers.
23. `e1_sequence_full_checkpoint_graph_slots`
    - Input: full-checkpoint layer plan, command stream, and control scheduler
      report.
    - Output: generated RTL graph sequencer, flist, and Verilator C++ harness
      for the ordered 14-slot TinyLlama layer template across 22 layers.
24. `e1_integrate_full_checkpoint_rtl_top`
    - Input: graph sequencer report and full-checkpoint command stream.
    - Output: generated full-checkpoint RTL top, slot-scoped linear/control
      engines, flist, bounded Verilator C++ harness, and full-command
      Verilator C++ harness. The bounded harness runs all 308 graph slots while
      limiting each linear slot to a short tile smoke; the full-command harness
      runs the same top for all 3,784,704 planned linear tile commands and
      checks every accepted command payload and documented command phase
      sequence against the generated C++ schedule.
25. `e1_generate_full_checkpoint_module_dpi`
    - Input: generated full-checkpoint RTL modules.
    - Output: C++-generated module-DPI probes, flists, C++ mains, scoreboard,
      manifest, and generated input/output signal documentation for each
      generated full-checkpoint RTL module.
26. `e1_end_to_end_smoke`
    - Input: all prior pass artifacts.
    - Output: one evidence report tying StableHLO, E1-H1 binding, device code,
      C++ chip model, generated SystemVerilog top, and target packages together.

## End State

E1 is complete when TinyLlama-derived reduced workloads can run through:

- StableHLO inspection.
- E1-H1 architecture binding.
- TinyLlama fixture operation coverage through active `imp2` RTL.
- Full TinyLlama checkpoint execution report from
  `e1/tools/run_tinyllama_checkpoint.py`.
- C++ chip model execution.
- Legible device program execution.
- L1.5 hybrid runs for every module.
- Module-local VIP manifests that target exactly one SystemVerilog module.
- C++-generated module-DPI probes and per-module Verilator runs.
- RTL lowering evidence that maps StableHLO fixture operations to active `imp2`
  RTL and the documented cycle/latch schedule.
- Full-checkpoint RTL lowering plan for the pinned TinyLlama layer inventory,
  with an explicit non-claim until full StableHLO export and RTL execution are
  proven.
- Full-checkpoint compressed tile-command stream code that can enumerate the
  planned systolic-array commands and pass a host smoke.
- Full-checkpoint linear tile scheduler RTL, its flist, and a Verilator harness
  that checks emitted RTL commands against the generated C++ schedule while
  naming every cycle in the CPU/latch/array template.
- Full-checkpoint tile-engine RTL that wires the generated scheduler to the
  explicit latch buffer and systolic array, with a Verilator harness proving
  command handshakes, latch holds, and array input consumption.
- Full-checkpoint CPU/control scheduler RTL that enumerates RMSNorm, RoPE,
  attention-control/softmax, residual, and SiLU gate-control graph slots for
  every layer and passes a Verilator harness.
- Full-checkpoint graph sequencer RTL that preserves the ordered 14-slot
  layer template and launches either CPU/control work or linear tile work in
  the correct layer order.
- Full-checkpoint RTL top that wires the ordered graph sequencer to
  slot-scoped CPU/control and linear engines, keeping the latch buffer and
  systolic array as separated modules and passing both a bounded full-graph
  Verilator smoke and a full-command-payload/phase Verilator run.
- C++-generated module-DPI probes for the generated full-checkpoint RTL
  modules, including their flists, C++ mains, scoreboard, and per-module cycle
  notes.
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
