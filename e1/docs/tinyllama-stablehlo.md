# TinyLlama And StableHLO Capture

## Objective

E1 starts by downloading a pinned TinyLlama checkpoint and exporting the model
through a path that produces StableHLO-compatible MLIR. The first milestone is
not performance. The first milestone is an inspectable artifact chain that shows
what the frontend emits and what every later compiler pass consumes.

## Required Artifacts

The E1 TinyLlama capture flow must produce:

- A manifest identifying the exact TinyLlama checkpoint and download source.
- A reproducible download command or script.
- The raw frontend output.
- StableHLO-compatible MLIR.
- A summary of unsupported or suspicious operations.
- Shape, dtype, and layout summaries for attention, MLP, normalization, and
  embedding paths.
- A pass-by-pass artifact directory for later lowering stages.

Current files:

- Manifest: `e1/model/tinyllama_manifest.json`
- Fetch tool: `e1/tools/fetch_tinyllama.py`
- StableHLO export tool: `e1/tools/export_stablehlo.py`
- Reduced StableHLO fixture: `e1/fixtures/stablehlo/tinyllama_block.mlir`
- Deterministic scaffold runner: `e1/tools/run_e1_pipeline.py`
- Pass artifacts: `e1/generated/pipeline/`

## StableHLO Inspection Questions

The first inspection pass must answer:

- Which operations dominate the model graph?
- Which operations map directly to systolic-array compute?
- Which operations require scalar/vector CPU handling?
- Which tensors must be staged through on-chip SRAM?
- Which data enters through Ethernet/RGMII in the E1-H1 system model?
- Which constants or weights need special transport, compression, or tiling?
- Which unsupported operations require a temporary C++ model fallback?

## Download Policy

Do not treat an unpinned model download as reproducible. Before E1 has a real
download script, docs must list the intended checkpoint and the exact command
that will be used.

Large model artifacts should not be committed to this repository. Store only
manifests, checksums, generated summaries, and small reduced IR fixtures unless
the root docs explicitly allow a checked-in artifact.

The checked-in pipeline currently runs in `offline_fixture` mode. The manifest
records the pinned Hugging Face revision and exact `hf download` command that
will be used when the full checkpoint is fetched outside the test path.

Live preflight commands:

```sh
python3 e1/tools/fetch_tinyllama.py --mode preflight --report e1/generated/pipeline/01_fetch_model.json
python3 e1/tools/export_stablehlo.py --mode preflight \
  --fetch-report e1/generated/pipeline/01_fetch_model.json \
  --stablehlo-out e1/generated/pipeline/02_stablehlo.mlir \
  --report e1/generated/pipeline/02_stablehlo_export.json
```

The current environment used by tests does not require network or large model
files. Live mode is intentionally gated on the Hugging Face CLI and frontend
dependencies.

## Current Implementation Check

The executable E1-H1 path proves the checked-in reduced TinyLlama StableHLO
fixture and now carries a shape-complete full-checkpoint structural RTL
lowering path through command-stream, graph-slot, and module-DPI construction
evidence. It still does not claim live full-checkpoint StableHLO export or
TinyLlama numeric output equivalence. The pipeline emits:

- `e1/generated/pipeline/12_module_dpi_generation.json`
- `e1/generated/pipeline/15_rtl_lowering.json`
- `e1/generated/pipeline/16_tinyllama_imp2_coverage.json`
- `e1/generated/pipeline/17_full_tinyllama_checkpoint_execution.json`
- `e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json`
- `e1/generated/pipeline/19_full_checkpoint_command_stream.json`
- `e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json`
- `e1/generated/pipeline/21_full_checkpoint_tile_engine.json`
- `e1/generated/pipeline/22_full_checkpoint_control_scheduler.json`
- `e1/generated/pipeline/23_full_checkpoint_graph_sequencer.json`
- `e1/generated/pipeline/24_full_checkpoint_rtl_top.json`
- `e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json`
- `e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json`
- `e1/generated/pipeline/27_full_graph_module_dpi_binding.json`
- `e1/generated/pipeline/28_lowering_construction_certificate.json`
- `e1/generated/pipeline/29_end_to_end_smoke.json`

The coverage artifact requires every StableHLO operation in
`tinyllama_block.mlir` to bind to an accepted active `imp2` implementation and
requires the target RTL filelist to use `e1/e1-h1/rtl/imp2/*.sv`. It also
records `full_tinyllama_checkpoint_implemented: false` until live checkpoint
export and execution are added.

The base E1-H1 module-DPI generator emits
`e1/e1-h1/generated/module_dpi/module_interfaces.md`,
`e1/e1-h1/generated/module_dpi/module_isolation.json`, and
`e1/e1-h1/generated/module_dpi/cycle_contract.json` in addition to the probe
manifest and `e1/e1-h1/generated/module_dpi/module_test_plan.json`. These
artifacts prove each replaceable IP has generated input/output signal
documentation, that each probe contains only one active `imp2` DUT plus its
`imp1` oracle with exact recorded instantiation counts in both the isolation
proof and construction ledger, that every probe-reported cycle is named against the README
cycle diagrams, that the exact ordered cycle-index row is present in the
README, and that each module has a generated Verilator invocation. The C++
generator also emits
`e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json`, which owns the
exact build command, obj-dir convention, run executable, and expected DPI
markers for every module-only run, and
`e1/e1-h1/generated/module_dpi/construction_ledger.json`, which ties each C++
module spec to its generated probe, flist, cycle contract, README phase
coverage, and Verilator recipe. `e1/tools/run_module_dpi_verilator.py`
consumes that generated recipe with the test plan and emits
`e1/e1-h1/generated/module_dpi/verilator_execution_report.json`, which records
the actual module-only Verilator build/run result and observed DPI stdout
markers for every base IP, including one `phase=<name>` marker for every named
cycle phase in the generated cycle contract. It also records the first observed
DPI phase trace and checks it against the generated cycle order. For base IPs
it also records `case=<name>` markers for every module-VIP stream-space case
declared in `e1/e1-h1/vip/*.json`, and the runner checks the observed case
trace against the C++-generated order. The pipeline module-DPI report also
cross-checks each generated probe, main, flist, top module, and active `imp2`
RTL path against `e1/e1-h1/generated/implementation_matrix.json`.

The RTL-lowering artifact maps each checked-in StableHLO fixture operation to
an active `imp2` RTL module, its imp2 flist, and its generated module-DPI proof.
It also records the cycle schedule and latch-buffer checks from
`e1/e1-h1/docs/modules/README.md`. This is a construction check for the reduced
fixture path, not a claim that the entire TinyLlama checkpoint graph has already
been lowered to RTL.

The full-checkpoint RTL lowering plan uses the pinned TinyLlama config shape
from `e1/model/tinyllama_manifest.json`: 22 layers, hidden width 2048,
intermediate width 5632, 32 attention heads, 4 KV heads, and bfloat16 weights.
It maps every layer's linear projections to the systolic array and the
control/elementwise pieces to the CPU/control path, using the generated
module-DPI collateral as the construction proof boundary. It records
`full_checkpoint_graph_lowering: false` until a real full StableHLO export and
Verilator/hybrid RTL execution prove the whole graph.

The full-checkpoint command-stream artifact emits
`e1/code/program/e1_tinyllama_full_schedule.hpp` and
`e1/code/program/e1_tinyllama_full_schedule_smoke.cpp`. The C++ schedule is a
compressed descriptor for 3,784,704 planned systolic-array tile commands across
22 layers; the smoke compiles and verifies the command count and first/last
command boundaries.

The full-checkpoint RTL-cycle lowering artifact emits
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler.sv`, a
matching flist, a Verilator C++ harness, and
`e1/code/program/e1_tinyllama_full_rtl_cycle_smoke.cpp`. This is the first RTL
lowering of the full linear tile stream: every tile command is assigned to the
documented 8-cycle CPU/latch-buffer/systolic-array template for 30,277,632
planned RTL cycles. It still records `full_checkpoint_graph_lowering: false`
and `full_checkpoint_rtl_execution: false` because RMSNorm, RoPE, attention
softmax, KV/cache updates, and end-to-end checkpoint comparison are not yet RTL
executed.

The full-checkpoint tile-engine artifact emits
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine.sv`, a
matching flist, and a Verilator C++ harness. The engine wires the generated
scheduler to the active `imp2` latch buffer (`e1_h1_stream_sram`) and active
`imp2` systolic array while gating array command acceptance to the documented
handshake cycle. Its harness checks real RTL command handshakes, latch-buffer
hold behavior, array input consumption, and command payloads against the
generated C++ schedule. It is still scoped to the full linear tile stream, not
the complete TinyLlama graph.

The full-checkpoint control-scheduler artifact emits
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler.sv`, a
matching flist, and a Verilator C++ harness. It lowers the seven CPU/control
ops per TinyLlama layer into a four-cycle control template: issue, metadata
read, execute, and commit. The covered graph slots are input RMSNorm, RoPE,
attention softmax/control, attention residual, post-attention RMSNorm, SiLU
gate multiply, and MLP residual for all 22 layers. This makes the planned
non-linear control path visible in RTL, but the arithmetic kernels and full
checkpoint output comparison are still future work.

The full-checkpoint graph-sequencer artifact emits
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer.sv`, a
matching flist, and a Verilator C++ harness. It preserves the ordered 14-slot
layer template from the lowering plan and launches either CPU/control work or
linear tile-engine work for each slot. The current generated sequencer covers
308 ordered graph slots across 22 layers: 154 control launches and 154 linear
launches. It is ordering RTL for the full layer inventory and is integrated by
the generated top-level RTL.

The full-checkpoint RTL-top artifact emits
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top.sv`,
slot-scoped linear and CPU/control engines, a matching flist, a bounded
Verilator C++ harness, and
`e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp`.
The top connects the graph sequencer to exactly one selected slot engine at a
time. Linear slots run through `e1_h1_stream_sram` and `e1_h1_systolic_array`
as separated modules; control slots run through a separate CPU/control slot
engine without instantiating array RTL. The bounded harness runs all 308 graph
slots with a two-tile smoke per linear slot. The full-command harness compiles
the same top with `SmokeMaxTilesPerLinearSlot=0` and runs all 3,784,704 planned
linear tile commands through the RTL control/handshake path while checking each
accepted command payload and the accepted payload digest against
`e1/code/program/e1_tinyllama_full_schedule.hpp`. It also checks the phase 1
scheduler-valid, phase 2 array-handshake, and phase 6 array-done sequence for
every command. The pipeline compiles and runs the bounded and full-command top
harnesses with Verilator and records their emitted JSON reports in
`e1/generated/pipeline/24_full_checkpoint_rtl_top.json`. The recorded build
commands use `<full_checkpoint_top_smoke_obj_dir>` and
`<full_checkpoint_top_full_obj_dir>` placeholders for Verilator object
directories, so the proof is not tied to one developer machine's temporary
path. This is full command-stream RTL execution evidence. The RTL-top report
records `full_checkpoint_rtl_execution: true` only at the scoped construction
boundary
`structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence`.
It also records
`full_checkpoint_structural_rtl_execution: true` only when the bounded
graph-slot smoke and the full-command run both pass, every planned command is
accepted through the RTL path, payload and digest checks match the generated
C++ schedule, every CPU/control slot payload and commit matches the generated
graph schedule, and the documented command phase checks pass. It is not yet a
TinyLlama numeric output comparison.

The full-checkpoint graph RTL-lowering proof ties the layer plan, command
stream, generated cycle scheduler, tile engine, control scheduler, graph
sequencer, and integrated top into one construction check. It records
`full_checkpoint_graph_lowering: true` only after every ordered layer slot has
an RTL slot-engine binding, the full top integrates the graph dispatcher, and
the full linear command stream is checked through the RTL top. The proof emits
one binding for each of the 308 ordered layer slots, with layer, slot-in-layer,
global-slot index, selected RTL engine, cycle template, and module-DPI probe
metadata. It also records README cycle coverage for the tile-command,
control-op, graph-launch, and top-dispatch templates, so the proof fails if a
used full-graph cycle phase is no longer listed in the module README diagrams.
It records `full_checkpoint_rtl_execution: true` only for the scoped structural
construction boundary, records `full_checkpoint_structural_rtl_execution: true`
for the current structural Verilator evidence, and still records
`full_checkpoint_numeric_output_equivalence: false`; arithmetic kernel
equivalence against a live TinyLlama checkpoint remains future work. The proof
also requires machine-checked README diagram snippets for the separated CPU,
`ingress_sram` latch buffer, systolic array, graph sequencer, and top
slot-dispatch cycle boundaries.

The full-checkpoint module-DPI generation artifact is produced by
`e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp`. It emits
`e1/e1-h1/generated/full_checkpoint_dpi/manifest.json`, one SystemVerilog DPI
probe per generated full-checkpoint RTL module, matching flists, C++ mains, a
shared C++ scoreboard, and
`e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md` with generated
input/output signal tables. The same generator parses each generated DUT's
SystemVerilog port block and the pipeline report fails if those tables omit,
reorder, or mis-size any RTL input or output. It also emits
`e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json`, which records
the allowed and forbidden RTL child modules for each generated DUT, and
`e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json`, which names every
cycle phase and phase signal for each generated module and requires the README
cycle-contract index to contain the exact ordered row for the module. The
companion
`e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json` records the
module-only Verilator build/run inputs for every generated module. The paired
`e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json`
records the C++-generated commands and expected DPI markers consumed by the
runner. The execution report
`e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json`
records the actual Verilator build/run result for each generated module-only
probe and requires every generated cycle-contract phase name to appear as an
observed DPI phase marker in the expected cycle order. It also records a
generated phase-signal trace, so `cycle_phase_o` or the full top's
`graph_cycle_phase_o` is checked against the expected cycle index while the
module runs. The generated
`e1/e1-h1/generated/full_checkpoint_dpi/construction_ledger.json` is the
per-module source-of-truth ledger tying the C++ spec to probes, flists,
interfaces, isolation, cycle phases, README coverage, and Verilator recipes.
This extends the module-only construction rule from the base E1-H1 IPs to the
generated TinyLlama RTL modules.

`e1/generated/pipeline/27_full_graph_module_dpi_binding.json` then binds the
full graph RTL proof to module-only verification. It requires the generated
linear scheduler, tile engine, control scheduler, graph sequencer, slot
engines, and full-checkpoint top to have passing module-DPI Verilator reports,
and also requires the separated base `control_cpu`, `ingress_sram` latch
buffer, and `systolic_array` modules to have passing module-only DPI reports
and passing C++ construction-ledger checks. The binding proof parses the
generated RTL and separated base `imp2` RTL source files, then emits a
source-derived coverage row for each parsed SystemVerilog module. Each
coverage row carries exact flist entries, probe DUT/reference instance counts,
cycle-contract status, README cycle-row status, ordered Verilator phase-trace
status, the matching generated C++ launcher runtime result, and an exact
launcher-recipe match for the generated command, executable, and stdout marker
contract. The row also carries the expected and observed launcher phase-key
prefixes for phase names and phase-signal values. The row records the selected
DUT RTL for both generated and base modules; generated full-checkpoint rows
prove `selected DUT + probe` flists, while separated base rows prove `imp1
reference + imp2 DUT + probe` flists. The binding also scans the on-disk
`e1/e1-h1/generated/full_checkpoint/*.sv` and `e1/e1-h1/rtl/imp2/*.sv`
inventories, then checks that they exactly match the generated module-DPI RTL
set and the all-base `imp2` RTL set, and that every parsed inventory module is
covered by module-DPI evidence. The same binding report also carries an
all-base-module proof for every replaceable base IP in
`e1/e1-h1/generated/module_dpi`, including RGMII ingress and both SRAM shell
configurations. Each all-base row parses the selected `imp2` RTL source and
proves the expected SystemVerilog top module is defined there, then ties that
row to the generated C++ launcher's runtime marker, ordered phase, repeated
template, phase-signal, and exact recipe-match checks.

`e1/generated/pipeline/28_lowering_construction_certificate.json` is the
machine-checkable construction certificate for the current lowering boundary.
It ties StableHLO fixture operations to active `imp2` RTL and module-DPI
proofs, ties every planned full-checkpoint graph slot to generated RTL slot
engines and documented cycle templates, records target-filelist agreement, and
hashes the source/report/RTL/README artifacts that form the proof chain. It
also hashes every target-listed RTL file, generated SoC top artifacts, the SoC
top generator inputs, the C++ module-DPI generator sources, the module-DPI
Verilator recipe runner, and the pipeline orchestrator so the proof chain
includes the programs that generate and execute module-local DPI evidence. It
also carries the production RTL inventory for generated top, accepted `imp1`
mocks, active base `imp2`, and generated full-checkpoint RTL, then hashes every
inventory RTL path. The accepted `imp1` mock RTL rows include one-file
Verilator lint evidence in addition to the C++/L1.5/VIP contracts. The same
certificate parses the generated SoC top against the composition manifest and
requires each expected IP instance to appear once, including distinct control
CPU, ingress latch-buffer, and systolic-array boundaries. It records every
StableHLO source operation instance by source line, source span, result SSA
name, and operation text before binding that instance to its RTL/module-DPI
proof. It also records the full
command-stream payload digest and checks that it matches the RTL-accepted
payload digest from the executed full-command Verilator report. It also
records the CPU/control slot payload digest from the same full-command
Verilator report. It requires structural RTL execution while explicitly
recording TinyLlama numeric output equivalence as a non-claim.

The end-to-end smoke report then checks that the active implementation, FPGA,
and OpenROAD target filelists all name the same RTL files, that each
target-listed RTL file is either the generated SoC top or has a passing
module-DPI proof with exact C++ launcher recipe and phase-key evidence, and that
the lowering construction certificate passes. It also records a production RTL
inventory spanning the generated SoC top,
accepted `imp1` mock RTL, active base `imp2` RTL, and generated
full-checkpoint RTL. Each inventory row must parse the RTL source, match the
expected SystemVerilog module names, and point to the proof family for that
category: standalone top Verilator, `imp1` mock C++/L1.5/VIP contracts plus
one-file Verilator lint, or module-only DPI/Verilator. Source-derived
module-only rows must also carry the generated C++ launcher's runtime result,
exact recipe match, and expected/observed phase-key prefixes.

## Full Checkpoint Execution

The full-checkpoint runner is:

```sh
python3 e1/tools/run_tinyllama_checkpoint.py --mode live \
  --report e1/generated/pipeline/17_full_tinyllama_checkpoint_execution.json
```

The same check is wired into the E1 pipeline:

```sh
python3 e1/tools/run_e1_pipeline.py --clean --full-checkpoint-mode live \
  --checkpoint-cache-dir .cache/e1/tinyllama-1.1b-chat-v1.0
```

Live mode requires the pinned checkpoint under
`.cache/e1/tinyllama-1.1b-chat-v1.0` plus local `torch`, `transformers`, and
`safetensors` Python packages. It loads the checkpoint locally, runs a
deterministic one-token prompt, records generated token ids and top logits, and
writes a checksum. The checked-in pipeline currently runs the same command in
`preflight` mode so test runs remain network-free and do not require large model
artifacts. A successful live run sets `full_tinyllama_checkpoint_implemented`
to `true` in the pipeline summary and end-to-end smoke report.

This live checkpoint execution is a Python/Transformers source-of-truth check
for the pinned model. It does not yet mean the full TinyLlama graph has been
lowered through imp2 RTL; that claim still belongs to the reduced StableHLO
fixture until full export/lowering coverage is implemented.
