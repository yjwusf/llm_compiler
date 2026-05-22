# Execution Plan

## Phase 0: Documentation And Repository Contract

Define the project contract, plan, architecture conventions, coding style, and
module documentation template. New code must follow these docs or update them
in the same change.

Done when:

- `docs/l1/`, `docs/l2/`, and `docs/l3/` exist.
- README points to the docs entry point.
- A module template exists with required signal documentation fields.

## Phase 1: MLIR Ingest And Pass Skeleton

Add a compiler pipeline that can ingest MLIR containing StableHLO-style LLM
operations and run named passes without requiring full lowering.

Initial pass boundaries:

- Model ingest and validation.
- StableHLO normalization.
- Tensor shape and layout analysis.
- Architecture binding from JSON.
- Memory planning.
- Pipeline insertion.
- Hardware graph lowering.
- SystemVerilog emission.
- Target packaging for FPGA or ASIC/OpenROAD.

Done when each pass has a documented input/output contract and minimal tests.

## Phase 2: Architecture JSON Schema

Define a human-editable JSON schema for architecture configuration.

The schema must cover:

- Systolic array dimensions and link topology.
- SRAM sizes, banking, widths, and logical ownership.
- Pipeline depths.
- Precision choices.
- Target selection.
- Host/device interface assumptions.

Done when sample JSON files can drive a mock hardware graph.

## Phase 3: Mock Hardware Modules

Create the initial SystemVerilog module set as mocks. Each module must expose
the intended interface while delegating behavioral truth to the C++ model and
module VIP.

Done when every mock module has:

- L3 module documentation.
- Input and output signal tables.
- C++ model.
- Verilator harness.
- Module-local VIP.

## Phase 4: Systolic Array And SRAM Implementation

Replace selected mocks with real RTL for systolic compute and SRAM-backed data
movement.

Done when:

- Systolic arrays can be linked according to JSON configuration.
- SRAM size changes are driven by JSON and reflected in generated RTL
  parameters.
- C++ models and VIPs cover the replaced RTL.

## Phase 5: Configurable Pipelines

Make pipeline length configurable across compiler lowering and RTL generation.

Done when:

- Pipeline depths come from JSON or compiler defaults documented in L2.
- Verilator tests cover at least shallow, default, and deep pipeline settings.
- Module docs describe latency and valid/ready behavior.

## Phase 6: FPGA Target

Add FPGA target packaging and tests.

Done when generated output includes FPGA-oriented constraints, memory mapping
assumptions, and build scripts sufficient for the selected FPGA flow.

## Phase 7: ASIC/OpenROAD Target

Add ASIC/OpenROAD target packaging and tests.

Done when generated output includes OpenROAD-friendly RTL, constraints, macro
assumptions, and build scripts.

## Phase 8: Formal And End-To-End Tests

Add formal properties and end-to-end model-to-hardware tests.

Done when:

- Formal properties exist for selected L3 and L2 modules.
- End-to-end tests compile an MLIR LLM fragment through generated hardware.
- C++ model, Verilator, and generated target artifacts agree on expected
  behavior.
