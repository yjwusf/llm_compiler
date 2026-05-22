# Device Code

## Purpose

The code running on the E1-H1 CPU must be legible. It is not just a generated
blob. A human should be able to read it and understand how the CPU configures
the systolic array, points it at SRAM tiles, starts execution, and observes
completion.

## Location

Device-side code lives under `e1/code/program/`.

Initial files:

- `e1_device_mmio.hpp`: named MMIO addresses and small helpers.
- `e1_tinyllama_program.cpp`: first readable program skeleton.
- `e1_tinyllama_program_host_smoke.cpp`: host-side MMIO model used to execute
  and check the device program in tests.

## Style

- Prefer named constants over raw numeric writes.
- Keep accelerator command construction explicit.
- Keep tile dimensions visible at the call site.
- Avoid generated names that encode compiler internals.
- Do not hide required ordering behind opaque helper chains.
- Leave comments only where they explain hardware-visible intent.

## Boundary

The device code programs a systolic array through a stable accelerator command
interface. The array implementation may change, including replacement with a
Gemmini-inspired implementation, but the command interface must remain stable
or the E1-H1 docs and tests must be updated.

## Host Smoke

The host smoke builds the device program with `E1_DEVICE_HOST_MODEL`, replaces
raw MMIO loads/stores with a small C++ register model, runs `e1_main`, and
checks the exact write sequence for the first attention tile. The E1 pipeline
writes the result to `e1/generated/pipeline/07_device_program_run.json`.
