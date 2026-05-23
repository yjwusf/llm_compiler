# `e1_h1_stream_sram`

Status: mock

Layer: l2

SystemVerilog source: `e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv`

IP manifest: `e1/e1-h1/ip/ingress_sram.json`

C++ model: `e1/e1-h1/cmodels/ingress_sram.json`

L1.5 hybrid run: `e1_h1_ingress_sram_hybrid`

L1.5 harness: `e1/e1-h1/l1_5/ingress_sram.json`

Module VIP: `e1/e1-h1/vip/ingress_sram.json`

## Purpose

Ingress SRAM staging mock. It accepts Ethernet-ingested stream data and presents
staged data to the systolic array input side.

## Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `SIZE_BYTES` | `int unsigned` | 262144 | Logical SRAM capacity. |
| `DATA_WIDTH` | `int unsigned` | 128 | Configured SRAM data width. |
| `BANKS` | `int unsigned` | 4 | Configured bank count. |

## Input Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Primary clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |
| `stream_valid_i` | 1 | core clock | Ingress stream beat is valid. |
| `stream_data_i` | 64 | core clock | Ingress stream payload. |
| `stream_last_i` | 1 | core clock | Final beat marker. |
| `stream_error_i` | 1 | core clock | Ingress error marker. |
| `array_ready_i` | 1 | core clock | Systolic array can accept staged data. |

## Output Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `stream_ready_o` | 1 | core clock | SRAM can accept ingress data. |
| `array_valid_o` | 1 | core clock | Staged array data is valid. |
| `array_data_o` | 64 | core clock | Staged array data. |

## Interface Protocol

Both sides use valid/ready. The mock is a one-entry staging buffer.

## Replacement Compatibility

A replacement may use real SRAM, FPGA BRAM, or an ASIC SRAM macro wrapper, but
must preserve stream semantics, JSON-configured capacity fields, and
backpressure behavior.

## Mock Behavior

The mock forwards one non-error beat at a time and drops error-marked beats.

## C++ Model Contract

The C++ model represents this block as on-chip staging storage between Ethernet
ingress and array consumption.

C++ implementation: `e1::StreamSramModel` in
`e1/code/chip_model/e1_chip_model.*`.

## L1.5 Hybrid Execution

Only this RTL module is instantiated through
`e1/e1-h1/l1_5/ingress_sram.json`. C++ supplies ingress stream producer, array
stream consumer, memory expectations, and counters.

## Module VIP

The module-local VIP is `e1/e1-h1/vip/ingress_sram.json`. It allows only
`e1_h1_stream_sram` as the SystemVerilog DUT; Ethernet producer and array
consumer behavior are supplied by C++ environment models.

## Implementation Versions

`imp1` is the current accepted mock RTL in
`e1/e1-h1/rtl/ip/e1_h1_stream_sram.sv`. `imp2` is the active candidate RTL in
`e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv`. It is accepted because
Verilator+DPI VIP equivalence proves it matches `imp1` on sensible
ingress-stream cases and its flist is gathered.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core cycles observed. | yes |
| `input_transfers` | Ingress beats accepted. | yes |
| `output_transfers` | Array beats emitted. | yes |
| `stall_cycles` | Backpressure cycles. | yes |
| `error_events` | Error-marked beats. | yes |

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `test_ip_manifests_are_replaceable_and_connected` | E1-H1 unittest | Manifest points at this spec and stable ports. |
| `test_verilator_lints_generated_top_and_mock_ips` | Verilator | Mock RTL lints as part of generated SoC top. |
