#!/usr/bin/env python3
"""Fetch or preflight the pinned TinyLlama checkpoint for E1."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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


def hf_command(manifest: dict[str, Any], cache_dir: Path) -> list[str]:
    source = manifest["source"]
    return [
        "hf",
        "download",
        source["repo"],
        "--revision",
        source["revision"],
        "--local-dir",
        repo_rel(cache_dir),
    ]


def build_report(manifest: dict[str, Any], cache_dir: Path, mode: str) -> dict[str, Any]:
    hf_path = shutil.which("hf")
    command = hf_command(manifest, cache_dir)
    cache_exists = cache_dir.exists()
    return {
        "schema": "e1-fetch-model-report-v0",
        "model_id": manifest["model_id"],
        "source": manifest["source"],
        "mode": mode,
        "hf_available": hf_path is not None,
        "hf_path": hf_path,
        "command": command,
        "cache_dir": repo_rel(cache_dir),
        "cache_exists": cache_exists,
        "large_artifacts_committed": False,
        "ready_for_live_fetch": hf_path is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "e1/model/tinyllama_manifest.json")
    parser.add_argument("--cache-dir", type=Path, default=REPO_ROOT / ".cache/e1/tinyllama-1.1b-chat-v1.0")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=["offline", "preflight", "live"], default="offline")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    cache_dir = args.cache_dir if args.cache_dir.is_absolute() else REPO_ROOT / args.cache_dir
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    manifest = load_json(manifest_path)
    report = build_report(manifest, cache_dir, args.mode)

    if args.mode == "live":
        if not report["hf_available"]:
            write_json(report_path, {**report, "status": "missing_hf_cli"})
            raise SystemExit("hf CLI is required for live TinyLlama fetch")
        subprocess.run(report["command"], cwd=REPO_ROOT, check=True)
        report["cache_exists"] = cache_dir.exists()
        report["status"] = "fetched"
    elif args.mode == "preflight":
        report["status"] = "ready" if report["ready_for_live_fetch"] else "missing_hf_cli"
    else:
        report["status"] = "offline_fixture"

    write_json(report_path, report)
    print(f"PASS e1_fetch_model {report['status']} -> {repo_rel(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
