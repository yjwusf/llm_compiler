# E1: TinyLlama-To-Chip Example

E1 is the first worked example for this repository. It is the place where the
project will turn an LLM input into inspectable compiler artifacts, a generated
C++ chip model, legible device-side code, and eventually SystemVerilog for the
first hardware architecture.

## Goals

- Download and pin a TinyLlama checkpoint for repeatable experiments.
- Inspect what StableHLO/MLIR is generated from that model.
- Plan the complete pass sequence from StableHLO to SystemVerilog.
- Start the generated C++ chip model used for L1.5 hybrid execution.
- Keep the code running on the device readable enough for a human to audit.
- Define `e1-h1`, the first hardware architecture for this example.

## Layout

- `docs/`: E1-specific documentation and pass planning.
- `code/`: generated-model skeletons and legible code intended to run on the
  modeled device.
- `e1-h1/`: first hardware architecture instance for E1.

## Upstream References

- CORE-ET: https://github.com/openhwgroup/core-et
- Gemmini: https://github.com/ucb-bar/gemmini

E1-H1 will evaluate CORE-ET as a CPU/IP starting point and Gemmini as the first
systolic-array reference. These are references, not hard dependencies on their
existing interfaces. E1-H1 interfaces must stay stable enough that modules can
be replaced independently.
