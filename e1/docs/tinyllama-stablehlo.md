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

- `e1/generated/pipeline/13_tinyllama_imp2_coverage.json`
- `e1/generated/pipeline/14_end_to_end_smoke.json`

The coverage artifact requires every StableHLO operation in
`tinyllama_block.mlir` to bind to an accepted active `imp2` implementation and
requires the target RTL filelist to use `e1/e1-h1/rtl/imp2/*.sv`. It also
records `full_tinyllama_checkpoint_implemented: false` until live checkpoint
export and execution are added.
