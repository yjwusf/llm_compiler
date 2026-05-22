# Upstream Notes

## CORE-ET

Repository: https://github.com/openhwgroup/core-et

E1-H1 will evaluate CORE-ET as a CPU/IP reference for the first control core.
The E1-H1 target is a barebones, 3-wide CPU derived from or inspired by that
line of work, with nonessential Linux-boot and full SoC features removed.

If another CPU option is a better fit for a minimal accelerator controller, it
may replace this choice only if the E1-H1 CPU interface remains stable or is
updated through docs and tests.

## Gemmini

Repository: https://github.com/ucb-bar/gemmini

Gemmini is the first reference for the systolic-array side of E1-H1. The parts
of interest are the systolic array, scratchpad/accumulator organization,
tiling/programming model, and accelerator command structure.

E1-H1 does not inherit Gemmini's host-memory/DMA assumptions directly. The
project contract says external model data enters through Ethernet/RGMII and is
staged through configurable on-chip SRAM.

## Interface Rule

These upstreams are references for implementation ideas. The E1-H1 interface
docs are the compatibility contract. Any module, including the CPU or systolic
array, must be replaceable by another implementation when the same interface is
preserved.
