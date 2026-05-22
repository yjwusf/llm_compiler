# Generated SoC Top

E1-H1 uses a generated SoC top instead of a hand-wired monolithic top. The
reference pattern is Wujian100's SoC organization, where IP blocks live under a
`soc/` tree and are assembled by a top-level module. E1-H1 keeps that top-level
assembly idea but makes the composition source the local IP manifests.

Reference: https://github.com/XUANTIE-RV/wujian100_open

## Generator

The generator is:

```sh
e1/e1-h1/tools/generate_soc_top.py \
  --architecture e1/e1-h1/config/architecture.json \
  --ip-dir e1/e1-h1/ip \
  --output e1/e1-h1/generated/e1_h1_soc_top.sv
```

The generated file is checked in so reviews can inspect the current top-level
wiring. Tests regenerate it and compare the result to the checked-in file.

## IP Manifests

Each file under `e1/e1-h1/ip/` describes one replaceable IP:

- Instance name.
- SystemVerilog module name.
- Composition order.
- Optional implementation reference.
- Parameters.
- Ports and their connections.

Connections use two namespaces:

- `top.<name>` creates or connects a top-level port.
- `net.<name>` creates or connects an internal SoC net.

The generator validates that every shared connection has a consistent width and
that top-level ports do not conflict on direction or width.

## Current Generated Top

The current generated top is:

- `e1/e1-h1/generated/e1_h1_soc_top.sv`

It combines:

- `control_cpu`
- `rgmii_ethernet_ingress`
- `ingress_sram`
- `activation_sram`
- `accumulator_sram`
- `systolic_array`

## Replacement Rule

Replacing an IP means changing the manifest's module implementation while
preserving the documented ports, parameters, C++ model behavior, L1.5 harness
behavior, VIP behavior, and performance counters. If the interface changes, the
manifest, generated top, docs, and tests must change together.
