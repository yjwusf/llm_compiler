#!/usr/bin/env python3
"""Run the deterministic E1 pass scaffold.

This does not download the full TinyLlama checkpoint by default. It records the
pinned checkpoint and command, then runs a reduced StableHLO fixture through the
same artifact boundaries that the real TinyLlama export will use.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
    return [load_json(path) for path in sorted(ip_dir.glob("*.json"))]


def emit_target_packages(e1_h1_dir: Path, architecture: dict[str, Any]) -> dict[str, Any]:
    target_dir = e1_h1_dir / "generated" / "targets"
    fpga_dir = target_dir / "fpga"
    openroad_dir = target_dir / "openroad"
    rtl_files = [
        e1_h1_dir / "generated" / "e1_h1_soc_top.sv",
        *sorted((e1_h1_dir / "rtl" / "ip").glob("*.sv")),
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
    write_json(
        fetch_out,
        {
            "model_id": manifest["model_id"],
            "source": manifest["source"],
            "download_command": manifest["download"]["command"],
            "mode": "offline_fixture",
            "large_artifacts_committed": False,
            "cache_expected": ".cache/e1/tinyllama-1.1b-chat-v1.0",
        },
    )
    passes.append({"pass": "e1_fetch_model", "artifact": repo_rel(fetch_out)})

    stablehlo_out = output_dir / "02_stablehlo.mlir"
    write_text(stablehlo_out, fixture_text)
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
    write_json(
        chip_model_out,
        {
            "chip_model": [
                "e1/code/chip_model/e1_chip_model.hpp",
                "e1/code/chip_model/e1_chip_model.cpp",
            ],
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
            }
        },
    )
    passes.append({"pass": "e1_generate_l1_5_harnesses", "artifact": repo_rel(harness_out)})

    graph_out = output_dir / "10_hardware_graph.json"
    write_json(
        graph_out,
        {
            "top": "e1_h1_soc_top",
            "generator": "e1/e1-h1/tools/generate_soc_top.py",
            "ips": [
                {
                    "name": ip["name"],
                    "module": ip["module"],
                    "spec": ip["spec"],
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
            "generated_top": "e1/e1-h1/generated/e1_h1_soc_top.sv",
            "mock_rtl": sorted(repo_rel(path) for path in (e1_h1_dir / "rtl" / "ip").glob("*.sv")),
            "composition_source": "e1/e1-h1/ip/*.json",
        },
    )
    passes.append({"pass": "e1_emit_systemverilog", "artifact": repo_rel(sv_out)})

    target_manifest = emit_target_packages(e1_h1_dir, architecture)
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
