# llm_compiler

## Docs

Start with [docs/README.md](docs/README.md). The docs define the project
contract, execution plan, coding conventions, and per-module documentation
requirements.

## Commit precheck

This checkout uses `.githooks/pre-commit` and `.githooks/prepare-commit-msg`
to allow commits only after 20:00 on weekdays in Singapore time, and anytime
on weekends.

Enable it in a clone with:

```sh
git config core.hooksPath .githooks
```
