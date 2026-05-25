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
probe before it is treated as selectable RTL. The pipeline also parses each
selected base `imp2` SystemVerilog module and checks that the generated signal
documentation and IP manifest ports match the actual RTL port contract.

The generated module-DPI artifacts are owned by:

- `e1/e1-h1/tools/generate_module_dpi.cpp`
- `e1/e1-h1/generated/module_dpi/manifest.json`
- `e1/e1-h1/generated/module_dpi/module_interfaces.md`
- `e1/e1-h1/generated/module_dpi/module_isolation.json`
- `e1/e1-h1/generated/module_dpi/cycle_contract.json`
- `e1/e1-h1/generated/module_dpi/module_test_plan.json`
- `e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json`
- `e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_verilator_launcher.cpp`
- `e1/e1-h1/generated/module_dpi/verilator_execution_report.json`
- `e1/e1-h1/generated/module_dpi/flists/*.f`

The generator validates the existing IP and VIP manifests, then emits one
SystemVerilog probe per module. Each generated probe instantiates exactly one
candidate SystemVerilog DUT plus exactly one `imp1` reference oracle, and
supplies all neighboring behavior from the C++ DPI environment. The isolation
proof and construction ledger record those exact instance counts for every
module. The older all-module DPI probe remains a smoke test, but it is not the
replacement boundary. The generator also emits machine-checkable
isolation, cycle-contract, Verilator test-plan, C++-owned Verilator execution
recipe, and Verilator execution artifacts so the CPU, RGMII ingress, latch
buffer, SRAM shells, and systolic-array probes can be reviewed and run with the
same construction-proof shape as generated full-checkpoint RTL modules. It also
emits `e1/e1-h1/generated/module_dpi/module_interfaces.md`, so each base IP's
input and output signal table is generated from the same C++ module spec that
emits its probe, flist, cycle contract, and Verilator recipe. The generated
Verilator execution report records the first observed DPI phase trace and
checks that it matches the generated cycle order. It also records every
declared module-VIP stream-space case as a generated DPI case marker, so the
base module-only run is tied back to the VIP cases in `e1/e1-h1/vip/*.json`.
The pipeline module-DPI report cross-checks each generated probe, main, flist,
top module, and active `imp2` RTL path against
`e1/e1-h1/generated/implementation_matrix.json`, so module-only execution is
tied to the selected implementation matrix. It also records the sanitized C++
generator build command, run command, working directory, and normalized stdout
from the generator run, including the expected generated module count. The same
report compiles and dry-runs the generated C++ Verilator launcher at
`e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_verilator_launcher.cpp`, then
checks every launcher module record against the generated Verilator execution
recipe. It also runs that launcher in `--run --build-root <dir>` mode, so the
C++ launcher itself builds and executes every base module-only Verilator test
and emits a pass/fail result for each module. The C++ launcher captures the
module executable stdout, compares it against the expected marker list from the
Verilator execution recipe, reports missing markers, and fails the module if
any expected marker is absent. It also checks the ordered `cycle:phase` trace
and the phase-signal trace from the module executable before reporting a
passing module result, including every repeated occurrence observed after the
first cycle-template prefix. The systolic-array module run additionally checks
`result_digest_o` against a C++ DPI scoreboard value on every documented cycle,
so the current placeholder datapath has an observable result signal while full
matrix numeric equivalence remains a later proof.
The isolation reports themselves carry top-level `status`, `checks`, and
`separated_boundaries` fields, so the construction certificate can fail if the
CPU, latch-buffer, and systolic-array boundaries stop being explicit.
The construction certificate also carries an objective-coverage table tying
full RTL lowering, construction checks, C++ DPI generation, module-only runs,
active SystemVerilog standalone runtime runs, separated boundaries,
latch-buffer behavior, and this README's cycle diagrams to concrete generated
evidence. The standalone runtime row includes the generated SoC top's C++
Verilator testbench proof as well as the C++-generated module-DPI launchers for
base and full-checkpoint RTL. The same table also checks that Verilator
observed the generated phase traces in the cycle order documented here. The
certificate's `objective_traceability_audit` maps each human-requested clause
for full RTL lowering, construction checking, C++ DPI generation, module-only
SystemVerilog runs, separated CPU/latch/systolic boundaries, and README cycle
diagrams to the exact evidence rows that prove the current structural scope.

## Cycle Diagram

The first separated RTL path is CPU command issue, an explicit latched buffer,
and the systolic array. The cycles below are the named phases used by the
generated probes and are the review reference for future lowering passes.

```text
Cycle-boundary topology

rgmii_ethernet_ingress/VIP stream
  |
  | cycle 0 latch first stream word
  v
ingress_sram explicit latch buffer
  | cycle 1 hold while array_ready_i = 0
  | cycle 2 release latched word into systolic_array
  v
systolic_array standalone RTL
  ^ cycle 1 accept command, cycles 3..6 consume beats
  |
control_cpu standalone RTL command issue
  | cycle 1 assert cmd_valid_o, cycle 2 handshake, cycle 5 observe done
  v
debug_halted_o at cycle 7 after the CPU, latch buffer, and array return idle
```

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

## Module-Only Boundary Matrix

The table below is the human-readable review map for the generated module-only
DPI flists. Each row names the one SystemVerilog DUT allowed in the module run;
everything else at that boundary is owned by the C++ DPI/VIP environment or by
probe-local stubs.

| Boundary | SystemVerilog DUT in module-only flist | C++/DPI-owned neighbors | Machine proof |
| --- | --- | --- | --- |
| `control_cpu` | `e1_h1_control_cpu` plus generated per-module `imp1` reference and probe | Array ready/done/error inputs, memory responses, and ingress traffic | `e1/e1-h1/generated/module_dpi/module_isolation.json` |
| `ingress_sram` latch buffer | `e1_h1_stream_sram` plus generated per-module `imp1` reference and probe | Ethernet/RGMII stream source and array ready backpressure | `e1/e1-h1/generated/module_dpi/module_isolation.json` |
| `systolic_array` | `e1_h1_systolic_array` plus generated per-module `imp1` reference and probe | CPU command stream and input payload stream | `e1/e1-h1/generated/module_dpi/module_isolation.json` |
| Generated full-checkpoint module | Selected generated RTL plus its probe only | Sibling modules are absent from the flist; allowed children are probe-local stubs | `e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json` |

The lowering construction certificate also emits `module_boundary_taxonomy`,
which classifies every active runtime boundary before the proof is accepted:

| Role | Boundaries |
| --- | --- |
| CPU/control | `control_cpu`, `control_scheduler`, `control_slot_engine`, `graph_sequencer` |
| Digital ingress | `rgmii_ethernet_ingress` |
| Latch buffer | `ingress_sram` |
| SRAM shell | `activation_sram`, `accumulator_sram` |
| Systolic array | `systolic_array` |
| Linear systolic path | `linear_scheduler`, `linear_tile_engine`, `linear_slot_engine` |
| Top glue | `full_checkpoint_top`, `generated_soc_top` |

Each source-derived taxonomy entry points to the module-only isolation proof,
Verilator execution report, cycle contract, README cycle coverage, interface
documentation, and standalone runtime inventory row. The generated SoC top is
classified as top glue and is covered by the hierarchy proof plus the
standalone C++ Verilator smoke rather than a module-only cycle template.
The same certificate emits `module_interface_signal_inventory`, which turns the
generated interface markdown into audited data: every module has input and
output signal tables, every listed signal carries a description, and the
machine-readable signal lists must match the parsed RTL port contract before
the certificate passes. It also emits
`systemverilog_module_coverage_audit`, which lists every production RTL row,
the parsed module names, the required run scope, and the single-DUT
module-DPI runtime proof for each active source-derived module. The companion
`systemverilog_defined_module_runtime_audit` expands that to one row per parsed
module name, assigning each production SystemVerilog module to module-only
DPI/Verilator, standalone-top Verilator, or generated C++ Verilator runtime
plus lint and contract scope for accepted `imp1` mocks. The companion
`cycle_diagram_audit` ties the README cycle rows to generated cycle contracts
and observed Verilator phase traces. The certificate also emits
`dpi_generation_provenance_audit`, which ties the C++ generator sources,
sanitized generator build/run records, emitted module-DPI artifacts, generated
C++ Verilator launchers, and per-module launcher runtime results into one
auditable section.

## Full Checkpoint Tile-Cycle Template

The generated full-checkpoint linear scheduler uses the same separation for
every planned TinyLlama tile command. The command stream remains compact, but
each command lowers to this 8-cycle review template:

```text
Full-checkpoint slot topology

graph_sequencer -> selected slot engine -> linear_tile_engine
linear_tile_engine cycle 0 selects tile command metadata
linear_tile_engine cycle 1 drives command valid to standalone systolic_array
linear_tile_engine cycle 2 observes command handshake
linear_tile_engine cycles 3..6 routes stream beats through ingress_sram latch buffer
linear_tile_engine cycle 7 commits the tile and returns the separated modules to ready
```

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
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler.f`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler_tb.cpp`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer.f`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer_tb.cpp`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_slot_engine.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_slot_engine.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top.sv`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top.f`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_tb.cpp`
- `e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp`
- `e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp`
- `e1/e1-h1/generated/full_checkpoint_dpi/manifest.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md`
- `e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json`
- `e1/e1-h1/generated/full_checkpoint_dpi/flists/*.f`

The Verilator harness checks sampled RTL command payloads against
`e1/code/program/e1_tinyllama_full_schedule.hpp`; the pipeline report records
3,784,704 tile commands and 30,277,632 planned tile-template cycles. This is
the linear-command RTL-cycle evidence consumed by the full-checkpoint top
integration below, where graph-slot dispatch and the complete command stream
are run under Verilator at the structural execution boundary.
The full-checkpoint DPI generator also parses each generated RTL module's
SystemVerilog port list and fails if `module_interfaces.md` omits, reorders, or
mis-sizes any documented input or output signal.

The tile-engine harness additionally wires the scheduler to the explicit
`ingress_sram` latch buffer and `systolic_array` RTL. It checks that the
scheduler command-valid phase remains separate from the array handshake phase,
that the latch buffer holds data while the array is not ready, and that the
array consumes latched input beats after command acceptance.

The CPU/control scheduler covers the non-linear graph slots with this
four-cycle template:

```text
Control cycle  control_cpu responsibility
-------------  --------------------------
0              issue graph-slot command and hold under backpressure
1              read source/control metadata
2              execute scalar or vector-control operation
3              commit graph-slot result and advance layer/op counters
```

The generated control sequence has seven graph slots per layer:
`input_rms_norm`, `rope_qk`, `attention_scores_softmax`,
`post_attention_residual`, `post_attention_rms_norm`, `silu_gate_multiply`, and
`post_mlp_residual`.

The graph sequencer wraps the ordered layer template with this four-cycle
launch protocol:

```text
Graph cycle  control_cpu responsibility
-----------  --------------------------
0            present ordered layer graph slot and hold under backpressure
1            launch either CPU/control scheduler or linear tile engine
2            wait for launched engine completion
3            commit graph slot and advance layer/slot counters
```

This sequencer preserves the review-visible TinyLlama order:
RMSNorm, Q/K/V projections, RoPE, attention softmax/control, output
projection, residual, post-attention RMSNorm, MLP gate/up projections, SiLU
gate multiply, down projection, and MLP residual.

The full-checkpoint top connects that sequencer to slot-scoped engines with
this review-visible cycle boundary:

```text
Top cycle  graph_sequencer responsibility       selected slot engine
---------  ------------------------------       --------------------
0          presents next graph slot              idle or finishing prior slot
1          pulses exactly one start              latches layer/op selection
2          holds current graph slot              runs CPU/control or linear slot
3          commits graph slot                    returns done to sequencer
```

## Full Graph Slot Cycle Coverage

The generated full-graph proof checks the exact phase names below against this
README before it records graph-slot RTL lowering as passing.

| Template | Applies To Slots | Cycle Phases |
| --- | ---: | --- |
| `tile_command_8_cycle_cpu_latch_array_template` | 154 | 0 `reset_release_or_next_command_setup`; 1 `cmd_valid_o asserted under allowed backpressure`; 2 `command handshake accepted`; 3 `latched input beat 0 visible to array`; 4 `input beat 1 consumed`; 5 `input beat 2 consumed`; 6 `input beat 3 consumed and done observed`; 7 `advance to next tile command` |
| `control_op_4_cycle_cpu_template` | 154 | 0 `issue control op and allow backpressure`; 1 `read source/control metadata`; 2 `execute scalar or vector-control operation`; 3 `commit control op and advance graph slot` |
| `graph_slot_4_cycle_launch_template` | 308 | 0 `present ordered graph slot and allow backpressure`; 1 `launch CPU/control or linear tile engine`; 2 `wait for launched engine completion`; 3 `commit graph slot and advance layer/slot counters` |
| `top_dispatch_4_cycle_slot_engine_template` | 308 | 0 `present next graph slot`; 1 `pulse one slot engine start`; 2 `hold graph slot until selected engine done`; 3 `commit slot and advance` |

### Generated Cycle Contract Index

The tables below are the machine-checked index for the generated
`cycle_contract.json` files. The C++ DPI generators fail if a module name,
template name, phase name, or exact ordered cycle-index row in those contracts
is missing here.

Base module cycle contracts:

| Module | Template | Cycle Phases |
| --- | --- | --- |
| `control_cpu` | `cpu_command_8_cycle_template` | 0 `reset_release`; 1 `command_backpressure`; 2 `command_handshake`; 3 `wait_for_array`; 4 `wait_for_array_stable`; 5 `array_completion`; 6 `halt_transition`; 7 `halted_idle` |
| `rgmii_ethernet_ingress` | `digital_rgmii_ingress_10_cycle_template` | 0 `idle_after_reset`; 1 `frame_nibble_0`; 2 `frame_nibble_1`; 3 `frame_nibble_2`; 4 `frame_nibble_3`; 5 `frame_nibble_4`; 6 `frame_gap`; 7 `downstream_accept`; 8 `drain_stream`; 9 `return_idle` |
| `ingress_sram` | `latch_buffer_6_cycle_template` | 0 `latch_first_word`; 1 `hold_latched_word`; 2 `release_latched_word`; 3 `latch_next_clean_word`; 4 `reject_error_word`; 5 `empty_or_ready` |
| `activation_sram` | `config_sram_3_cycle_template` | 0 `initialization_latch`; 1 `initialized_hold_0`; 2 `initialized_hold_1` |
| `accumulator_sram` | `config_sram_3_cycle_template` | 0 `initialization_latch`; 1 `initialized_hold_0`; 2 `initialized_hold_1` |
| `systolic_array` | `systolic_array_8_cycle_template` | 0 `array_idle`; 1 `accept_array_command`; 2 `enter_busy`; 3 `consume_input_beat_0`; 4 `consume_input_beat_1`; 5 `consume_input_beat_2`; 6 `completion_pulse`; 7 `return_ready` |

Full-checkpoint generated module cycle contracts:

| Module | Template | Cycle Phases |
| --- | --- | --- |
| `linear_scheduler` | `tile_command_8_cycle_cpu_latch_array_template` | 0 `setup_tile_command`; 1 `assert_scheduler_valid`; 2 `accept_command_handshake`; 3 `wait_for_array_progress_0`; 4 `wait_for_array_progress_1`; 5 `wait_for_array_progress_2`; 6 `sample_array_done`; 7 `advance_tile_counters` |
| `linear_tile_engine` | `tile_command_8_cycle_cpu_latch_array_template` | 0 `setup_tile_engine`; 1 `scheduler_valid_visible`; 2 `array_command_handshake`; 3 `latch_to_array_beat_0`; 4 `latch_to_array_beat_1`; 5 `latch_to_array_beat_2`; 6 `array_done_pulse`; 7 `return_ready` |
| `control_scheduler` | `control_op_4_cycle_cpu_template` | 0 `issue_control_op`; 1 `read_control_metadata`; 2 `execute_control_op`; 3 `commit_control_op` |
| `graph_sequencer` | `graph_slot_4_cycle_launch_template` | 0 `present_graph_slot`; 1 `launch_selected_engine`; 2 `wait_for_slot_done`; 3 `commit_graph_slot` |
| `linear_slot_engine` | `tile_command_8_cycle_cpu_latch_array_template` | 0 `latch_selected_linear_slot`; 1 `slot_command_valid`; 2 `array_command_handshake`; 3 `latch_to_array_beat_0`; 4 `latch_to_array_beat_1`; 5 `latch_to_array_beat_2`; 6 `array_done_pulse`; 7 `slot_done_or_next_tile` |
| `control_slot_engine` | `control_op_4_cycle_cpu_template` | 0 `issue_selected_control_slot`; 1 `read_selected_control_metadata`; 2 `execute_selected_control_slot`; 3 `commit_selected_control_slot` |
| `full_checkpoint_top` | `top_dispatch_4_cycle_slot_engine_template` | 0 `present_top_graph_slot`; 1 `start_selected_slot_engine`; 2 `run_selected_slot_engine`; 3 `commit_top_graph_slot` |

### Module Cycle Runtime Matrix

This table is the human-readable bridge from the cycle-contract rows above to
module-only Verilator runtime evidence. The construction certificate requires
each exact row below to match the generated `cycle_diagram_audit`, so every
module's documented cycle template has a corresponding observed phase trace and
phase-signal trace.

| Suite | Module | Top Module | Template | Contract Cycles | Observed Phase Trace Records | Observed Phase-Signal Trace Records | Runtime |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| `base_module_dpi` | `control_cpu` | `e1_h1_control_cpu` | `cpu_command_8_cycle_template` | 8 | 8 | 8 | `pass` |
| `base_module_dpi` | `rgmii_ethernet_ingress` | `e1_h1_rgmii_ethernet_ingress` | `digital_rgmii_ingress_10_cycle_template` | 10 | 10 | 10 | `pass` |
| `base_module_dpi` | `ingress_sram` | `e1_h1_stream_sram` | `latch_buffer_6_cycle_template` | 6 | 6 | 6 | `pass` |
| `base_module_dpi` | `activation_sram` | `e1_h1_config_sram` | `config_sram_3_cycle_template` | 3 | 3 | 3 | `pass` |
| `base_module_dpi` | `accumulator_sram` | `e1_h1_config_sram` | `config_sram_3_cycle_template` | 3 | 3 | 3 | `pass` |
| `base_module_dpi` | `systolic_array` | `e1_h1_systolic_array` | `systolic_array_8_cycle_template` | 8 | 8 | 8 | `pass` |
| `generated_full_checkpoint_module_dpi` | `linear_scheduler` | `e1_h1_tinyllama_linear_scheduler` | `tile_command_8_cycle_cpu_latch_array_template` | 8 | 27 | 27 | `pass` |
| `generated_full_checkpoint_module_dpi` | `linear_tile_engine` | `e1_h1_tinyllama_linear_tile_engine` | `tile_command_8_cycle_cpu_latch_array_template` | 8 | 27 | 27 | `pass` |
| `generated_full_checkpoint_module_dpi` | `control_scheduler` | `e1_h1_tinyllama_control_scheduler` | `control_op_4_cycle_cpu_template` | 4 | 616 | 616 | `pass` |
| `generated_full_checkpoint_module_dpi` | `graph_sequencer` | `e1_h1_tinyllama_graph_sequencer` | `graph_slot_4_cycle_launch_template` | 4 | 1232 | 1232 | `pass` |
| `generated_full_checkpoint_module_dpi` | `linear_slot_engine` | `e1_h1_tinyllama_linear_slot_engine` | `tile_command_8_cycle_cpu_latch_array_template` | 8 | 16 | 16 | `pass` |
| `generated_full_checkpoint_module_dpi` | `control_slot_engine` | `e1_h1_tinyllama_control_slot_engine` | `control_op_4_cycle_cpu_template` | 4 | 4 | 4 | `pass` |
| `generated_full_checkpoint_module_dpi` | `full_checkpoint_top` | `e1_h1_tinyllama_full_checkpoint_top` | `top_dispatch_4_cycle_slot_engine_template` | 4 | 1232 | 1232 | `pass` |

The linear slot engine instantiates the separated `ingress_sram` latch buffer
and `systolic_array` modules. The control slot engine does not instantiate
array RTL. The generated top-level Verilator harness runs all 308 graph slots
and bounds each linear slot to a two-tile smoke so the full graph order is
covered quickly. The generated full-command top harness compiles the same RTL
with `SmokeMaxTilesPerLinearSlot=0` and runs all 3,784,704 planned linear tile
commands through the graph sequencer, selected slot engine, latch buffer, and
systolic-array handshake path. It checks every accepted command payload against
`e1/code/program/e1_tinyllama_full_schedule.hpp`, checks the accepted payload
digest against the same generated C++ schedule, and checks the phase 1
scheduler-valid, phase 2 array-handshake, and phase 6 array-done sequence for
every command. It also checks every CPU/control slot payload and commit against
the generated graph schedule and records a control-slot payload digest. The
pipeline records the actual bounded and full-command Verilator JSON reports in
`e1/generated/pipeline/24_full_checkpoint_rtl_top.json`. This is full
command-stream RTL execution evidence. The report exposes scoped
`full_checkpoint_rtl_execution: true` for
`structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence`
and exposes
`full_checkpoint_structural_rtl_execution: true` when the bounded graph-slot
smoke, full-command run, linear payload checks, control-slot payload checks,
digest checks, and documented phase checks all pass; it still does not claim
TinyLlama numeric output equivalence.
The pipeline also emits
`e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json`, which
ties the layer plan, generated graph sequencer, slot engines, latch buffer,
systolic array, and full-command harness into one construction proof that the
ordered TinyLlama graph slots are lowered to RTL slot-engine dispatch. Its
`slot_bindings` table enumerates all 308 layer/slot instances with the selected
slot engine, cycle template, separated modules, and module-DPI probe. Its
`readme_cycle_coverage` block proves the full-graph slot templates and exact
phase names are listed in this README's cycle tables.

The generated full-checkpoint module-DPI collateral applies the same
module-only rule to generated RTL modules. The C++ generator emits one probe
per generated module: linear scheduler, linear tile engine, control scheduler,
graph sequencer, linear slot engine, control slot engine, and full-checkpoint
top. Each probe records cycle notes through DPI and has its own flist and C++
main, so review can build and run the modules individually. The same generator
also emits `e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md`, which
is the generated input/output signal table for every full-checkpoint RTL module,
and `e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json`, which lists
the allowed and forbidden RTL child modules for each generated DUT. The same
generator emits `e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json`,
which names every cycle in each generated module's phase template and links it
back to this README's cycle diagrams. It also emits
`e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json`, which records
the Verilator top, flist, scoreboard, C++ main, and expected output markers for
each generated module-only run. The C++-generated
`e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json` owns
the exact build command, obj-dir convention, and run executable consumed by the
runner. The generated C++ launcher
`e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_verilator_launcher.cpp`
emits one dry-run record per generated module-only Verilator invocation, and
the pipeline requires those records to match the recipe exactly. The same
launcher runs in `--run --build-root <dir>` mode during the pipeline and emits
per-module build/run results for every generated module-only Verilator test.
Those results include the expected stdout marker list, the observed marker
count, and any missing markers so the C++ launcher owns the runtime marker
check. They also include compact ordered phase-trace keys and phase-signal
trace keys so the launcher owns the cycle-order, repeated-template, and
signal-trace checks.
The companion
`e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json`
records the actual build/run result for each generated module-only Verilator
probe and records `phase=<name>` markers proving that every named phase in the
generated cycle contract was emitted by the DPI trace. The report also records
the first observed DPI phase trace and checks that it matches the generated
cycle order. For generated full-checkpoint modules, the probe also samples the
primary RTL phase output on each named cycle: `cycle_phase_o` for module-local
templates and `graph_cycle_phase_o` for the full-checkpoint top. The execution
report records that phase-signal trace and requires it to match the generated
cycle index.
The generated
`e1/e1-h1/generated/full_checkpoint_dpi/construction_ledger.json` ties each C++
module spec to its emitted probe, flist, interface docs, cycle contract, README
phase coverage, and Verilator recipe. The base-IP companion is
`e1/e1-h1/generated/module_dpi/construction_ledger.json`.
`e1/generated/pipeline/27_full_graph_module_dpi_binding.json` binds that
module-only evidence back to the full graph lowering proof. It requires all
generated full-graph RTL modules and the separated base `control_cpu`,
`ingress_sram` latch buffer, and `systolic_array` modules to have passing
module-DPI Verilator reports and passing construction-ledger checks before the
final end-to-end smoke can pass. It also parses those SystemVerilog source
files and records a source-derived coverage row for each parsed module, so the
coverage is tied to the RTL actually emitted into the graph. Each row also
records exact flist entries, probe DUT/reference instance counts, plus
cycle-contract, README-row, ordered phase-trace, generated C++ launcher runtime,
and launcher-recipe match status.
The binding also scans the on-disk
`e1/e1-h1/generated/full_checkpoint/*.sv` and `e1/e1-h1/rtl/imp2/*.sv`
inventories and requires them to match the generated module-DPI RTL set and the
all-base `imp2` RTL set exactly. Every parsed inventory module must resolve to
module-DPI coverage, so extra RTL files or stray top modules fail the report.
The same binding report also records an all-base-module proof for every
replaceable base IP in `e1/e1-h1/generated/module_dpi`, including RGMII ingress
and the SRAM shell configurations, so module-only evidence is not limited to
the current graph path. Each all-base row parses the selected `imp2` RTL source
and proves the expected SystemVerilog top module is defined there. Both the
source-derived coverage rows and the all-base rows carry the matching generated
C++ launcher runtime result, so the binding proof itself checks launcher-owned
stdout markers, ordered phase traces, repeated cycle templates, and phase-signal
traces before later certificate checks consume the binding. The same rows also
prove the launcher result matched the generated recipe command, executable, and
stdout marker contract for that module. Each row also carries a
`cpp_launcher_readme_cycle_proof` that compares the cycle keys from the
generated cycle contract, the README cycle-contract row, and the C++ launcher
observed phase prefix, so a module-only Verilator run is not accepted unless it
observed the same ordered cycle names documented here.
The lowering construction certificate at
`e1/generated/pipeline/28_lowering_construction_certificate.json` ties the
StableHLO fixture operation bindings, full-checkpoint graph slot bindings,
module-DPI reports, target filelists, and README cycle-template coverage into
one machine-checkable proof chain with artifact hashes. The certificate also
emits `active_objective_completion_audit`, which lists the active full-RTL
lowering objective requirements, marks the current verdict as
`proved_for_structural_rtl_lowering_scope`, and repeats the residual non-claims
for TinyLlama tensor numeric equivalence and live checkpoint StableHLO export.
The adjacent `objective_traceability_audit` is the reviewer-facing map from
the original request language to those machine proofs; it lists the evidence
for full RTL lowering, correct-by-construction checks, generated C++ DPI,
module-only runs, separated CPU/latch/systolic boundaries, latch behavior, and
README cycle diagrams while keeping the same residual non-claims.
The audit is intentionally scoped to structural graph-slot and full-command
Verilator execution; it is not a numerical model-equivalence claim. The
certificate also records the `standalone_runtime_inventory` from
`production_rtl_inventory`, so every active production SystemVerilog module is
covered either by a C++-driven generated-top Verilator run or by the
C++-generated module-DPI launcher lane before accepted `imp1` mock RTL is
exempted. It also records `module_boundary_taxonomy`, which requires the active
runtime paths to be classified into CPU/control, digital ingress, latch-buffer,
SRAM shell, systolic-array, linear-path, and top-glue roles with passing
standalone runtime evidence and, for source-derived modules, module-only cycle
evidence. It also records `systemverilog_defined_module_runtime_audit`, which
is the module-name-level review table for the same inventory and fails if any
parsed production SystemVerilog module lacks an explicit runtime or accepted
mock scope. It also records `module_interface_signal_inventory`, requiring each
module-only boundary's documented input/output signal lists and descriptions to
match the parsed RTL ports. The full-command C++ Verilator harness also emits
`linear_trace_anchors` and `control_trace_anchors`; the certificate requires
the first and last observed RTL command/control payload anchors to match the
generated C++ schedule before the structural RTL proof can pass. It also emits
per-op `linear_op_trace_coverage` and per-slot `control_slot_trace_coverage`,
so every TinyLlama linear op index and every control slot kind has an observed
RTL payload count matching the generated C++ schedule. The
certificate also
records every StableHLO source operation instance by source line, source span,
result SSA name, and operation snippet, then binds that source instance to its
active `imp2` RTL files and module-DPI proof. It carries the C++ module-DPI
generator build/run records from both module-DPI reports, and those records
must contain sanitized commands plus normalized stdout with the expected module
counts. It also carries the generated C++ Verilator launchers for both
module-DPI suites and requires their dry-run module records to match the
generated execution recipes. The certificate also requires those launchers to
run every module-only Verilator test, report zero failures, and validate each
module run's stdout markers, ordered phase trace, and phase-signal trace
against the generated recipe. It also records a suite-level C++ launcher
runtime summary with module counts, trace record counts, and pass/fail booleans
for marker, ordered-prefix, repeated-template, and phase-signal checks. It also
requires the production RTL inventory's module-only rows to carry passing C++
launcher evidence, exact recipe matches, phase-key prefixes, and README-cycle
proofs for the same module, so source RTL coverage is tied to the generated C++
runner and not only to the Python report wrapper. It also requires the pass-27
source-derived and all-base boundary rows to carry exact launcher-recipe matches
for those generated C++ runner results, including the expected and observed
launcher phase-key prefixes for phase names and phase-signal values plus the
README cycle row they match. The `dpi_generation_provenance_audit` repeats that
generator-to-runtime chain in one place: each suite records its generator
source, sanitized build/run commands, expected stdout module count, generated
artifacts, generated C++ launcher source, suite-level launcher summary, and
per-module artifact, ledger, stdout-marker, and phase-trace result. It also
records the full command-stream payload
digest and checks that the RTL-accepted payload
digest matches the generated C++ schedule from the executed full-command
Verilator report. It also records the CPU/control slot payload digest from the
same full-command Verilator report. It carries the production RTL inventory and
hashes every inventory RTL path, including `imp1` mock RTL and generated
full-checkpoint RTL that are not part of the active target filelist. The
`imp1` mock RTL rows must also pass a one-file Verilator lint and a generated
C++ Verilator smoke run before the inventory can pass. It also carries a
generated SoC-top hierarchy proof that
checks every manifest IP instance appears once in `e1_h1_soc_top.sv` and that
the control CPU, ingress latch buffer, and systolic array are distinct
instances with active RTL defining their modules. It requires the structural
RTL execution proof and keeps TinyLlama numeric output equivalence as an
explicit non-claim.
The full-graph module-DPI binding records the selected DUT RTL for every parsed
generated and separated base module. Generated rows prove `selected DUT +
probe` flists; separated CPU, latch-buffer, and systolic-array rows prove
`imp1 reference + imp2 DUT + probe` flists.
The end-to-end smoke report also checks that the active implementation, FPGA,
and OpenROAD target filelists all use the same RTL files, that each
target-listed RTL file is covered by either the generated-top proof or a
passing module-DPI proof with exact C++ launcher recipe, phase-key, and
README-cycle evidence, and that the lowering construction certificate passes.
The same report carries
a production RTL inventory for the generated
SoC top, accepted `imp1` mock RTL in `rtl/ip`, active base `imp2` RTL in
`rtl/imp2`, and generated full-checkpoint RTL. Each inventory row parses the
RTL source, compares the defined module names against the expected proof
modules, and records the applicable proof kind: standalone top Verilator,
`imp1` mock C++/L1.5/VIP contract plus one-file Verilator lint and generated
C++ runtime smoke, base module-only DPI/Verilator, or generated full-checkpoint
module-only DPI/Verilator. The inventory also records `module_only_dpi_inventory`; active
source-derived RTL rows, namely base `imp2` and generated full-checkpoint RTL,
must appear in both its module-DPI covered-path list and its C++ launcher
covered-path list with exact recipe, phase-key, and README-cycle proof, while
generated SoC top glue and accepted `imp1` mock RTL are explicit
non-module-only proof families.
The generated `readme_cycle_coverage.json` artifacts for both base IP and
full-checkpoint modules prove that this README lists every generated cycle
template and phase name. They also require the review-visible diagram snippets
for the separated CPU, `ingress_sram` latch buffer, systolic array,
full-checkpoint tile scheduler, control scheduler, graph sequencer, and top
slot-dispatch boundary.
