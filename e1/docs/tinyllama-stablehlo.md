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

The executable E1-H1 path currently proves the checked-in reduced TinyLlama
StableHLO fixture, not the full TinyLlama checkpoint. The pipeline emits:

- `e1/generated/pipeline/12_module_dpi_generation.json`
- `e1/generated/pipeline/15_rtl_lowering.json`
- `e1/generated/pipeline/16_tinyllama_imp2_coverage.json`
- `e1/generated/pipeline/17_full_tinyllama_checkpoint_execution.json`
- `e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json`
- `e1/generated/pipeline/19_full_checkpoint_command_stream.json`
- `e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json`
- `e1/generated/pipeline/21_full_checkpoint_tile_engine.json`
- `e1/generated/pipeline/22_full_checkpoint_control_scheduler.json`
- `e1/generated/pipeline/23_end_to_end_smoke.json`

The coverage artifact requires every StableHLO operation in
`tinyllama_block.mlir` to bind to an accepted active `imp2` implementation and
requires the target RTL filelist to use `e1/e1-h1/rtl/imp2/*.sv`. It also
records `full_tinyllama_checkpoint_implemented: false` until live checkpoint
export and execution are added.

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
