# E1 Code

This directory contains the first C++ skeletons for E1.

## Sections

- `chip_model/`: generated or generator-shaped C++ chip model.
- `program/`: legible code intended to run on the E1-H1 bare-metal CPU.
  `e1_tinyllama_full_schedule.hpp` is a compact generated descriptor for the
  full-checkpoint systolic-array tile command stream, and
  `e1_tinyllama_full_rtl_cycle_smoke.cpp` checks the 8-cycle RTL lowering
  template for that stream.

## Rules

- Code under `program/` must be human-readable first.
- Generated names must not obscure hardware intent.
- The device program talks to stable interfaces, not implementation internals.
- The chip model must allow one C++ module to be replaced by one
  SystemVerilog module for L1.5 runs.
- Each E1-H1 SystemVerilog IP must point at an explicit C++ model manifest
  under `e1/e1-h1/cmodels/`.
