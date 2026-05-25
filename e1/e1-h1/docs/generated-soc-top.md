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
RTL interface validation, architecture-to-IP parameter validation, subsystem
grouping, architecture-driven pipeline validation, and the Wujian100 style
reference that the top is following. The interface contracts file records the
stable replacement boundary for each IP and hashes the ports, parameters,
connections, and performance counters that must remain compatible.

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

For architecture-owned dimensions, `e1/e1-h1/config/architecture.json` is the
source of truth. The generator validates that the SRAM manifests mirror
`size_bytes`, `data_width`, and `banks` as `SIZE_BYTES`, `DATA_WIDTH`, and
`BANKS`, and that the `systolic_array` manifest mirrors the architecture's
`rows`, `cols`, `data_width`, and `accumulator_width` as `ROWS`, `COLS`,
`DATA_WIDTH`, and `ACCUMULATOR_WIDTH`. Any drift is a generator error rather
than a review-time convention.

The same architecture file owns generated pipeline depths. The top generator
keeps logical IP manifest connections stable, then rewrites the physical top
wiring to insert pipeline registers for:

- `cpu_to_accelerator_depth`: valid/ready command and payload signals from the
  control CPU to the systolic array.
- `array_input_depth`: valid/ready input stream and data from ingress SRAM to
  the systolic array.
- `array_output_depth`: completion and error signals from the systolic array
  back to the control CPU.

Depth `0` emits direct logical wiring. Positive depths emit concrete
SystemVerilog registers inside the generated top, so target packages and
Verilator lint see the configured pipeline.

## Current Generated Top

The current generated top is:

- `e1/e1-h1/generated/e1_h1_soc_top.sv`

The integrated top smoke test is:

- `e1/e1-h1/tests/e1_h1_soc_top_tb.cpp`

The E1 end-to-end smoke report runs this testbench as a standalone Verilator
proof for the generated top and attaches that proof to every target-filelist
row for `e1/e1-h1/generated/e1_h1_soc_top.sv`. Target packages must therefore
show either module-DPI evidence for an RTL file or this generated-top
standalone proof. The checked-in proof records `<soc_top_obj_dir>` and
`<soc_top_obj_dir>/Ve1_h1_soc_top` instead of a machine-local temporary build
path, so the evidence is reproducible across developers and CI machines.
The same end-to-end report includes `production_rtl_inventory`, where the
generated top is classified separately from `imp1` mock RTL, active base
`imp2` RTL, and generated full-checkpoint RTL. The generated-top row must parse
`e1_h1_soc_top` from this file and attach the standalone Verilator proof. The
inventory's `standalone_runtime_inventory` includes this generated-top proof
alongside the C++-generated module-DPI Verilator runs for active base `imp2`
RTL and generated full-checkpoint RTL; only accepted `imp1` mock RTL is exempt
from that active-runtime lane.
The construction certificate also carries `generated_soc_top_hierarchy`, which
parses this RTL against `e1_h1_soc_top_manifest.json`; every manifest IP must
appear exactly once as `u_<ip-name>`, its active RTL must define the module, and
the control CPU, ingress latch buffer, and systolic array must stay distinct
instance boundaries.

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
- `cpp_model` names the per-module C++ model manifest used by the chip model
  and L1.5 environment.
- `architecture_validation` records architecture-owned SRAM and accelerator
  parameters that the manifests are required to mirror.
- `pipeline_validation` records the architecture-owned pipeline depths and
  logical nets that the generated top uses.
- `module_vip` names the module-local VIP manifest that targets only that IP's
  SystemVerilog module.
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
