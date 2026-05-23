# `e1_h1_rgmii_ethernet_ingress`

Status: mock

Layer: l2

SystemVerilog source: `e1/e1-h1/rtl/ip/e1_h1_rgmii_ethernet_ingress.sv`

IP manifest: `e1/e1-h1/ip/rgmii_ethernet_ingress.json`

C++ model: `e1/e1-h1/cmodels/rgmii_ethernet_ingress.json`

L1.5 hybrid run: `e1_h1_rgmii_ethernet_ingress_hybrid`

L1.5 harness: `e1/e1-h1/l1_5/rgmii_ethernet_ingress.json`

Module VIP: `e1/e1-h1/vip/rgmii_ethernet_ingress.json`

## Purpose

Digital-only Ethernet ingress boundary. It receives RGMII-side nibbles from an
external PHY boundary and produces internal stream data for SRAM staging.

## Parameters

No RTL parameters are defined yet.

## Input Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Internal stream-side clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |
| `rgmii_rx_clk_i` | 1 | RGMII RX clock | Receive clock from external PHY. |
| `rgmii_rxd_i` | 4 | RGMII RX clock | Receive data nibble. |
| `rgmii_rx_ctl_i` | 1 | RGMII RX clock | Receive control/data-valid indicator. |
| `stream_ready_i` | 1 | core clock | Downstream stream readiness. |

## Output Signals

| Signal | Width | Domain | Description |
| --- | --- | --- | --- |
| `stream_valid_o` | 1 | core clock | Stream output is valid. |
| `stream_data_o` | 64 | core clock | Packed received data. |
| `stream_last_o` | 1 | core clock | End of mock frame segment. |
| `stream_error_o` | 1 | core clock | Frame/decode error indicator. |

## Interface Protocol

The RGMII side is digital-only. The stream side uses valid/ready.

## Replacement Compatibility

A replacement must preserve RGMII pin names, stream signal meanings, digital-only
behavior, and no off-chip DRAM dependency.

## Mock Behavior

The mock shifts received nibbles into a 64-bit stream word while
`rgmii_rx_ctl_i` is high and never reports an error.

## C++ Model Contract

The C++ model supplies external PHY stimulus and checks stream beat ordering,
backpressure, and error reporting.

C++ implementation: `e1::RgmiiEthernetIngressModel` in
`e1/code/chip_model/e1_chip_model.*`.

## L1.5 Hybrid Execution

Only this RTL module is instantiated through
`e1/e1-h1/l1_5/rgmii_ethernet_ingress.json`. C++ supplies RGMII stimulus,
downstream consumer behavior, scoreboarding, and counters.

## Module VIP

The module-local VIP is `e1/e1-h1/vip/rgmii_ethernet_ingress.json`. It allows
only `e1_h1_rgmii_ethernet_ingress` as the SystemVerilog DUT; external PHY
stimulus and downstream stream consumption are C++ environment roles.

## Implementation Versions

`imp1` is the current accepted mock RTL in
`e1/e1-h1/rtl/ip/e1_h1_rgmii_ethernet_ingress.sv`. `imp2` is the reserved real
digital RGMII ingress implementation slot. `imp2` is not selectable until
Verilator+DPI VIP equivalence proves it matches `imp1` on all sensible RGMII
and internal-stream cases and its flist is gathered.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core cycles observed. | yes |
| `rgmii_rx_cycles` | RGMII receive cycles observed. | yes |
| `input_transfers` | RGMII receive events. | yes |
| `output_transfers` | Stream transfers. | yes |
| `stall_cycles` | Stream backpressure cycles. | yes |
| `error_events` | Stream error events. | yes |
| `frames_seen` | Frames detected at RGMII boundary. | yes |

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `test_ip_manifests_are_replaceable_and_connected` | E1-H1 unittest | Manifest points at this spec and stable ports. |
| `test_verilator_lints_generated_top_and_mock_ips` | Verilator | Mock RTL lints as part of generated SoC top. |
