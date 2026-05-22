# Testing Contract

## Test Layers

Tests must match the documentation hierarchy:

- L1 tests verify complete flows, target packaging, and end-to-end behavior.
- L2 tests verify implementation assumptions, coding conventions, pass
  contracts, and architecture JSON semantics.
- L3 tests verify individual modules with C++ models, Verilator, and VIPs.

## Module Verification

Every SystemVerilog module requires:

- C++ model for expected behavior.
- Verilator harness for running the module.
- Module-local VIP that targets only that module.
- Tests covering reset, nominal behavior, backpressure if applicable, and at
  least one parameterized configuration when the module has parameters.

The VIP for one module must not depend on another module's implementation. It
may instantiate adapters or drivers, but the device under test must be exactly
the documented module.

## Mock Verification

Mock modules are allowed during early development. A mock is acceptable only
when:

- Its interface is documented in L3.
- Its C++ model defines the intended inputs and outputs.
- Verilator can compile and run it.
- Its VIP checks behavior at the module boundary.

Mock behavior should be simple and deterministic. If a mock intentionally does
not implement final behavior, document the limitation in the L3 module file.

## Compiler Pass Tests

Each compiler pass must have tests that cover:

- Accepted input form.
- Rejected invalid input form.
- Output artifact or IR shape.
- Relevant architecture JSON fields.

Pass tests should compare structured artifacts when possible instead of relying
only on fragile string matching.

## Future Formal And End-To-End Tests

Formal checks will be added after the initial module test structure is stable.
Formal properties should reference the same behavior described in L3 module
docs.

End-to-end tests will compile MLIR model fragments through generated hardware
and compare results against C++ reference behavior.
