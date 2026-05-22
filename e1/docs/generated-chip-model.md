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

It is intentionally small. It records the first interface names and performance
counters before detailed timing is implemented.
