# E1-H1 Module Specs

Each replaceable E1-H1 IP manifest must point at one module spec in this
directory. These specs are the compatibility boundary for generated SoC-top
composition, C++ chip-model behavior, L1.5 hybrid execution, VIPs, and tests.

Required sections:

- Purpose
- Parameters
- Input Signals
- Output Signals
- Interface Protocol
- Replacement Compatibility
- Mock Behavior
- C++ Model Contract
- L1.5 Hybrid Execution
- Module VIP
- Implementation Versions
- C++ Performance Counters
- Tests

## RTL Lowering And Module DPI

Full RTL lowering must stay correct by construction: each replaceable IP is
lowered behind the interface in its IP manifest, and each `imp2` candidate is
tested against the accepted `imp1` mock through a generated module-only DPI
probe before it is treated as selectable RTL.

The generated module-DPI artifacts are owned by:

- `e1/e1-h1/tools/generate_module_dpi.cpp`
- `e1/e1-h1/generated/module_dpi/manifest.json`
- `e1/e1-h1/generated/module_dpi/flists/*.f`

The generator validates the existing IP and VIP manifests, then emits one
SystemVerilog probe per module. Each generated probe instantiates exactly one
candidate SystemVerilog DUT and supplies all neighboring behavior from the C++
DPI environment. The older all-module DPI probe remains a smoke test, but it is
not the replacement boundary.

## Cycle Diagram

The first separated RTL path is CPU command issue, an explicit latched buffer,
and the systolic array. The cycles below are the named phases used by the
generated probes and are the review reference for future lowering passes.

```text
Cycle      control_cpu module       ingress_sram latch buffer        systolic_array module
-----      ------------------       -------------------------        ---------------------
reset      state_q = Reset          buffered_valid_q = 0             busy_q = 0
0          reset releases           latches first stream word        idle, cmd_ready_o = 1
1          cmd_valid_o under        holds data while                 accepts array command
           backpressure             array_ready_i = 0                and enters busy
2          command handshake        releases latched word            busy, input_ready_o = 1
3          waits for completion     latches next clean word          consumes input beat 1
4          waits for completion     rejects error-marked word        consumes input beat 2
5          observes array_done_i    empty or ready for next word     consumes input beat 3
6          enters halted state      empty                            consumes beat 4 and
                                                                    pulses done_o
7          debug_halted_o = 1       idle                             idle, cmd_ready_o = 1
```

Separation rules:

- The CPU probe never instantiates systolic-array RTL; ready, done, and error
  are DPI-driven inputs.
- The systolic-array probe never instantiates CPU RTL; command and input stream
  stimulus are DPI-driven inputs.
- The ingress SRAM is the explicit latch buffer between Ethernet/RGMII ingress
  and the array stream. Its probe must show data held while downstream ready is
  low and released on a later cycle.

## Full Checkpoint Tile-Cycle Template

The generated full-checkpoint linear scheduler uses the same separation for
every planned TinyLlama tile command. The command stream remains compact, but
each command lowers to this 8-cycle review template:

```text
Tile cycle  control_cpu responsibility        ingress_sram latch buffer      systolic_array responsibility
----------  --------------------------        -------------------------      -----------------------------
0           setup next tile command           ready for staged input         idle or previous command done
1           cmd_valid_o asserted              may hold staged input          observes valid command
2           command handshake accepted        releases staged input          enters busy
3           waits for array progress          beat 0 visible to array        consumes input beat 0
4           waits for array progress          next beat may stage            consumes input beat 1
5           waits for array progress          next beat may stage            consumes input beat 2
6           observes array_done_i/error_i     last beat may stage            consumes beat 3 and pulses done
7           advances layer/op/tile counters   returns to ready/empty         returns to ready
```

The generated artifacts are:

- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler.f`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler_tb.cpp`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine.f`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine_tb.cpp`

The Verilator harness checks sampled RTL command payloads against
`e1/code/program/e1_tinyllama_full_schedule.hpp`; the pipeline report records
3,784,704 tile commands and 30,277,632 planned tile-template cycles. This is
linear-command RTL lowering evidence, not yet full graph RTL execution.

The tile-engine harness additionally wires the scheduler to the explicit
`ingress_sram` latch buffer and `systolic_array` RTL. It checks that the
scheduler command-valid phase remains separate from the array handshake phase,
that the latch buffer holds data while the array is not ready, and that the
array consumes latched input beats after command acceptance.
