# `e1_h1_systolic_array`

Status: mock

Layer: l2

SystemVerilog source: `e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv`

IP manifest: `e1/e1-h1/ip/systolic_array.json`

C++ model: `e1/e1-h1/cmodels/systolic_array.json`

L1.5 hybrid run: `e1_h1_systolic_array_hybrid`

L1.5 harness: `e1/e1-h1/l1_5/systolic_array.json`

Module VIP: `e1/e1-h1/vip/systolic_array.json`

## Purpose

Gemmini-inspired systolic-array placeholder behind the E1-H1 stable command and
tile-stream interfaces.

## Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `ROWS` | `int unsigned` | 16 | Array row count. |
| `COLS` | `int unsigned` | 16 | Array column count. |
| `DATA_WIDTH` | `int unsigned` | 16 | Input/weight data width. |
| `ACCUMULATOR_WIDTH` | `int unsigned` | 32 | Accumulator width. |

## Input Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Primary clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |
| `cmd_valid_i` | 1 | core clock | Command is valid. |
| `cmd_input_addr_i` | 32 | core clock | Input tile address. |
| `cmd_weight_addr_i` | 32 | core clock | Weight tile address. |
| `cmd_output_addr_i` | 32 | core clock | Output tile address. |
| `cmd_rows_i` | 16 | core clock | Tile rows. |
| `cmd_cols_i` | 16 | core clock | Tile columns. |
| `cmd_depth_i` | 16 | core clock | Tile reduction depth. |
| `input_valid_i` | 1 | core clock | Input stream beat valid. |
| `input_data_i` | 64 | core clock | Input stream payload. |

## Output Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `cmd_ready_o` | 1 | core clock | Array can accept a command. |
| `input_ready_o` | 1 | core clock | Array can accept input data. |
| `done_o` | 1 | core clock | Mock command completed. |
| `error_o` | 1 | core clock | Mock command failed. |
| `debug_busy_o` | 1 | core clock | Array is busy. |

## Interface Protocol

Commands use valid/ready. Input tile data uses valid/ready once a command is
accepted. Completion is signaled with `done_o`.

## Replacement Compatibility

A replacement may use a Gemmini-derived or unrelated systolic implementation,
but it must preserve command fields, stream meanings, completion/error signals,
JSON-configured dimensions, C++ model behavior, and L1.5 counters.

## Mock Behavior

The mock accepts one command when idle, consumes four valid input beats, then
pulses done.

## C++ Model Contract

The C++ model implements `SystolicArrayModel::submit` and `tick` as the
behavioral reference for command acceptance and completion counters.

C++ implementation: `e1::SystolicArrayModel` in
`e1/code/chip_model/e1_chip_model.*`.

## L1.5 Hybrid Execution

Only this RTL module is instantiated through
`e1/e1-h1/l1_5/systolic_array.json`. C++ supplies command producer, input
stream producer, completion checker, memory context, and counters.

## Module VIP

The module-local VIP is `e1/e1-h1/vip/systolic_array.json`. It allows only
`e1_h1_systolic_array` as the SystemVerilog DUT; CPU command, input stream, and
completion scoreboarding behavior are supplied by C++ environment models.

## Implementation Versions

`imp1` is the current accepted mock RTL in
`e1/e1-h1/rtl/ip/e1_h1_systolic_array.sv`. `imp2` is the active candidate RTL
in `e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv`. It is accepted because
Verilator+DPI VIP equivalence proves it matches `imp1` on sensible
command/input/completion streams and its flist is gathered.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core cycles observed. | yes |
| `array_commands` | Accepted array commands. | yes |
| `input_transfers` | Input beats consumed. | yes |
| `output_transfers` | Completed output events. | yes |
| `stall_cycles` | Command or input backpressure cycles. | yes |
| `error_events` | Array error events. | yes |

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `test_ip_manifests_are_replaceable_and_connected` | E1-H1 unittest | Manifest points at this spec and stable ports. |
| `test_verilator_lints_generated_top_and_mock_ips` | Verilator | Mock RTL lints as part of generated SoC top. |
