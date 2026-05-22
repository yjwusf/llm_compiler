#!/usr/bin/env python3
"""Export or stage StableHLO for E1.

The live full-model export is intentionally dependency-gated. Offline mode
copies the checked-in reduced StableHLO fixture and writes the same report shape
used by the pipeline.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def dependency_report() -> dict[str, bool]:
    return {
        "torch": has_module("torch"),
        "transformers": has_module("transformers"),
        "jax": has_module("jax"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "e1/model/tinyllama_manifest.json")
    parser.add_argument("--fetch-report", type=Path, required=True)
    parser.add_argument("--stablehlo-out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=["offline", "preflight", "live"], default="offline")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    stablehlo_out = args.stablehlo_out if args.stablehlo_out.is_absolute() else REPO_ROOT / args.stablehlo_out
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    fetch_report_path = args.fetch_report if args.fetch_report.is_absolute() else REPO_ROOT / args.fetch_report
    manifest = load_json(manifest_path)
    fetch_report = load_json(fetch_report_path)
    fixture = REPO_ROOT / manifest["frontend"]["fixture"]
    deps = dependency_report()
    live_ready = all(deps.values()) and fetch_report.get("cache_exists", False)

    report: dict[str, Any] = {
        "schema": "e1-stablehlo-export-report-v0",
        "model_id": manifest["model_id"],
        "mode": args.mode,
        "dependencies": deps,
        "fetch_report": repo_rel(fetch_report_path),
        "stablehlo_out": repo_rel(stablehlo_out),
        "fixture": repo_rel(fixture),
        "live_ready": live_ready,
    }

    if args.mode == "live":
        if not live_ready:
            report["status"] = "missing_live_export_dependencies"
            write_json(report_path, report)
            raise SystemExit("torch, transformers, jax, and cached model files are required for live export")
        raise SystemExit("live StableHLO export path is dependency-ready but not implemented")
    if args.mode == "preflight":
        report["status"] = "ready" if live_ready else "missing_live_export_dependencies"
        write_json(report_path, report)
        print(f"PASS e1_export_stablehlo_preflight {report['status']} -> {repo_rel(report_path)}")
        return 0

    stablehlo_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, stablehlo_out)
    report["status"] = "offline_fixture"
    write_json(report_path, report)
    print(f"PASS e1_export_stablehlo offline_fixture -> {repo_rel(stablehlo_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
