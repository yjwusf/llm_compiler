# Generated C++ Chip Model

## Purpose

The E1 generated chip model is the first executable system model. It represents
the E1-H1 chip in C++ while individual SystemVerilog modules are still mocked
or not yet implemented.

The chip model supports:

- Running the legible device program.
- Modeling the barebones CPU issuing accelerator commands.
- Modeling Ethernet/RGMII ingress as a digital source of data.
- Modeling on-chip SRAM staging.
- Modeling the systolic array through a replaceable interface.
- Collecting L1.5 C++ performance counters.
- Replacing one C++ module with one SystemVerilog module for hybrid execution.

## Replaceability

Every modeled block must sit behind a stable interface. The first C++ model may
be simple, but it must make replacement boundaries explicit:

- CPU model.
- Ethernet ingress model.
- SRAM model.
- Systolic-array model.
- Accelerator command interface.
- Target wrapper model.

SystemVerilog modules can replace C++ modules one at a time only when the same
documented interface is preserved.

## Current Skeleton

The initial skeleton lives in:

- `e1/code/chip_model/e1_chip_model.hpp`
- `e1/code/chip_model/e1_chip_model.cpp`
- `e1/code/chip_model/e1_chip_smoke.cpp`

It is intentionally small, but each current E1-H1 IP has a first-class C++
model contract under `e1/e1-h1/cmodels/`. Those JSON files bind the IP manifest
to the C++ class, source, header, inputs, outputs, and required performance
counters. The system-level `ChipModel` composes these module models for the
current smoke run.

Current C++ model classes:

- `e1::ControlCpuModel`
- `e1::RgmiiEthernetIngressModel`
- `e1::StreamSramModel`
- `e1::ConfigSramModel`
- `e1::SystolicArrayModel`

Run:

```sh
python3 e1/tools/run_e1_pipeline.py --clean
```

The pipeline compiles and runs `e1_chip_smoke.cpp`, then writes
`e1/generated/pipeline/08_chip_model_run.json` with C++ performance counters.
