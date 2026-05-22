# Generated SoC Top

E1-H1 uses a generated SoC top instead of a hand-wired monolithic top. The
reference pattern is Wujian100's SoC organization, where IP blocks live under a
`soc/` tree and are assembled by a top-level module. E1-H1 keeps that top-level
assembly idea but makes the composition source the local IP manifests.

Reference: https://github.com/XUANTIE-RV/wujian100_open

The Wujian100 reference is used as a structural reference, not as copied RTL:
keep a visible top-level SoC boundary, keep named IP blocks readable at the top
level, and emit FPGA/simulation collateral from a single SoC source. E1-H1
replaces Wujian100's fixed MCU top with generated composition from
`e1/e1-h1/ip/*.json`.

## Generator

The generator is:

```sh
e1/e1-h1/tools/generate_soc_top.py \
  --architecture e1/e1-h1/config/architecture.json \
  --ip-dir e1/e1-h1/ip \
  --output e1/e1-h1/generated/e1_h1_soc_top.sv \
  --manifest-output e1/e1-h1/generated/e1_h1_soc_top_manifest.json \
  --interfaces-output e1/e1-h1/generated/e1_h1_interface_contracts.json
```

The generated files are checked in so reviews can inspect the current top-level
wiring, composition data, and interface locks. Tests regenerate them through
both the Python API and the CLI, then compare the result to the checked-in
files.

The generator also emits:

- `e1/e1-h1/generated/e1_h1_soc_top_manifest.json`
- `e1/e1-h1/generated/e1_h1_interface_contracts.json`

That JSON manifest records the same generated composition in reviewable data:
top ports, internal nets, net endpoints, top/internal driver and load roles,
RTL interface validation, subsystem grouping, and the Wujian100 style reference
that the top is following. The interface contracts file records the stable
replacement boundary for each IP and hashes the ports, parameters, connections,
and performance counters that must remain compatible.

## IP Manifests

Each file under `e1/e1-h1/ip/` describes one replaceable IP:

- Instance name.
- SystemVerilog module name.
- SystemVerilog RTL file.
- Subsystem membership.
- Composition order.
- Optional implementation reference.
- Parameters.
- Ports and their connections.

Connections use two namespaces:

- `top.<name>` creates or connects a top-level port.
- `net.<name>` creates or connects an internal SoC net.

The generator validates that every shared connection has a consistent width,
that top-level ports do not conflict on direction or width, that each top-level
output has exactly one output driver, and that each internal `net.*` connection
has exactly one output driver and at least one input load. Inout top ports and
internal nets are reserved for later bidirectional interfaces and must be
declared as inout-only when introduced.

The generator also validates each IP manifest against its `rtl` file. The
referenced SystemVerilog module must exist, every manifest parameter must be
declared in the module parameter list, and every manifest port must exist with
the same direction and bit width.

## Current Generated Top

The current generated top is:

- `e1/e1-h1/generated/e1_h1_soc_top.sv`

It combines:

- `cpu_subsystem`: `control_cpu`
- `io_subsystem`: `rgmii_ethernet_ingress`
- `memory_subsystem`: `ingress_sram`, `activation_sram`, `accumulator_sram`
- `accelerator_subsystem`: `systolic_array`

Subsystems are declared in `e1/e1-h1/config/architecture.json` and assigned per
IP manifest. The generated SystemVerilog comments and composition manifest must
stay in agreement with those declarations.

## Interface Contracts

The generated interface contract file is the review point for replacement
compatibility:

- `implementation_module` names the current RTL module used by the top.
- `rtl` names the current SystemVerilog source for that IP.
- `ports` and `parameters` define the stable generated wiring boundary.
- `perf_counters` define the required C++/L1.5 observation boundary.
- `signature_sha256` changes when the stable interface payload changes.

Changing a module implementation without changing `signature_sha256` is a
replacement-compatible edit. Changing the signature requires matching updates to
module docs, C++ model behavior, L1.5 harnesses, VIPs, generated artifacts, and
tests.

## Replacement Rule

Replacing an IP means changing the manifest's module implementation while
preserving the documented ports, parameters, C++ model behavior, L1.5 harness
behavior, VIP behavior, and performance counters. If the interface changes, the
manifest, generated top, docs, and tests must change together.
