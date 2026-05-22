# L1.5 Hybrid Execution

L1.5 defines the execution layer between the human-readable L1 architecture and
the implementation details in L2/L3. Its job is to make every SystemVerilog
module runnable in isolation while the rest of the system is supplied by C++.

## Contract

Every SystemVerilog module must support a hybrid execution mode:

- Exactly one SystemVerilog module is the device under test.
- All upstream, downstream, memory, control, peripheral, and environment
  behavior is provided by C++ models, mocks, or adapters.
- The module runs through Verilator or an equivalent documented simulator path.
- The same C++ behavioral model used by module tests is available to score or
  compare the module.
- C++ records performance counters for the run.

A module is not complete until it can be run individually in this mode.

## Mocked Module Runs

Mock SystemVerilog modules must also support L1.5 runs. For mocks, the C++
environment defines the expected inputs and outputs, while the mock RTL
preserves the real module boundary and handshake behavior.

The L1.5 harness should make it possible to replace a mocked RTL module with
real RTL without changing the surrounding C++ environment contract.

## C++ Performance Counters

Initial performance counters live in C++ harness code, not in generated RTL.
The C++ harness must be able to count at least:

- Cycles observed at the module clock.
- Input transfers accepted by the module.
- Output transfers produced by the module.
- Backpressure or stall cycles when the module uses ready/valid interfaces.
- Error events exposed by documented module outputs.
- Optional module-specific events listed in the module's L3 document.

Later RTL performance counters may be added, but they must be cross-checked
against the C++ counters before they become authoritative.

## Harness Boundary

Each module's L1.5 harness must document:

- The SystemVerilog module under test.
- Which C++ models replace its neighbors.
- The module-local VIP manifest that targets only that module.
- How clocks and resets are generated.
- How input stimuli are produced.
- How outputs are checked.
- Which performance counters are collected.
- How to run the harness.

The harness must not depend on another SystemVerilog module being present. If a
neighbor is needed for context, use the neighbor's C++ model or an explicit C++
mock.

Each VIP manifest must be module-local. Its `allowed_systemverilog_modules`
list must contain exactly the DUT module, and all other behavior must be
provided by the C++ environment named by the harness.

## Relationship To Other Docs

- L1 defines architecture intent and required behavior.
- L1.5 defines hybrid C++/SystemVerilog execution and performance observation.
- L2 records implementation assumptions and coding conventions.
- L3 records each module's interface, behavior, harnesses, counters, and tests.
