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
      construction report proving one generated probe per replaceable module,
      plus generated base-IP interface documentation, isolation,
      cycle-contract, and Verilator test-plan artifacts. The C++ generator also
      emits the Verilator execution recipe that the runner must match before
      building any module. It also emits a C++ Verilator launcher whose dry-run
      module records must exactly match that execution recipe. Each base
      module-DPI probe must emit one DPI case marker per declared module-VIP
      stream-space case from `e1/e1-h1/vip`.
      The systolic-array probe must also emit and check a
      `E1_H1_MODULE_DPI_SYSTOLIC_DIGEST` marker, proving that
      `result_digest_o` matches the generated C++ DPI scoreboard on the
      documented module-only cycle template.
      The isolation proof and construction ledger must both record exactly one
      `imp2` DUT instantiation and exactly one `imp1` reference instantiation
      for each probe. The isolation proof must also carry top-level `status`,
      `checks`, and named separated boundaries for the CPU, explicit latch
      buffer, and systolic array.
      The pipeline report must cross-check each generated module-DPI probe,
      flist, main, top module, and `imp2` RTL path against the active
      implementation matrix entry. It must also parse each selected `imp2`
      SystemVerilog module and check that the generated signal docs and IP
      manifest ports match the actual RTL port contract.
      The report must also record the sanitized C++ generator build command,
      run command, working directory, and normalized generator stdout proving
      the generator reported the expected module count.
      It must compile and dry-run the generated C++ Verilator launcher and
      prove the launcher's per-module build/run records exactly match the
      generated execution recipe. The same launcher must also run every
      module-only Verilator test in `--run --build-root <dir>` mode and report
      pass/fail for each module. In run mode, the C++ launcher must capture
      each module executable's stdout and fail the module if any expected
      recipe marker is missing, if the ordered `cycle:phase` trace does not
      match and repeat the cycle contract, or if the phase-signal trace does
      not match and repeat the expected cycle index values.
      The README coverage check must match the exact ordered cycle-index row,
      not just individual phase tokens.
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
    - Output: a passing shape-complete full-checkpoint layer-to-RTL contract
      that maps all TinyLlama layers to the CPU/control path, systolic array,
      SRAMs, Ethernet ingress, and latch buffer, with every op carrying active
      `imp2` RTL and module-DPI collateral. This artifact is the construction
      input for later command-stream, graph-sequencing, RTL-top, and
      certificate passes; by itself it still does not claim live checkpoint
      StableHLO export, full RTL execution, or numeric output equivalence.
19. `e1_emit_full_checkpoint_command_stream`
    - Input: full-checkpoint RTL lowering plan and E1-H1 array tile shape.
    - Output: compact C++ tile-command stream descriptors for all planned
      TinyLlama linear ops, plus a host smoke proving the command count,
      boundary commands, and deterministic payload digest without unrolling
      millions of commands into source.
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
      checks every accepted command payload, accepted payload digest, and
      documented command phase sequence against the generated C++ schedule.
      It also checks every CPU/control slot payload and commit against the
      generated graph schedule and records a control-slot payload digest.
      The pass compiles and runs both harnesses with Verilator and records
      their emitted JSON reports in the RTL-top report. Recorded build
      commands use stable object-directory tokens
      (`<full_checkpoint_top_smoke_obj_dir>` and
      `<full_checkpoint_top_full_obj_dir>`) rather than machine-local
      temporary directories. The report sets
      `full_checkpoint_rtl_execution: true` at the scoped construction
      boundary
      `structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence`
      and sets
      `full_checkpoint_structural_rtl_execution: true` only when the bounded
      graph-slot smoke and full-command Verilator run both pass, every planned
      command reaches the RTL top, payload and digest checks match the
      generated C++ schedule, and the documented command phases are observed.
      TinyLlama numeric output equivalence remains a separate non-claim.
25. `e1_prove_full_checkpoint_graph_rtl_lowering`
    - Input: the full-checkpoint layer plan, command stream, cycle scheduler,
      tile engine, control scheduler, graph sequencer, and integrated RTL top
      reports.
    - Output: a construction proof that every ordered TinyLlama graph slot is
      bound to a generated slot-scoped RTL engine, that the full linear command
      stream runs through the integrated RTL top, and that command payloads and
      documented phases are checked. The proof materializes all 308 layer/slot
      bindings, not just the repeated 14-slot template. This proves graph-slot
      RTL lowering only when the slot bindings reference cycle templates whose
      phase names are present in the module README diagrams, and still does not
      claim TinyLlama numeric output equivalence. The proof records structural
      RTL execution separately from numeric equivalence so reviewers can see
      that graph dispatch, slot-engine handshakes, command payloads, digest, and
      phases ran under Verilator without treating that as output tensor
      correctness.
26. `e1_generate_full_checkpoint_module_dpi`
    - Input: generated full-checkpoint RTL modules.
    - Output: C++-generated module-DPI probes, flists, C++ mains, scoreboard,
      manifest, generated input/output signal documentation, and generated
      isolation, cycle-contract, Verilator test-plan, C++-owned Verilator
      execution recipe, C++ construction ledger, and Verilator execution proof
      for each generated full-checkpoint RTL module. The generator parses each
      generated DUT's SystemVerilog port list and fails if the generated signal
      documentation does not match the RTL input/output names and widths
      exactly. The pipeline report must record the sanitized C++ generator
      build command, run command, working directory, and normalized generator
      stdout proving the expected generated-module count. It must compile and
      dry-run the generated C++ Verilator launcher and prove that each launcher
      module record exactly matches the generated execution recipe. The same
      launcher must also run every generated-module Verilator test in
      `--run --build-root <dir>` mode and report pass/fail for each module.
      In run mode, the C++ launcher must capture each module executable's
      stdout and fail the module if any expected recipe marker is missing, if
      the ordered `cycle:phase` trace does not match and repeat the cycle
      contract, or if the phase-signal trace does not match and repeat the
      expected cycle index values.
      The Verilator proof must observe every named phase marker from the
      generated cycle contract.
      The generated isolation proof must also
      carry top-level `status`, `checks`, and named separated boundaries for
      control modules, linear modules, latch-buffer RTL, and systolic-array RTL.
      Reports also preserve the first observed phase trace in cycle order so
      the named cycle template is checked as an ordered sequence, not just as a
      marker set, and preserve a generated phase-signal trace tying
      `cycle_phase_o` or top-level `graph_cycle_phase_o` to the expected cycle
      index at runtime. The README cycle-index row must also match the
      generated ordered phase list.
27. `e1_bind_full_graph_module_dpi`
    - Input: full graph RTL lowering proof, base module-DPI report, and
      generated full-checkpoint module-DPI report.
    - Output: a construction proof that every generated RTL module used by the
      full graph lowering, plus the separated base CPU, latch buffer, and
      systolic array modules, has module-only DPI/Verilator execution evidence
      backed by C++-generated recipes and C++ construction ledgers. The proof
      parses the generated SystemVerilog source modules and the separated
      base `imp2` RTL source modules, then records a source-derived coverage
      row for each parsed module. Each source-derived row also carries exact
      flist entries, probe DUT/reference instantiation counts, cycle-contract
      checks, README cycle-row checks, ordered Verilator phase-trace checks, and
      the matching generated C++ launcher runtime result. The launcher result
      must also match the row's generated recipe command, executable, and stdout
      marker contract exactly, and must carry the exact expected and observed
      launcher phase-key prefixes for both phase names and phase-signal values.
      The row records the selected DUT RTL for both generated and base modules;
      generated rows must use `selected DUT + probe` flists, while separated
      base rows must use `imp1 reference + imp2 DUT + probe` flists.
      The proof also scans the on-disk
      `e1/e1-h1/generated/full_checkpoint/*.sv` and
      `e1/e1-h1/rtl/imp2/*.sv` inventories and fails if those inventories do
      not exactly match the generated module-DPI RTL list and the all-base
      `imp2` RTL list, respectively, or if a parsed inventory module lacks
      module-DPI coverage.
      It also carries a separate all-base-module proof for every replaceable
      base IP in the module-DPI report, including RGMII ingress and the SRAM
      shell configurations, not only the three base modules used by the current
      graph path. Each all-base row parses the selected `imp2` RTL source,
      proves the expected top module is present in that source, and carries the
      matching generated C++ launcher runtime result plus exact recipe match.
28. `e1_emit_lowering_construction_certificate`
    - Input: StableHLO inspection and binding reports, reduced-fixture RTL
      lowering, full-checkpoint graph RTL lowering proof, module-DPI reports,
      full-graph module-DPI binding, active implementation matrix, and target
      filelists.
    - Output: a machine-checkable construction certificate tying the
      checked-in StableHLO fixture operations to active `imp2` RTL and
      module-DPI proofs, tying each planned full-checkpoint graph slot to RTL
      slot engines and documented cycle templates, and recording hashes for
      the relevant source, generated RTL, report, README, and target-filelist
      artifacts. The certificate records each StableHLO source operation
      instance by source line, result SSA name, and source span before binding
      that instance to its RTL/module-DPI proof. It also records the full
      command-stream payload digest and requires the RTL-accepted payload digest
      to match the generated C++ schedule from the executed full-command
      Verilator report. It also requires the structural RTL execution flag from
      the full-graph proof and requires machine-checked README diagram snippets
      for the separated CPU, latch buffer, systolic array, graph sequencer, and
      top slot-dispatch cycle boundaries. It records both the linear command
      payload digest and the CPU/control slot payload digest. It also hashes
      the C++ module-DPI generator sources, module-DPI reports carrying
      generator build/run stdout evidence, generated C++ Verilator launchers,
      the module-DPI Verilator recipe runner, the pipeline orchestrator, every
      target-listed RTL file, and the generated SoC top generator inputs as
      construction inputs. The
      certificate also carries the production RTL inventory for generated top,
      accepted `imp1` mocks, active base `imp2`, and generated
      full-checkpoint RTL, then hashes every inventory RTL path and checks that
      each parsed SystemVerilog module name matches the proof family attached
      to that RTL file. Active source-derived RTL rows, meaning base `imp2`
      and generated full-checkpoint RTL, must carry module-only DPI/Verilator
      coverage in the inventory. The inventory also carries a standalone
      runtime lane whose required paths are the generated SoC top, active base
      `imp2` RTL, and generated full-checkpoint RTL. That lane proves the
      generated top with its C++ Verilator testbench and proves the base and
      full-checkpoint RTL with the C++-generated module-DPI launchers; only
      accepted `imp1` mock RTL is exempt, after one-file-at-a-time Verilator
      lint passes. The
      generated SoC top hierarchy is parsed against its composition manifest,
      so the CPU, RGMII ingress, latch buffer, SRAM shells, and systolic array
      must appear as distinct expected instances in manifest order. The
      certificate must also emit a `module_boundary_taxonomy` section that maps
      active runtime boundaries into CPU/control, digital ingress, latch buffer,
      SRAM shell, systolic array, linear systolic path, and top-glue roles. Each
      source-derived entry must point to module-only isolation proof, Verilator
      execution, cycle contract, README cycle coverage, interface documentation,
      and standalone runtime inventory evidence, while generated SoC top glue is
      tied to hierarchy plus standalone top-smoke evidence.
      The certificate must also emit `module_interface_signal_inventory`, a
      machine-readable copy of the per-module input/output signal lists,
      descriptions, and parsed RTL port contracts for base `imp2` and generated
      full-checkpoint modules.
      It also emits `systemverilog_module_coverage_audit`, a consolidated
      table that lists every production RTL row, parsed SystemVerilog module
      names, the required run scope, and the single-DUT module-DPI runtime
      evidence for each active source-derived module.
      It also emits `systemverilog_defined_module_runtime_audit`, a
      module-name-level table that assigns every parsed production
      SystemVerilog module to exactly one explicit runtime scope:
      module-only DPI/Verilator for active source-derived modules,
      standalone-top Verilator for generated top glue, or generated C++
      Verilator runtime plus lint and contract scope for accepted `imp1`
      mock references.
      It also emits `cycle_diagram_audit`, which ties README cycle rows to
      generated cycle contracts and observed Verilator phase traces for base
      modules, generated full-checkpoint modules, and graph-slot templates.
      The audit must also require the README's module cycle runtime matrix to
      list each module-only runtime row with the same template, cycle count,
      observed phase-trace count, observed phase-signal count, and runtime
      status recorded by Verilator.
      It also emits `dpi_generation_provenance_audit`, which ties the C++
      generator sources, sanitized generator build/run records, emitted
      module-DPI artifacts, generated C++ Verilator launchers, and launcher
      runtime results into one reviewable provenance section.
      It also emits `systolic_array_result_digest_proof`, which binds the
      base systolic-array `result_digest_o` output to the generated C++
      module-DPI scoreboard and launcher runtime marker evidence.
      The
      certificate also emits an `objective_coverage` table that maps the
      current full-RTL-lowering objective to concrete evidence for construction
      checking, C++ DPI generation, module-only proofs, active-SystemVerilog
      standalone runtime proofs, module interface signal documentation,
      module-boundary taxonomy, separated boundaries, latch-buffer behavior,
      README cycle diagrams, and Verilator-observed runtime phase traces. It
      includes a requirement that the module coverage audit pass before the
      active objective completion audit can pass. It also requires the cycle
      diagram audit to pass before the README-cycle objective is accepted. It
      also emits
      `active_objective_completion_audit`, which records the current verdict as
      `proved_for_structural_rtl_lowering_scope`, repeats the exact objective
      requirements it proves, and carries the non-claims for TinyLlama numeric
      output equivalence and live checkpoint StableHLO export. It also emits
      `objective_traceability_audit`, which maps each human-requested clause
      for full RTL lowering, construction checking, C++ DPI generation,
      module-only SystemVerilog runs, separated CPU/latch/systolic boundaries,
      and README cycle diagrams to concrete evidence while preserving the
      structural RTL scope and non-claims. The certificate
      must also summarize the generated C++ launcher runtime coverage by suite,
      including module counts, zero run failures, marker checks, ordered
      phase-prefix checks, repeated cycle-template checks, phase-signal checks,
      and observed trace record counts. The DPI generation provenance audit
      must pass before the C++ DPI generation objective is accepted. It must
      also require the full-command
      C++ Verilator harness to emit first/last linear and control trace anchors
      whose observed RTL payloads match the generated C++ lowering schedule,
      plus per-linear-op and per-control-slot trace coverage counts whose
      observed RTL payload counts match the generated C++ schedule.
      It must also require pass-27
      source-derived and all-base boundary
      rows to carry passing C++ launcher runtime evidence, exact
      launcher-recipe matches, and a README-cycle proof showing that launcher
      observed phase keys match the generated cycle contract and the exact
      README cycle-contract row. The production RTL inventory must tie each
      required source RTL row to a passing C++ launcher module result and exact
      launcher-recipe, phase-key, and README-cycle proof, not only to the
      Python Verilator report wrapper. It explicitly keeps TinyLlama numeric
      output equivalence as a non-claim until that execution path exists.
29. `e1_end_to_end_smoke`
    - Input: all prior pass artifacts.
    - Output: one evidence report tying StableHLO, E1-H1 binding, device code,
      C++ chip model, generated SystemVerilog top, and target packages together.
      The target filelists must match the active implementation filelist and
      each listed RTL file must either be generated top-level glue or have a
      passing module-DPI proof with exact C++ launcher recipe, phase-key, and
      README-cycle evidence. Generated top-level glue must carry a
      standalone Verilator smoke proof instead of only an existence check. The
      generated-top smoke records `<soc_top_obj_dir>` in the proof command line
      and executable path so checked-in evidence is reproducible across
      machines. It also emits a production RTL inventory covering the generated
      SoC top, accepted `imp1` mock RTL, active base `imp2` RTL, and generated
      full-checkpoint RTL. Each inventory row must parse at least one
      SystemVerilog module, match the expected proof module names, and carry
      either standalone top Verilator evidence, `imp1` mock C++/L1.5/VIP
      contract plus one-file Verilator lint and generated C++ runtime evidence,
      or module-only DPI/Verilator evidence. It also records the subset for which
      module-only DPI is mandatory: active base `imp2` RTL and generated
      full-checkpoint RTL. Those same source-derived rows must also appear in
      the production inventory's C++ launcher covered-path list, proving the
      generated C++ launcher built and ran the matching module-only Verilator
      executable with exact recipe, phase-key, and README-cycle evidence. It
      requires the lowering construction certificate to pass.

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
- C++-generated module-DPI probes and generated per-module Verilator test
  plans, with generated input/output signal documentation for each base IP.
  The generated Verilator reports must record ordered phase traces for the
  module cycle templates and ordered VIP case traces for the declared
  module-VIP stream-space cases.
- Generated base-IP module isolation and cycle-contract artifacts for CPU,
  RGMII ingress, latch buffer, SRAM shells, and systolic array.
- A module-only systolic-array result digest proof showing that the active
  `imp2` RTL result signal matches generated C++ DPI scoreboard expectations.
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
  notes, plus generated isolation proof showing each DUT's allowed and
  forbidden child modules and a generated cycle contract naming every phase in
  each module template, with generated construction ledgers and Verilator test
  plans for every module. Generated signal documentation must match the parsed
  SystemVerilog port contracts for those generated DUTs. The generated
  module-only Verilator reports must observe all named phase markers from those
  cycle contracts.
- Full-graph module-DPI binding proof tying generated full-graph RTL artifacts
  and separated base modules to module-only Verilator/DPI execution reports and
  C++ construction-ledger checks, with source-derived coverage proving every
  parsed generated RTL module and every separated base RTL module has a
  matching module-only proof.
- A SystemVerilog module coverage audit that maps every production RTL row to
  parsed module names, run scope, and single-DUT runtime evidence.
- A defined-module runtime audit that maps every parsed production
  SystemVerilog module name to an explicit runtime scope, including generated
  C++ Verilator smoke runs for accepted `imp1` mocks.
- A cycle-diagram audit that maps README rows to generated cycle contracts and
  observed Verilator phase traces, including a README module cycle runtime
  matrix row for every base and generated module-only Verilator run.
- A DPI generation provenance audit that maps C++ generator sources and
  sanitized build/run records to emitted module-DPI artifacts, generated C++
  Verilator launchers, and per-module runtime results.
- An objective traceability audit that maps every human-requested full-RTL
  lowering clause to the certificate evidence that proves the current
  structural scope, including explicit residual non-claims.
- Implementation matrix showing active `imp2` candidates and `imp1` mock
  references.
- Generated SystemVerilog mocks.
- End-to-end smoke evidence that references the exact artifacts used.
- End-to-end target-filelist evidence showing FPGA/OpenROAD filelists match
  the active implementation flist and every listed RTL file has either a
  generated-top proof or passing module-DPI proof with exact C++ launcher recipe
  phase-key, and README-cycle evidence.
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
