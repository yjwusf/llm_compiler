# E1 Code

This directory contains the first C++ skeletons for E1.

## Sections

- `chip_model/`: generated or generator-shaped C++ chip model.
- `program/`: legible code intended to run on the E1-H1 bare-metal CPU.

## Rules

- Code under `program/` must be human-readable first.
- Generated names must not obscure hardware intent.
- The device program talks to stable interfaces, not implementation internals.
- The chip model must allow one C++ module to be replaced by one
  SystemVerilog module for L1.5 runs.
- Each E1-H1 SystemVerilog IP must point at an explicit C++ model manifest
  under `e1/e1-h1/cmodels/`.
