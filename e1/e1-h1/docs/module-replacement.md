# E1-H1 Module Replacement

## Rule

Every module in E1-H1 is replaceable. The replacement boundary is the documented
interface, not the implementation language or upstream inspiration.

If an implementation changes but the interface is constant, dependent modules
and device code must keep working. If the interface changes, the docs, C++
model, L1.5 harness, VIP, and tests must change in the same patch.

The generated interface lock file is:

- `e1/e1-h1/generated/e1_h1_interface_contracts.json`

It is generated from `e1/e1-h1/ip/*.json`. Each IP has a
`signature_sha256` over the stable interface payload: IP name, subsystem,
parameters, ports, connections, and performance counters. The implementation
module name is recorded for build generation but is not part of the interface
signature, so a replacement implementation can use a different module name when
the manifest is updated without changing the stable interface.

Each IP also points at a module-local VIP manifest under `e1/e1-h1/vip/`.
The VIP manifest is not a multi-module integration test: it must allow exactly
one SystemVerilog DUT, and all neighbors must be supplied by the C++ L1.5
environment.

## Required Per-Module Interface Definition

Each E1-H1 module must document:

- Input and output signals.
- C++ model entry points.
- L1.5 hybrid run command.
- Module-local VIP manifest.
- C++ performance counters.
- Device-visible programming interface when applicable.
- JSON fields that parameterize it.
- Replacement-compatible behavior.
- Generated interface signature in `e1_h1_interface_contracts.json`.

## CPU Interface

The CPU implementation is replaceable if it preserves:

- Reset and clock behavior.
- Bare-metal entry behavior.
- MMIO reads and writes to the accelerator command interface.
- Interrupt or polling completion behavior, whichever E1-H1 documents.
- Access to on-chip SRAM windows needed by the device program.

## Systolic Array Interface

The systolic array implementation is replaceable if it preserves:

- Command acceptance semantics.
- Tile address and dimension fields.
- Input, weight, accumulator, and output stream meanings.
- Completion and error reporting.
- C++ model behavior for documented operations.

Gemmini may inform the first implementation, but E1-H1 must not expose a
Gemmini-specific implementation detail unless it becomes part of the documented
interface.
