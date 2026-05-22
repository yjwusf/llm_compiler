# E1 Docs

E1 is the first complete example flow. It starts with TinyLlama, inspects
StableHLO/MLIR output, plans the compiler passes through generated
SystemVerilog, and begins the C++ chip model and device program.

## Documents

- [TinyLlama and StableHLO capture](tinyllama-stablehlo.md)
- [End-to-end pass plan](pass-plan.md)
- [Generated C++ chip model](generated-chip-model.md)
- [Device code](device-code.md)
- [Upstream notes](upstream-notes.md)
- [E1-H1 architecture](../e1-h1/docs/architecture.md)
- [E1-H1 generated SoC top](../e1-h1/docs/generated-soc-top.md)
- [E1-H1 target packaging](../e1-h1/docs/target-packaging.md)
- [E1-H1 module replacement](../e1-h1/docs/module-replacement.md)

## Rules

- E1 docs follow the root documentation contract.
- E1-H1 module interfaces are the compatibility boundary. Implementations may
  be replaced only when those interfaces remain constant or the docs and tests
  are updated in the same change.
- Any code added under `e1/code/` must be understandable without reverse
  engineering generated names.
