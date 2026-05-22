# E1-H1 Architecture

E1-H1 is the first hardware architecture for E1. It is a concrete architecture
instance used to drive TinyLlama-derived workloads from StableHLO through the
compiler, C++ chip model, L1.5 hybrid runs, and generated SystemVerilog.

## Architecture Summary

- External data source: Ethernet over digital RGMII.
- Control core: barebones 3-wide CPU, evaluated against CORE-ET as a starting
  point and stripped of nonessential Linux-boot/full-SoC features.
- Accelerator: programmable systolic array, initially informed by Gemmini.
- Memory: configurable on-chip SRAM used for Ethernet-ingested staging,
  activations, weights, and accumulator traffic.
- Runtime: legible bare-metal device code under `e1/code/program/`.
- Model: generated C++ chip model under `e1/code/chip_model/`.

## CPU Direction

The first CPU concept is a 3-wide barebones control core. The intent is a
widened, CVA6-like control engine without the parts needed only for booting
Linux or hosting a general-purpose SoC.

CORE-ET is the first reference to evaluate for this direction. If it is not the
right fit, a better bare-metal RISC-V control core may replace it. The CPU's
accelerator programming interface must remain stable across replacement.

## Systolic Array Direction

Gemmini is the first systolic-array reference. E1-H1 should borrow ideas from
Gemmini's systolic array, private SRAM organization, and explicit accelerator
programming model, while adapting them to this repository's Ethernet/RGMII
ingress and replaceable-interface requirements.

## Module Replaceability

Every E1-H1 module must be replaceable behind a stable interface. Replacement
examples:

- A CORE-ET-derived CPU can be replaced by a different bare-metal CPU.
- A Gemmini-inspired systolic array can be replaced by another array.
- SRAM wrappers can be replaced by FPGA BRAMs, generic models, or ASIC SRAM
  macros.
- RGMII ingress can be replaced internally while preserving its documented
  digital pin and stream interface.

The interface, not the first implementation, is the contract.

## Initial Blocks

| Block | Initial source of ideas | Replaceable through |
| --- | --- | --- |
| Control CPU | CORE-ET / barebones 3-wide CPU concept | CPU command and MMIO interface |
| Systolic array | Gemmini | Accelerator command and tile stream interfaces |
| On-chip SRAM | Project JSON config | SRAM request/response interfaces |
| Ethernet ingress | Root peripheral contract | RGMII pins and internal stream interface |
| Chip model | E1 C++ generated model | L1.5 module interfaces |

## Generated SoC Top

E1-H1 uses a generated SoC top assembled from individual IP manifests under
`e1/e1-h1/ip/`. The generator emits
`e1/e1-h1/generated/e1_h1_soc_top.sv` and the tests check that the checked-in
top matches the manifest-driven output.

This follows the Wujian100-style idea of a visible SoC top that connects IP
blocks, while avoiding a hand-maintained monolithic top. See
[generated-soc-top.md](generated-soc-top.md).

## Target Packages

E1-H1 emits initial FPGA and ASIC/OpenROAD smoke packages from the same
manifest-driven SoC top and IP RTL list. See
[target-packaging.md](target-packaging.md).
