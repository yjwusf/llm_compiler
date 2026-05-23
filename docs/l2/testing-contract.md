# Testing Contract

## Test Layers

Tests must match the documentation hierarchy:

- L1 tests verify complete flows, target packaging, and end-to-end behavior.
- L1.5 tests verify one SystemVerilog module running with all surrounding
  system behavior supplied by C++.
- L2 tests verify implementation assumptions, coding conventions, pass
  contracts, and architecture JSON semantics.
- L3 tests verify individual modules with C++ models, Verilator, and VIPs.

Peripheral tests must be digital-only unless an L1 document explicitly adds a
mixed-signal requirement.

## Module Verification

Every SystemVerilog module requires:

- C++ model for expected behavior.
- L1.5 hybrid run where the module is the only SystemVerilog instance and all
  other behavior is C++.
- C++ performance counters for the hybrid run.
- Verilator harness for running the module.
- Module-local VIP that targets only that module.
- Tests covering reset, nominal behavior, backpressure if applicable, and at
  least one parameterized configuration when the module has parameters.
- Tests confirming required C++ performance counters increment for at least one
  representative run.

The VIP for one module must not depend on another module's implementation. It
may instantiate adapters or drivers, but the device under test must be exactly
the documented module.

The L1.5 harness for one module must also avoid depending on any other
SystemVerilog module. All neighbors must be represented by C++ models, C++
mocks, or explicit C++ adapters.

Ethernet/RGMII VIPs must drive and monitor digital RGMII pins and internal
streams. They must not require analog PHY models, mixed-signal simulators, or
off-chip DRAM traffic sources.

## Mock Verification

Mock modules are allowed during early development. A mock is acceptable only
when:

- Its interface is documented in L3.
- Its C++ model defines the intended inputs and outputs.
- Its L1.5 hybrid run can execute the mock with a C++ environment.
- Its C++ performance counters report the documented module-local events.
- Verilator can compile and run it.
- Its VIP checks behavior at the module boundary.

Mock behavior should be simple and deterministic. If a mock intentionally does
not implement final behavior, document the limitation in the L3 module file.

## Implementation Equivalence

Each replaceable IP has implementation slots:

- `imp1` is the accepted mock contract implementation.
- `imp2` is a candidate implementation slot for real RTL, including upstream
  CPU or systolic-array IP adapted to the E1-H1 interface.

An `imp2` implementation is not selectable until all module-local VIP cases run
through Verilator with DPI scoreboarding against `imp1`. The VIP must generate
sensible bounded input/output streams for the module interface, the DPI
scoreboard must compare the externally visible behavior and counters, and the
candidate RTL filelist must be gathered into the generated implementation
flists.

## Compiler Pass Tests

Each compiler pass must have tests that cover:

- Accepted input form.
- Rejected invalid input form.
- Output artifact or IR shape.
- Relevant architecture JSON fields.
- External data-source behavior, including Ethernet/RGMII selection when
  applicable.

Pass tests should compare structured artifacts when possible instead of relying
only on fragile string matching.

## Future Formal And End-To-End Tests

Formal checks will be added after the initial module test structure is stable.
Formal properties should reference the same behavior described in L3 module
docs.

End-to-end tests will compile MLIR model fragments through generated hardware
and compare results against C++ reference behavior.
