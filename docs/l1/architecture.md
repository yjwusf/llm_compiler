# Architecture

## Compiler Flow

The compiler lowers an MLIR model to generated hardware through explicit
passes. The expected starting point is StableHLO-compatible MLIR, but the flow
must keep pass boundaries clear enough to support other MLIR frontends later.

Planned flow:

1. Validate MLIR input.
2. Normalize StableHLO-style operations.
3. Analyze tensor shapes, layouts, and precision.
4. Load architecture JSON.
5. Bind model operations to architecture resources.
6. Plan SRAM allocation and data movement.
7. Insert configurable pipelines.
8. Lower to a hardware graph.
9. Emit SystemVerilog and C++ model bindings.
10. Package for FPGA or ASIC/OpenROAD.

Each pass must be testable on its own. Intermediate artifacts should be easy to
dump for debugging.

## Hardware Flow

The hardware architecture is centered on linked systolic arrays with
configurable SRAM resources and configurable pipeline depths.

Expected hierarchy:

- `l1`: top-level accelerator composition, model/layer orchestration, host or
  device-facing interfaces, target wrapper selection.
- `l2`: systolic array clusters, SRAM subsystems, stream routers, DMA/control
  blocks, scheduling blocks, target adapters.
- `l3`: MAC cells, FIFOs, register slices, SRAM wrappers, arbiters, adapters,
  and other leaf modules.

L1 modules should make the system structure obvious. L2 and L3 modules may be
more detailed, but every interface must be documented.

## Architecture JSON

Architecture JSON is the source for user-controlled hardware structure. The
schema will evolve, but files should follow this shape:

```json
{
  "target": {
    "kind": "fpga"
  },
  "architecture": {
    "systolic_arrays": [
      {
        "name": "array0",
        "rows": 16,
        "cols": 16,
        "data_width": 16
      }
    ],
    "links": []
  },
  "memory": {
    "sram": [
      {
        "name": "activation_sram",
        "size_bytes": 262144,
        "data_width": 128,
        "banks": 4
      }
    ]
  },
  "pipeline": {
    "default_depth": 2
  }
}
```

Compiler code may reject incomplete JSON, but defaults must be documented in
L2 before Codex relies on them.

## Generated Code

Generated SystemVerilog must be readable enough for human review. Generated
files should identify the pass and architecture JSON that produced them.

Generated companion code may include:

- C++ model wrappers.
- Verilator harnesses.
- Host/device runtime descriptors.
- Target build metadata.
- FPGA constraints or ASIC/OpenROAD constraints.

Generated code must preserve the documented module interfaces.
