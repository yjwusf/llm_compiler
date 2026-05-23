# `e1_h1_control_cpu`

Status: mock

Layer: l1

SystemVerilog source: `e1/e1-h1/rtl/imp2/e1_h1_control_cpu.sv`

IP manifest: `e1/e1-h1/ip/control_cpu.json`

C++ model: `e1/e1-h1/cmodels/control_cpu.json`

L1.5 hybrid run: `e1_h1_control_cpu_hybrid`

L1.5 harness: `e1/e1-h1/l1_5/control_cpu.json`

Module VIP: `e1/e1-h1/vip/control_cpu.json`

## Purpose

Barebones 3-wide control CPU placeholder. It represents the E1-H1 control core
interface that will eventually be evaluated against CORE-ET or another
bare-metal CPU option. The CPU programs the systolic array through a stable
command interface and omits Linux-boot/full-SoC behavior.

## Parameters

No RTL parameters are defined yet.

## Input Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Primary core clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |
| `cmd_ready_i` | 1 | core clock | Systolic array can accept a command. |
| `array_done_i` | 1 | core clock | Systolic array completed the issued command. |
| `array_error_i` | 1 | core clock | Systolic array reported an error. |

## Output Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `cmd_valid_o` | 1 | core clock | CPU command is valid. |
| `cmd_input_addr_o` | 32 | core clock | Input tile base address. |
| `cmd_weight_addr_o` | 32 | core clock | Weight tile base address. |
| `cmd_output_addr_o` | 32 | core clock | Output tile base address. |
| `cmd_rows_o` | 16 | core clock | Tile row count. |
| `cmd_cols_o` | 16 | core clock | Tile column count. |
| `cmd_depth_o` | 16 | core clock | Tile reduction depth. |
| `debug_halted_o` | 1 | core clock | Mock CPU reached halted state. |

## Interface Protocol

The CPU emits one accelerator command. A command transfer occurs when
`cmd_valid_o` and `cmd_ready_i` are high on the same clock edge. The CPU then
waits for `array_done_i` or `array_error_i`.

## Replacement Compatibility

A replacement CPU must preserve reset behavior, command signal meanings, command
handshake, completion/error observation, and the device-visible MMIO command
contract documented in `e1/docs/device-code.md`.

## Mock Behavior

The mock emits one fixed 16x16x16 tile command and halts after completion or
error.

## C++ Model Contract

The C++ chip model represents this block by issuing `SystolicCommand` objects
and counting `instructions` and `array_commands`.

C++ implementation: `e1::ControlCpuModel` in
`e1/code/chip_model/e1_chip_model.*`.

## L1.5 Hybrid Execution

The L1.5 run instantiates only `e1_h1_control_cpu` in SystemVerilog through
`e1/e1-h1/l1_5/control_cpu.json`. C++ models the systolic array readiness,
completion, errors, reset sequencing, and performance counters.

## Module VIP

The module-local VIP is `e1/e1-h1/vip/control_cpu.json`. It allows only
`e1_h1_control_cpu` as the SystemVerilog DUT; systolic-array readiness,
completion, and error behavior are provided by C++ environment models.

## Implementation Versions

`imp1` is the current accepted mock RTL in
`e1/e1-h1/rtl/ip/e1_h1_control_cpu.sv`. `imp2` is the active candidate RTL in
`e1/e1-h1/rtl/imp2/e1_h1_control_cpu.sv`. It is accepted because
Verilator+DPI VIP equivalence proves it matches `imp1` on sensible control-CPU
streams and its flist is gathered.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core cycles observed. | yes |
| `instructions` | Device-side command writes or issued instructions. | yes |
| `array_commands` | Accepted accelerator commands. | yes |
| `stall_cycles` | Cycles with `cmd_valid_o` high and `cmd_ready_i` low. | yes |
| `error_events` | Observed `array_error_i` events. | yes |

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `test_ip_manifests_are_replaceable_and_connected` | E1-H1 unittest | Manifest points at this spec and stable ports. |
| `test_verilator_lints_generated_top_and_mock_ips` | Verilator | Mock RTL lints as part of generated SoC top. |
