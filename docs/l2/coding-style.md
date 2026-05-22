# Coding Style

## SystemVerilog

- Use one module per file.
- Match file names to module names.
- Prefer `logic`, `always_ff`, and `always_comb`.
- Use `default_nettype none` in handwritten RTL unless a target tool prevents
  it.
- Use `clk_i` for the primary clock.
- Use active-low reset named `rst_ni`.
- Use `_i`, `_o`, and `_io` suffixes for module ports.
- Use `_q` and `_d` suffixes for registered state and next-state values.
- Use `parameter int unsigned` for tunable widths, depths, and pipeline
  lengths.
- Keep pipeline depths parameterized when they come from architecture JSON.
- Keep L1 wiring explicit and readable.
- Do not silently change a port name, width, or protocol without updating the
  L3 module document and tests.

## Interfaces

- Prefer valid/ready handshakes for streaming data:
  - Producer outputs `valid_o` and `data_o`.
  - Producer inputs `ready_i`.
  - Consumer inputs `valid_i` and `data_i`.
  - Consumer outputs `ready_o`.
- Document any non-streaming, memory-mapped, or target-specific interface in
  the module's L3 document.
- Document latency for every pipelined module.

## C++

- C++ models define the behavioral reference for mocks and module tests.
- Keep C++ model inputs and outputs aligned with the SystemVerilog module
  ports.
- Prefer deterministic tests and explicit fixtures over randomized-only tests.
- If randomized tests are added, record the seed in failures.

## JSON

- Use snake_case keys.
- Keep units explicit in key names when useful, for example `size_bytes`.
- Do not add implicit defaults in compiler code without documenting them in
  `docs/l2/assumptions.md` or L1.
- Add schema tests when new JSON fields affect generated hardware.
