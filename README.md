# llm_compiler

## Docs

Start with [docs/README.md](docs/README.md). The docs define the project
contract, execution plan, coding conventions, and per-module documentation
requirements.

## Examples

- [E1](e1/README.md): first TinyLlama-to-chip example, including StableHLO
  capture planning, E1-H1 architecture notes, C++ chip model skeleton, and
  legible device code.

## Commit precheck

This checkout uses `.githooks/pre-commit` and `.githooks/prepare-commit-msg`
to allow commits only after 20:00 on weekdays in Singapore time, and anytime
on weekends.

Enable it in a clone with:

```sh
git config core.hooksPath .githooks
```
