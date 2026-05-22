# Project Contract

## Mission

This repository will transform generic LLMs expressed in MLIR, initially
expected to use StableHLO-compatible operations, into SystemVerilog hardware
and companion device-side/runtime code. The generated hardware must target both
FPGA flows and ASIC flows based on OpenROAD.

The project is pass-based. Each transformation stage must have a documented
input contract, output contract, and verification path.

## Required Outputs

For a supported input model and architecture configuration, the repository must
be able to produce:

- Lowered MLIR or intermediate artifacts for each compiler pass.
- SystemVerilog organized by an obvious `l1/`, `l2/`, `l3/` hardware hierarchy.
- Target packaging for FPGA and ASIC/OpenROAD flows.
- Companion C++ models for generated or handwritten SystemVerilog modules.
- Module-local VIPs that verify exactly one SystemVerilog module at a time.
- Verilator harnesses capable of running module tests against the C++ model.
- Eventually, formal checks and end-to-end tests.

## Architecture Configuration

Architecture must be configurable from JSON files. JSON configuration controls
at least:

- Systolic array topology and how arrays are linked together.
- SRAM sizes, banking, widths, and placement assumptions.
- Pipeline lengths.
- Numeric precision and tensor layout decisions.
- Target selection for FPGA or ASIC/OpenROAD.
- External interfaces used by host or device-side code.

Compiler passes may elaborate JSON into stricter internal schemas, but JSON is
the human-editable source of architecture intent.

## Hardware Hierarchy

Generated or handwritten hardware must preserve an obvious hierarchy:

- `l1/`: top-level orchestration. A human should be able to inspect L1 modules
  and understand how the accelerator pieces connect.
- `l2/`: reusable architectural blocks, including systolic array clusters,
  SRAM subsystems, stream routers, schedulers, and target adapters.
- `l3/`: leaf modules and primitive building blocks.

L1 should favor readability and explicit wiring over clever compression. L2 may
contain Codex-assumed details while the design is evolving, but those details
must be documented in `docs/l2/assumptions.md`.

## Module Contract

Every SystemVerilog module must have:

- A corresponding L3 module document.
- A complete list of input signals and output signals with descriptions.
- A C++ model describing expected behavior.
- A Verilator path that can run the module against the C++ model.
- A module-local VIP that targets only that module.

At first, SystemVerilog modules may be mocks. Mock modules still need stable
interfaces, documented behavior, and C++ models that define inputs and outputs.
Replacing a mock with real RTL must not silently change the documented
interface or behavior.

## Documentation And Tests

Docs are the contract. Tests must conform to the docs, not to undocumented
implementation behavior.

Any code change that alters behavior or interfaces must update docs. Any docs
change that alters expected behavior must update or add tests. A change is not
complete until docs and tests describe the same behavior.

## Target Contract

FPGA and ASIC/OpenROAD targets share the same architectural semantics. Target
passes may differ in packaging, memory macros, constraints, clocks, resets, and
physical implementation assumptions, but they must not change the model-level
meaning of a computation.
