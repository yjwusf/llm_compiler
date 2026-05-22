# `e1_h1_config_sram` Accumulator Instance

Status: mock

Layer: l2

SystemVerilog source: `e1/e1-h1/rtl/ip/e1_h1_config_sram.sv`

IP manifest: `e1/e1-h1/ip/accumulator_sram.json`

C++ model: `e1/e1-h1/cmodels/accumulator_sram.json`

L1.5 hybrid run: `e1_h1_accumulator_sram_hybrid`

L1.5 harness: `e1/e1-h1/l1_5/accumulator_sram.json`

Module VIP: `e1/e1-h1/vip/accumulator_sram.json`

## Purpose

Accumulator SRAM configuration placeholder. It preserves the replaceable SRAM
instance boundary for accumulator and output tiles.

## Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `SIZE_BYTES` | `int unsigned` | 524288 | Logical SRAM capacity. |
| `DATA_WIDTH` | `int unsigned` | 256 | Configured SRAM data width. |
| `BANKS` | `int unsigned` | 8 | Configured bank count. |

## Input Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Primary clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |

## Output Signals

No output signals are exposed in the current mock.

## Interface Protocol

The current mock only captures reset/clock and parameter wiring.

## Replacement Compatibility

A replacement must preserve configured capacity, width, bank count, and the
future accumulator SRAM request/response contract once documented.

## Mock Behavior

The mock records an initialized state after reset deassertion and exposes no
functional memory ports yet.

## C++ Model Contract

The C++ chip model owns accumulator and output storage behavior until RTL ports
are introduced.

C++ implementation: `e1::ConfigSramModel` in
`e1/code/chip_model/e1_chip_model.*`.

## L1.5 Hybrid Execution

Only this RTL instance is instantiated through
`e1/e1-h1/l1_5/accumulator_sram.json`. C++ provides reset, clocking, memory
stimulus expectations, and counters.

## Module VIP

The module-local VIP is `e1/e1-h1/vip/accumulator_sram.json`. It allows only
`e1_h1_config_sram` as the SystemVerilog DUT for this accumulator instance;
reset and future accumulator memory behavior are C++ environment roles.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core cycles observed. | yes |
| `input_transfers` | Future writes or fills. | yes |
| `output_transfers` | Future reads or drains. | yes |
| `stall_cycles` | Future unavailable-resource cycles. | yes |
| `error_events` | Future memory errors. | yes |

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `test_ip_manifests_are_replaceable_and_connected` | E1-H1 unittest | Manifest points at this spec and stable ports. |
| `test_verilator_lints_generated_top_and_mock_ips` | Verilator | Mock RTL lints as part of generated SoC top. |
