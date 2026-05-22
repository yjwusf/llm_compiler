# `<module_name>`

Status: planned | mock | rtl | formal-covered

Layer: l1 | l2 | l3

SystemVerilog source: `<path>`

C++ model: `<path>`

L1.5 hybrid run: `<path-or-command>`

C++ performance counters: `<path>`

VIP: `<path>`

Verilator target: `<path-or-command>`

## Purpose

Describe what this module does and where it sits in the hardware hierarchy.

## Parameters

| Name | Type | Default | Description | JSON source |
| --- | --- | --- | --- | --- |
| `<PARAM>` | `int unsigned` | `<value>` | `<description>` | `<json.path>` |

## Input Signals

| Signal | Width | Clock/reset domain | Description |
| --- | --- | --- | --- |
| `clk_i` | 1 | clock | Primary module clock. |
| `rst_ni` | 1 | async reset | Active-low reset. |

## Output Signals

| Signal | Width | Clock/reset domain | Description |
| --- | --- | --- | --- |
| `<signal_o>` | `<width>` | `<domain>` | `<description>` |

## Interface Protocol

Describe handshakes, ordering, backpressure, memory access rules, and any
target-specific behavior.

## Latency And Pipeline Behavior

Document fixed latency, configurable pipeline length, flush behavior, and how
valid/ready signals interact with pipeline stalls.

## Behavior

Describe expected functional behavior.

## Replacement Compatibility

Describe which parts of this module are stable compatibility boundaries. List
the ports, parameters, C++ model behavior, L1.5 harness behavior, performance
counters, and VIP expectations that a replacement implementation must preserve.

## Mock Behavior

If this is a mock, describe exactly what the mock implements and what final
behavior is intentionally missing.

## C++ Model Contract

Describe how the C++ model maps module inputs to outputs and what tolerances,
rounding rules, or ordering constraints apply.

## L1.5 Hybrid Execution

Describe how to run this module as the only SystemVerilog module while all
other system behavior is provided by C++ models, mocks, or adapters. List the
C++ components that replace upstream, downstream, memory, control, peripheral,
and environment behavior.

## C++ Performance Counters

| Counter | Description | Required |
| --- | --- | --- |
| `cycles` | Module clock cycles observed during the run. | yes |
| `input_transfers` | Input transfers accepted by the module. | yes |
| `output_transfers` | Output transfers produced by the module. | yes |
| `stall_cycles` | Backpressure or unavailable-resource cycles. | yes |
| `error_events` | Documented error events observed at the module boundary. | yes |
| `<counter>` | `<module-specific event>` | no |

## VIP Requirements

List the module-local VIP drivers, monitors, scoreboards, and required
scenarios.

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `<test_name>` | C++ / L1.5 hybrid / Verilator / VIP / formal | `<doc requirement>` |
