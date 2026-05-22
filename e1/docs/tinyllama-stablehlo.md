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
