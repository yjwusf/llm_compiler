# `rgmii_ethernet_ingress`

Status: planned

Layer: l2

SystemVerilog source: `rtl/l2/rgmii_ethernet_ingress.sv`

C++ model: `models/cpp/rgmii_ethernet_ingress_model.*`

L1.5 hybrid run: `rgmii_ethernet_ingress_hybrid`

C++ performance counters: `models/cpp/rgmii_ethernet_ingress_counters.*`

VIP: `vip/rgmii_ethernet_ingress/`

Verilator target: `rgmii_ethernet_ingress`

## Purpose

`rgmii_ethernet_ingress` receives Ethernet data from a digital RGMII MAC-side
boundary, validates packet framing, and emits accelerator input data on an
internal valid/ready stream.

The module is the initial external data-source boundary for the accelerator.
It replaces any assumption that input model data comes from off-chip DRAM.

## Parameters

| Name | Type | Default | Description | JSON source |
| --- | --- | --- | --- | --- |
| `STREAM_DATA_WIDTH` | `int unsigned` | `64` | Width of the internal output data stream. | `io.external_data_source.stream_data_width` |
| `FIFO_DEPTH` | `int unsigned` | `1024` | Depth of the digital ingress buffering FIFO. | `io.external_data_source.fifo_depth` |
| `ENABLE_FRAME_CHECK` | `bit` | `1` | Enables Ethernet frame validation in RTL or mock behavior. | `io.external_data_source.enable_frame_check` |

## Input Signals

| Signal | Width | Clock/reset domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | core clock | Primary core clock for the internal stream side. |
| `rst_ni` | 1 | async reset | Active-low reset for core-clocked logic. |
| `rgmii_rx_clk_i` | 1 | RGMII RX clock | Receive clock from the external Ethernet PHY. |
| `rgmii_rxd_i` | 4 | RGMII RX clock | RGMII receive data nibble bus from the external PHY. |
| `rgmii_rx_ctl_i` | 1 | RGMII RX clock | RGMII receive control signal carrying data-valid and error encoding. |
| `stream_ready_i` | 1 | core clock | Downstream ready for the internal accelerator input stream. |

## Output Signals

| Signal | Width | Clock/reset domain | Description |
| --- | --- | --- | --- |
| `stream_valid_o` | 1 | core clock | Asserted when `stream_data_o` is valid. |
| `stream_data_o` | `STREAM_DATA_WIDTH` | core clock | Internal accelerator input data extracted from received Ethernet frames. |
| `stream_last_o` | 1 | core clock | Marks the final beat of a decoded packet or payload segment. |
| `stream_error_o` | 1 | core clock | Indicates frame, checksum, decode, overflow, or protocol error on the current stream item. |

## Interface Protocol

The RGMII side is a digital MAC-side receive boundary connected to an external
PHY. The module does not instantiate analog PHY logic or mixed-signal models.

The internal stream side uses valid/ready. A transfer occurs on a core clock
edge when `stream_valid_o` and `stream_ready_i` are both high.

## Latency And Pipeline Behavior

Latency is implementation-defined while the module is in planned or mock
status. Any real RTL implementation must document receive-clock crossing,
buffering latency, packet decode latency, and behavior under downstream
backpressure.

## Behavior

The module converts accepted Ethernet payload data into an internal accelerator
stream. Invalid frames must either be dropped or emitted with `stream_error_o`
according to a documented policy before RTL status.

## Replacement Compatibility

A replacement implementation must preserve the documented RGMII receive pins,
internal valid/ready stream, parameters, error behavior, C++ model contract,
L1.5 hybrid run behavior, performance counters, and digital-only VIP
requirements.

## Mock Behavior

The initial mock may translate a deterministic byte sequence supplied by the
VIP into stream beats and may skip full Ethernet CRC validation. Any skipped
frame checks must be listed here before the mock is implemented.

## C++ Model Contract

The C++ model is the behavioral reference for mapping RGMII receive events to
internal stream beats. It must model packet boundaries, output ordering,
backpressure, and all documented error cases.

## L1.5 Hybrid Execution

The L1.5 run instantiates `rgmii_ethernet_ingress` as the only SystemVerilog
module. C++ supplies the external Ethernet PHY behavior, RGMII receive stimulus,
downstream accelerator stream consumer, reset sequencing, scoreboarding, and
performance counters.

No other SystemVerilog module may be required for this run.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Core clock cycles observed during the run. | yes |
| `rgmii_rx_cycles` | RGMII receive clock cycles observed during the run. | yes |
| `input_transfers` | Accepted RGMII receive data events. | yes |
| `output_transfers` | Internal stream transfers accepted by downstream C++. | yes |
| `stall_cycles` | Cycles where `stream_valid_o` is high and `stream_ready_i` is low. | yes |
| `error_events` | Frames or stream beats reported with `stream_error_o`. | yes |
| `frames_seen` | Ethernet frames detected at the RGMII boundary. | yes |
| `frames_emitted` | Decoded payload segments emitted on the internal stream. | yes |

## VIP Requirements

The module-local VIP must drive `rgmii_rx_clk_i`, `rgmii_rxd_i`, and
`rgmii_rx_ctl_i`, monitor the valid/ready stream, and check payload ordering,
packet boundaries, reset behavior, and backpressure behavior. It must be
digital-only and must not require analog or mixed-signal simulation.

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `rgmii_ethernet_ingress_reset` | Verilator / VIP | Reset produces no valid output until a valid frame is received. |
| `rgmii_ethernet_ingress_single_frame` | C++ / Verilator / VIP | One valid Ethernet payload becomes one ordered stream segment. |
| `rgmii_ethernet_ingress_backpressure` | Verilator / VIP | Downstream stalls do not reorder or drop accepted payload data. |
| `rgmii_ethernet_ingress_bad_frame` | C++ / Verilator / VIP | Invalid frame behavior matches the documented error/drop policy. |
| `rgmii_ethernet_ingress_hybrid_single_frame` | L1.5 hybrid | The SystemVerilog ingress module runs alone with C++ PHY stimulus and C++ downstream consumer. |
| `rgmii_ethernet_ingress_hybrid_counters` | L1.5 hybrid / C++ | C++ counters report cycles, transfers, stalls, errors, and frame counts. |
