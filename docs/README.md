# Documentation

This repository is documented by level:

- `l1/`: project contract, user-visible architecture, and execution plan.
- `l1_5/`: hybrid C++/SystemVerilog execution, mocked-module runs, and C++
  performance counter requirements.
- `l2/`: implementation assumptions and conventions that Codex may use, but
  that humans will review sporadically.
- `l3/`: module-level specifications, signal tables, and verification
  requirements.

The L1 documents are the primary architecture entry point. A human should be
able to read `docs/l1/` and `docs/l1_5/` and understand how the compiler,
generated hardware, C++ hybrid execution, and test strategy fit together.

## Documentation Rules

1. Any change that affects compiler behavior, architecture JSON semantics,
   SystemVerilog interfaces, C++ model behavior, L1.5 hybrid execution, C++
   performance counters, VIP behavior, target backend packaging, or tests must
   update the relevant docs in the same change.
2. Tests must conform to the docs. A test should name or reference the
   documented behavior it verifies.
3. Every SystemVerilog module must have an L3 module document before it
   graduates from a mock implementation.
4. Every L3 module document must list all input and output signals with
   descriptions.
5. Every L3 module document must list the L1.5 hybrid run and C++ performance
   counters for that module.
6. If Codex needs to assume an implementation detail that is not yet approved
   in L1, it must add that detail to `docs/l2/assumptions.md`.

## Current Documents

- [L1 project contract](l1/contract.md)
- [L1 execution plan](l1/plan.md)
- [L1 architecture](l1/architecture.md)
- [L1 peripherals](l1/peripherals.md)
- [L1.5 hybrid execution](l1_5/README.md)
- [L2 assumptions](l2/assumptions.md)
- [L2 coding style](l2/coding-style.md)
- [L2 testing contract](l2/testing-contract.md)
- [L3 module index](l3/module-index.md)
- [L3 module template](l3/module-template.md)

## Examples

- [E1 TinyLlama-to-chip example](../e1/README.md)
