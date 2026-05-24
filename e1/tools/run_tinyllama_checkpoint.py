#!/usr/bin/env python3
"""Run the pinned TinyLlama checkpoint locally when dependencies are present."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
        "safetensors": has_module("safetensors"),
    }


def checkpoint_files(cache_dir: Path) -> dict[str, list[str]]:
    if not cache_dir.exists():
        return {"model": [], "tokenizer": [], "config": []}
    model_files = sorted(
        [
            *cache_dir.glob("*.safetensors"),
            *cache_dir.glob("pytorch_model*.bin"),
            *cache_dir.glob("model*.bin"),
        ]
    )
    tokenizer_files = sorted(
        [
            *cache_dir.glob("tokenizer.json"),
            *cache_dir.glob("tokenizer.model"),
            *cache_dir.glob("tokenizer_config.json"),
            *cache_dir.glob("special_tokens_map.json"),
        ]
    )
    config_files = sorted(cache_dir.glob("config.json"))
    return {
        "model": [repo_rel(path) for path in model_files],
        "tokenizer": [repo_rel(path) for path in tokenizer_files],
        "config": [repo_rel(path) for path in config_files],
    }


def preflight_report(manifest: dict[str, Any], cache_dir: Path, prompt: str, max_new_tokens: int) -> dict[str, Any]:
    deps = dependency_report()
    files = checkpoint_files(cache_dir)
    missing_dependencies = [name for name, present in deps.items() if not present]
    missing_files: list[str] = []
    if not files["config"]:
        missing_files.append("config.json")
    if not files["tokenizer"]:
        missing_files.append("tokenizer")
    if not files["model"]:
        missing_files.append("model_weights")

    if missing_dependencies:
        status = "missing_python_dependencies"
    elif not cache_dir.exists():
        status = "missing_checkpoint_cache"
    elif missing_files:
        status = "missing_checkpoint_files"
    else:
        status = "ready"

    return {
        "schema": "e1-full-tinyllama-checkpoint-execution-v0",
        "model_id": manifest["model_id"],
        "source": manifest["source"],
        "mode": "preflight",
        "status": status,
        "cache_dir": repo_rel(cache_dir),
        "cache_exists": cache_dir.exists(),
        "dependencies": deps,
        "missing_dependencies": missing_dependencies,
        "checkpoint_files": files,
        "missing_checkpoint_files": missing_files,
        "prompt": prompt,
        "max_new_tokens": max_new_tokens,
        "full_checkpoint_execution": False,
    }


def run_live(
    manifest: dict[str, Any],
    cache_dir: Path,
    prompt: str,
    max_new_tokens: int,
    top_k: int,
    allow_download: bool,
) -> dict[str, Any]:
    report = preflight_report(manifest, cache_dir, prompt, max_new_tokens)
    if report["status"] != "ready":
        return {**report, "mode": "live", "full_checkpoint_execution": False}

    import torch  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]

    local_files_only = not allow_download
    tokenizer = AutoTokenizer.from_pretrained(cache_dir, local_files_only=local_files_only)
    model = AutoModelForCausalLM.from_pretrained(
        cache_dir,
        torch_dtype="auto",
        local_files_only=local_files_only,
    )
    model.eval()
    inputs = tokenizer(prompt, return_tensors="pt")

    with torch.inference_mode():
        outputs = model(**inputs)
        logits = outputs.logits[:, -1, :].float()
        top = torch.topk(logits[0], k=min(top_k, logits.shape[-1]))
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

    generated_ids = [int(item) for item in generated[0].tolist()]
    top_ids = [int(item) for item in top.indices.tolist()]
    top_values = [float(item) for item in top.values.tolist()]
    checksum_payload = json.dumps(
        {
            "generated_ids": generated_ids,
            "top_ids": top_ids,
            "top_values": [round(value, 6) for value in top_values],
        },
        sort_keys=True,
    ).encode("utf-8")

    return {
        **report,
        "mode": "live",
        "status": "pass",
        "full_checkpoint_execution": True,
        "prompt_token_count": int(inputs["input_ids"].shape[-1]),
        "generated_token_ids": generated_ids,
        "generated_text": tokenizer.decode(generated_ids, skip_special_tokens=False),
        "next_token_top_k": [
            {"token_id": token_id, "logit": value}
            for token_id, value in zip(top_ids, top_values, strict=True)
        ],
        "result_sha256": hashlib.sha256(checksum_payload).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "e1/model/tinyllama_manifest.json")
    parser.add_argument("--cache-dir", type=Path, default=REPO_ROOT / ".cache/e1/tinyllama-1.1b-chat-v1.0")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=["preflight", "live"], default="preflight")
    parser.add_argument("--prompt", default="The capital of France is")
    parser.add_argument("--max-new-tokens", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    cache_dir = args.cache_dir if args.cache_dir.is_absolute() else REPO_ROOT / args.cache_dir
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    manifest = load_json(manifest_path)

    if args.mode == "live":
        report = run_live(
            manifest,
            cache_dir,
            args.prompt,
            args.max_new_tokens,
            args.top_k,
            args.allow_download,
        )
    else:
        report = preflight_report(manifest, cache_dir, args.prompt, args.max_new_tokens)

    write_json(report_path, report)
    if args.mode == "live" and report["status"] != "pass":
        print(f"FAIL e1_full_tinyllama_checkpoint {report['status']} -> {repo_rel(report_path)}")
        return 1

    print(f"PASS e1_full_tinyllama_checkpoint {report['status']} -> {repo_rel(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
