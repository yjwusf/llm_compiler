#!/usr/bin/env python3
"""Run one E1-H1 L1.5 hybrid harness.

The current L1.5 harnesses are boundary smokes: each run Verilates exactly one
SystemVerilog DUT and links it with a C++ environment that drives and checks the
module boundary. Other system behavior is represented by that C++ harness.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def repo_path(path: str) -> Path:
    return REPO_ROOT / path


def run(cmd: list[str]) -> None:
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout)


def build_and_run(harness: dict[str, Any]) -> None:
    verilator = shutil.which("verilator")
    if verilator is None:
        raise RuntimeError("verilator is required for L1.5 hybrid runs")

    rtl = repo_path(harness["rtl"])
    tb = repo_path(harness["cpp_testbench"])
    top_module = harness["top_module"]

    if not rtl.exists():
        raise FileNotFoundError(rtl)
    if not tb.exists():
        raise FileNotFoundError(tb)

    with tempfile.TemporaryDirectory(prefix=f"{harness['name']}_") as tmp:
        obj_dir = Path(tmp) / "obj_dir"
        cmd = [
            verilator,
            "--cc",
            "--exe",
            "--build",
            "--sv",
            "-Wall",
            "-Wno-DECLFILENAME",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "--top-module",
            top_module,
            "-Mdir",
            str(obj_dir),
        ]
        for key, value in harness.get("parameters", {}).items():
            cmd.append(f"-G{key}={value}")
        cmd.extend([str(rtl.relative_to(REPO_ROOT)), str(tb.relative_to(REPO_ROOT))])
        run(cmd)
        run([str(obj_dir / f"V{top_module}")])


def validate_vip(harness: dict[str, Any]) -> None:
    vip_path = repo_path(harness["module_vip"])
    if not vip_path.exists():
        raise FileNotFoundError(vip_path)
    vip = load_json(vip_path)
    if vip.get("schema") != "e1-h1-module-vip-v0":
        raise ValueError(f"{vip_path}: unsupported VIP schema {vip.get('schema')!r}")

    for key in ["name", "ip_manifest", "top_module", "rtl", "cpp_testbench"]:
        if vip.get(key) != harness.get(key):
            raise ValueError(
                f"{vip_path}: VIP {key} {vip.get(key)!r} does not match harness {harness.get(key)!r}"
            )
    if vip.get("cpp_environment") != harness.get("cpp_environment"):
        raise ValueError(f"{vip_path}: VIP C++ environment does not match harness")
    if vip.get("perf_counters") != harness.get("perf_counters"):
        raise ValueError(f"{vip_path}: VIP performance counters do not match harness")

    scope = vip.get("scope", {})
    if scope.get("kind") != "module_only":
        raise ValueError(f"{vip_path}: VIP scope must be module_only")
    allowed = scope.get("allowed_systemverilog_modules")
    if allowed != [harness["top_module"]]:
        raise ValueError(f"{vip_path}: VIP must allow exactly the harness top module")
    if scope.get("neighbors") != "cpp_environment":
        raise ValueError(f"{vip_path}: VIP neighbors must be supplied by cpp_environment")


def validate_harness(harness: dict[str, Any]) -> None:
    required = [
        "schema",
        "name",
        "ip_manifest",
        "top_module",
        "rtl",
        "cpp_testbench",
        "module_vip",
        "cpp_environment",
        "perf_counters",
    ]
    for key in required:
        if key not in harness:
            raise ValueError(f"missing required harness key: {key}")
    if harness["schema"] != "e1-h1-l1_5-harness-v0":
        raise ValueError(f"unsupported harness schema: {harness['schema']!r}")
    if len(harness["perf_counters"]) == 0:
        raise ValueError("perf_counters must not be empty")
    validate_vip(harness)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", type=Path, required=True)
    args = parser.parse_args()

    harness_path = args.harness if args.harness.is_absolute() else REPO_ROOT / args.harness
    harness = load_json(harness_path)
    validate_harness(harness)
    build_and_run(harness)
    print(f"PASS {harness['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
