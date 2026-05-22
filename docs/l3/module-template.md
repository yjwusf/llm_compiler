# `<module_name>`

Status: planned | mock | rtl | formal-covered

Layer: l1 | l2 | l3

SystemVerilog source: `<path>`

C++ model: `<path>`

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

## Mock Behavior

If this is a mock, describe exactly what the mock implements and what final
behavior is intentionally missing.

## C++ Model Contract

Describe how the C++ model maps module inputs to outputs and what tolerances,
rounding rules, or ordering constraints apply.

## VIP Requirements

List the module-local VIP drivers, monitors, scoreboards, and required
scenarios.

## Tests

| Test | Type | Requirement |
| --- | --- | --- |
| `<test_name>` | C++ / Verilator / VIP / formal | `<doc requirement>` |
