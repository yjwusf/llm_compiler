# E1-H1 Module Replacement

## Rule

Every module in E1-H1 is replaceable. The replacement boundary is the documented
interface, not the implementation language or upstream inspiration.

If an implementation changes but the interface is constant, dependent modules
and device code must keep working. If the interface changes, the docs, C++
model, L1.5 harness, VIP, and tests must change in the same patch.

## Required Per-Module Interface Definition

Each E1-H1 module must document:

- Input and output signals.
- C++ model entry points.
- L1.5 hybrid run command.
- C++ performance counters.
- Device-visible programming interface when applicable.
- JSON fields that parameterize it.
- Replacement-compatible behavior.

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
