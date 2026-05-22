#!/usr/bin/env python3
"""Run the deterministic E1 pass scaffold.

This does not download the full TinyLlama checkpoint by default. It records the
pinned checkpoint and command, then runs a reduced StableHLO fixture through the
same artifact boundaries that the real TinyLlama export will use.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repo_rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def stablehlo_ops(text: str) -> Counter[str]:
    return Counter(re.findall(r"stablehlo\.([a-zA-Z_][a-zA-Z0-9_]*)", text))


def tensors(text: str) -> list[str]:
    seen = sorted(set(re.findall(r"tensor<[^>]+>", text)))
    return seen


def load_ip_manifests(ip_dir: Path) -> list[dict[str, Any]]:
    manifests = [load_json(path) for path in sorted(ip_dir.glob("*.json"))]
    return sorted(manifests, key=lambda item: (int(item["order"]), item["name"]))


def unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def load_soc_top_generator(e1_h1_dir: Path) -> Any:
    generator_path = e1_h1_dir / "tools" / "generate_soc_top.py"
    spec = importlib.util.spec_from_file_location("e1_h1_generate_soc_top", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {generator_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def emit_soc_top_artifacts(e1_h1_dir: Path, architecture_path: Path) -> dict[str, str]:
    generator = load_soc_top_generator(e1_h1_dir)
    ip_dir = e1_h1_dir / "ip"
    top_path = e1_h1_dir / "generated" / "e1_h1_soc_top.sv"
    manifest_path = e1_h1_dir / "generated" / "e1_h1_soc_top_manifest.json"
    interfaces_path = e1_h1_dir / "generated" / "e1_h1_interface_contracts.json"
    write_text(top_path, generator.generate(architecture_path, ip_dir))
    write_json(manifest_path, generator.generate_composition_manifest(architecture_path, ip_dir))
    write_json(interfaces_path, generator.generate_interface_contracts(architecture_path, ip_dir))
    return {
        "top": repo_rel(top_path),
        "composition_manifest": repo_rel(manifest_path),
        "interface_contracts": repo_rel(interfaces_path),
    }


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def fetch_report(manifest: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    return {
        "schema": "e1-fetch-model-report-v0",
        "model_id": manifest["model_id"],
        "source": manifest["source"],
        "mode": "offline",
        "hf_available": shutil.which("hf") is not None,
        "hf_path": shutil.which("hf"),
        "command": [
            "hf",
            "download",
            manifest["source"]["repo"],
            "--revision",
            manifest["source"]["revision"],
            "--local-dir",
            repo_rel(cache_dir),
        ],
        "cache_dir": repo_rel(cache_dir),
        "cache_exists": cache_dir.exists(),
        "large_artifacts_committed": False,
        "ready_for_live_fetch": shutil.which("hf") is not None,
        "status": "offline_fixture",
    }


def stablehlo_export_report(
    manifest: dict[str, Any],
    fetch: dict[str, Any],
    fetch_report_path: Path,
    stablehlo_out: Path,
    fixture_path: Path,
) -> dict[str, Any]:
    deps = {
        "torch": has_module("torch"),
        "transformers": has_module("transformers"),
        "jax": has_module("jax"),
    }
    return {
        "schema": "e1-stablehlo-export-report-v0",
        "model_id": manifest["model_id"],
        "mode": "offline",
        "dependencies": deps,
        "fetch_report": repo_rel(fetch_report_path),
        "stablehlo_out": repo_rel(stablehlo_out),
        "fixture": repo_rel(fixture_path),
        "live_ready": all(deps.values()) and fetch.get("cache_exists", False),
        "status": "offline_fixture",
    }


def emit_target_packages(
    e1_h1_dir: Path,
    architecture: dict[str, Any],
    ip_manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    target_dir = e1_h1_dir / "generated" / "targets"
    fpga_dir = target_dir / "fpga"
    openroad_dir = target_dir / "openroad"
    ip_rtl = [
        {
            "name": ip["name"],
            "module": ip["module"],
            "order": ip["order"],
            "manifest": f"e1/e1-h1/ip/{ip['name']}.json",
            "rtl": ip["rtl"],
        }
        for ip in ip_manifests
    ]
    rtl_files = [
        e1_h1_dir / "generated" / "e1_h1_soc_top.sv",
        *(REPO_ROOT / path for path in unique_ordered([item["rtl"] for item in ip_rtl])),
    ]
    rtl_rel = [repo_rel(path) for path in rtl_files]
    top = "e1_h1_soc_top"
    rgmii = architecture["io"]["external_data_source"]

    write_text(
        fpga_dir / "rtl.filelist",
        "\n".join(rtl_rel) + "\n",
    )
    write_text(
        fpga_dir / "constraints.xdc",
        "\n".join(
            [
                "# E1-H1 FPGA smoke constraints.",
                "# Digital-only RGMII boundary. External PHY owns analog signaling.",
                "create_clock -name clk_i -period 10.000 [get_ports clk_i]",
                "create_clock -name rgmii_rx_clk_i -period 8.000 [get_ports rgmii_rx_clk_i]",
                "set_property IOSTANDARD LVCMOS33 [get_ports {rgmii_rxd_i[*] rgmii_rx_ctl_i rgmii_rx_clk_i}]",
                "set_false_path -from [get_ports rgmii_rx_clk_i] -to [get_ports clk_i]",
                "",
            ]
        ),
    )
    write_text(
        fpga_dir / "run_synth.tcl",
        "\n".join(
            [
                "# Smoke synthesis script fragment for E1-H1.",
                f"set top {top}",
                "set rtl_files [split [read [open rtl.filelist r]] \"\\n\"]",
                "foreach f $rtl_files { if {$f ne \"\"} { read_verilog -sv $f } }",
                "synth_design -top $top",
                "",
            ]
        ),
    )

    write_text(
        openroad_dir / "rtl.filelist",
        "\n".join(rtl_rel) + "\n",
    )
    write_text(
        openroad_dir / "constraints.sdc",
        "\n".join(
            [
                "# E1-H1 OpenROAD smoke constraints.",
                "# Digital-only RGMII boundary. No mixed-signal PHY macro is required.",
                "create_clock -name clk_i -period 10.000 [get_ports clk_i]",
                "create_clock -name rgmii_rx_clk_i -period 8.000 [get_ports rgmii_rx_clk_i]",
                "set_input_delay 1.000 -clock rgmii_rx_clk_i [get_ports {rgmii_rxd_i[*] rgmii_rx_ctl_i}]",
                "set_false_path -from [get_ports rgmii_rx_clk_i] -to [get_ports clk_i]",
                "",
            ]
        ),
    )
    write_text(
        openroad_dir / "config.mk",
        "\n".join(
            [
                "# E1-H1 OpenROAD smoke package.",
                f"DESIGN_NAME := {top}",
                "VERILOG_FILES := $(shell cat rtl.filelist)",
                "SDC_FILE := constraints.sdc",
                "PLATFORM ?= sky130hd",
                "",
            ]
        ),
    )

    manifest = {
        "schema": "e1-h1-target-package-v0",
        "top": top,
        "digital_only": True,
        "external_data_source": {
            "kind": rgmii["kind"],
            "mac_interface": rgmii["mac_interface"],
            "phy_boundary": rgmii["phy_boundary"],
        },
        "rtl_source": "e1/e1-h1/ip/*.json",
        "ip_rtl": ip_rtl,
        "rtl_files": rtl_rel,
        "fpga": {
            "filelist": repo_rel(fpga_dir / "rtl.filelist"),
            "constraints": repo_rel(fpga_dir / "constraints.xdc"),
            "script": repo_rel(fpga_dir / "run_synth.tcl"),
        },
        "openroad": {
            "filelist": repo_rel(openroad_dir / "rtl.filelist"),
            "constraints": repo_rel(openroad_dir / "constraints.sdc"),
            "config": repo_rel(openroad_dir / "config.mk"),
        },
    }
    write_json(target_dir / "manifest.json", manifest)
    return manifest


def run_chip_model_smoke(output_dir: Path) -> dict[str, Any]:
    compiler = shutil.which("c++")
    if compiler is None:
        return {
            "schema": "e1-chip-model-smoke-v0",
            "status": "missing_cxx",
            "compile_command": [],
            "program": "first_attention_tile",
        }

    with tempfile.TemporaryDirectory(prefix="e1_chip_model_") as tmp:
        exe = Path(tmp) / "e1_chip_smoke"
        compile_command = [
            compiler,
            "-std=c++17",
            "-I",
            "e1/code/chip_model",
            "e1/code/chip_model/e1_chip_model.cpp",
            "e1/code/chip_model/e1_chip_smoke.cpp",
            "-o",
            str(exe),
        ]
        subprocess.run(
            compile_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        result = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        report = json.loads(result.stdout)
        report["compile_command"] = compile_command[:-1] + ["<temp-exe>"]
        report["source"] = "e1/code/chip_model/e1_chip_smoke.cpp"
        write_json(output_dir / "08_chip_model_run.json", report)
        return report


def run_pipeline(manifest_path: Path, architecture_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    architecture = load_json(architecture_path)
    e1_h1_dir = architecture_path.parent.parent
    fixture_path = REPO_ROOT / manifest["frontend"]["fixture"]
    fixture_text = fixture_path.read_text(encoding="utf-8")
    ops = stablehlo_ops(fixture_text)
    tensor_types = tensors(fixture_text)
    ip_manifests = load_ip_manifests(e1_h1_dir / "ip")

    if architecture["io"]["external_data_source"]["kind"] != "ethernet":
        raise ValueError("E1-H1 external data source must be ethernet")
    if architecture["io"]["external_data_source"]["mac_interface"] != "rgmii":
        raise ValueError("E1-H1 external data source must use RGMII")

    passes: list[dict[str, str]] = []

    model_manifest_out = output_dir / "00_model_manifest.json"
    write_json(model_manifest_out, manifest)

    fetch_out = output_dir / "01_fetch_model.json"
    fetch = fetch_report(manifest, REPO_ROOT / ".cache/e1/tinyllama-1.1b-chat-v1.0")
    write_json(fetch_out, fetch)
    passes.append({"pass": "e1_fetch_model", "artifact": repo_rel(fetch_out)})

    stablehlo_out = output_dir / "02_stablehlo.mlir"
    write_text(stablehlo_out, fixture_text)
    stablehlo_report_out = output_dir / "02_stablehlo_export.json"
    write_json(
        stablehlo_report_out,
        stablehlo_export_report(manifest, fetch, fetch_out, stablehlo_out, fixture_path),
    )
    passes.append({"pass": "e1_export_stablehlo", "artifact": repo_rel(stablehlo_out)})

    inspection_out = output_dir / "03_stablehlo_inspection.json"
    systolic_ops = {"dot_general"}
    cpu_ops = set(ops) - systolic_ops
    write_json(
        inspection_out,
        {
            "operation_counts": dict(sorted(ops.items())),
            "total_operations": sum(ops.values()),
            "systolic_array_ops": sorted(systolic_ops & set(ops)),
            "cpu_or_stream_ops": sorted(cpu_ops),
            "tensor_types": tensor_types,
            "unsupported_ops": [],
            "answers": {
                "dominant_operations": ["dot_general"],
                "systolic_array_mapping": "stablehlo.dot_general maps to the E1-H1 systolic array command interface.",
                "cpu_mapping": "gather, add, multiply, tanh, and control sequencing remain CPU/C++ model responsibilities in this scaffold.",
                "sram_staging": "weights, activations, and accumulator tiles stage through configurable on-chip SRAM.",
                "external_data_source": "Ethernet/RGMII ingress feeds on-chip SRAM; off-chip DRAM is not a source.",
                "fallbacks": "No unsupported fixture operations require fallback.",
            },
        },
    )
    passes.append({"pass": "e1_inspect_stablehlo", "artifact": repo_rel(inspection_out)})

    normalized_out = output_dir / "04_normalized_stablehlo.mlir"
    write_text(
        normalized_out,
        "// E1 normalized StableHLO fixture. Real normalization will canonicalize the full TinyLlama export.\n"
        + fixture_text,
    )
    passes.append({"pass": "e1_normalize_stablehlo", "artifact": repo_rel(normalized_out)})

    binding_out = output_dir / "05_e1_h1_binding.json"
    write_json(
        binding_out,
        {
            "architecture_id": architecture["architecture_id"],
            "bindings": {
                "stablehlo.dot_general": "systolic_array",
                "stablehlo.gather": "control_cpu",
                "stablehlo.add": "control_cpu",
                "stablehlo.multiply": "control_cpu",
                "stablehlo.tanh": "control_cpu",
                "external_data": "rgmii_ethernet_ingress",
                "staging": "ingress_sram",
            },
            "ip": [ip["name"] for ip in ip_manifests],
        },
    )
    passes.append({"pass": "e1_bind_e1_h1", "artifact": repo_rel(binding_out)})

    memory_out = output_dir / "06_memory_plan.json"
    write_json(
        memory_out,
        {
            "sram": architecture["memory"]["sram"],
            "tile_plan": {
                "attention_qkv": {"rows": 16, "cols": 16, "depth": 16},
                "mlp_up": {"rows": 16, "cols": 64, "depth": 16},
                "mlp_down": {"rows": 64, "cols": 16, "depth": 64},
            },
            "external_source": architecture["io"]["external_data_source"],
        },
    )
    passes.append({"pass": "e1_plan_memory", "artifact": repo_rel(memory_out)})

    device_out = output_dir / "07_device_program_plan.json"
    write_json(
        device_out,
        {
            "program": "e1/code/program/e1_tinyllama_program.cpp",
            "mmio": "e1/code/program/e1_device_mmio.hpp",
            "legibility_rule": "named MMIO constants and explicit tile commands",
        },
    )
    passes.append({"pass": "e1_plan_device_program", "artifact": repo_rel(device_out)})

    chip_model_out = output_dir / "08_chip_model_plan.json"
    chip_model_run = run_chip_model_smoke(output_dir)
    write_json(
        chip_model_out,
        {
            "chip_model": [
                "e1/code/chip_model/e1_chip_model.hpp",
                "e1/code/chip_model/e1_chip_model.cpp",
                "e1/code/chip_model/e1_chip_smoke.cpp",
            ],
            "run_report": "e1/generated/pipeline/08_chip_model_run.json",
            "run_status": chip_model_run["status"],
            "replaceable_blocks": [ip["name"] for ip in ip_manifests],
        },
    )
    passes.append({"pass": "e1_generate_chip_model", "artifact": repo_rel(chip_model_out)})

    harness_out = output_dir / "09_l1_5_harness_plan.json"
    write_json(
        harness_out,
        {
            "harnesses": {
                ip["name"]: ip["l1_5_harness"]
                for ip in ip_manifests
            },
            "module_vips": {
                ip["name"]: ip["module_vip"]
                for ip in ip_manifests
            }
        },
    )
    passes.append({"pass": "e1_generate_l1_5_harnesses", "artifact": repo_rel(harness_out)})

    soc_top_artifacts = emit_soc_top_artifacts(e1_h1_dir, architecture_path)
    graph_out = output_dir / "10_hardware_graph.json"
    write_json(
        graph_out,
        {
            "top": "e1_h1_soc_top",
            "generator": "e1/e1-h1/tools/generate_soc_top.py",
            "composition_manifest": soc_top_artifacts["composition_manifest"],
            "interface_contracts": soc_top_artifacts["interface_contracts"],
            "subsystems": [item["name"] for item in architecture["soc_top"]["subsystems"]],
            "ips": [
                {
                    "name": ip["name"],
                    "module": ip["module"],
                    "subsystem": ip["subsystem"],
                    "rtl": ip["rtl"],
                    "spec": ip["spec"],
                    "module_vip": ip["module_vip"],
                    "replaceable": ip["replaceable"],
                }
                for ip in ip_manifests
            ],
        },
    )
    passes.append({"pass": "e1_lower_to_hardware_graph", "artifact": repo_rel(graph_out)})

    sv_out = output_dir / "11_systemverilog_plan.json"
    write_json(
        sv_out,
        {
            "generated_top": soc_top_artifacts["top"],
            "generated_composition_manifest": soc_top_artifacts["composition_manifest"],
            "generated_interface_contracts": soc_top_artifacts["interface_contracts"],
            "mock_rtl": sorted(repo_rel(path) for path in (e1_h1_dir / "rtl" / "ip").glob("*.sv")),
            "composition_source": "e1/e1-h1/ip/*.json",
        },
    )
    passes.append({"pass": "e1_emit_systemverilog", "artifact": repo_rel(sv_out)})

    target_manifest = emit_target_packages(e1_h1_dir, architecture, ip_manifests)
    target_out = output_dir / "12_target_package_plan.json"
    write_json(
        target_out,
        {
            "fpga": {"status": "smoke", "top": "e1_h1_soc_top", "package": target_manifest["fpga"]},
            "asic_openroad": {"status": "smoke", "top": "e1_h1_soc_top", "package": target_manifest["openroad"]},
            "manifest": "e1/e1-h1/generated/targets/manifest.json",
            "digital_only": True,
        },
    )
    passes.append({"pass": "e1_package_targets", "artifact": repo_rel(target_out)})

    summary_out = output_dir / "summary.json"
    summary = {
        "schema": "e1-pipeline-summary-v0",
        "model_id": manifest["model_id"],
        "architecture_id": architecture["architecture_id"],
        "stablehlo_fixture": repo_rel(fixture_path),
        "pass_count": len(passes),
        "passes": passes,
        "operation_counts": dict(sorted(ops.items())),
        "all_current_modules_have_l1_5_harnesses": all("l1_5_harness" in ip for ip in ip_manifests),
        "generated_top": "e1/e1-h1/generated/e1_h1_soc_top.sv",
    }
    write_json(summary_out, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "e1/model/tinyllama_manifest.json")
    parser.add_argument("--architecture", type=Path, default=REPO_ROOT / "e1/e1-h1/config/architecture.json")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "e1/generated/pipeline")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)

    summary = run_pipeline(
        args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest,
        args.architecture if args.architecture.is_absolute() else REPO_ROOT / args.architecture,
        output_dir,
    )
    print(f"PASS e1_pipeline {summary['pass_count']} passes -> {repo_rel(output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
