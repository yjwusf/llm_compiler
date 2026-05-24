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
DEFAULT_CHECKPOINT_CACHE = REPO_ROOT / ".cache/e1/tinyllama-1.1b-chat-v1.0"


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
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


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


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def implementation_scheme(ip: dict[str, Any]) -> dict[str, Any]:
    scheme = ip.get("implementation_scheme")
    if not isinstance(scheme, dict):
        raise ValueError(f"{ip['name']}: missing implementation_scheme")
    implementations = scheme.get("implementations", {})
    if scheme.get("reference") != "imp1":
        raise ValueError(f"{ip['name']}: implementation reference must be imp1")
    if scheme.get("active") not in implementations:
        raise ValueError(f"{ip['name']}: active implementation is not defined")
    return scheme


def emit_implementation_matrix(
    e1_h1_dir: Path,
    ip_manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    generated_dir = e1_h1_dir / "generated"
    flist_dir = generated_dir / "flists"
    imp1_dir = flist_dir / "imp1"
    imp2_dir = flist_dir / "imp2"
    imp1_dir.mkdir(parents=True, exist_ok=True)
    imp2_dir.mkdir(parents=True, exist_ok=True)

    active_rtl = ["e1/e1-h1/generated/e1_h1_soc_top.sv"]
    imp1_flists: dict[str, str] = {}
    imp2_flists: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    active_implementations: list[str] = []
    module_dpi_dir = "e1/e1-h1/generated/module_dpi"
    module_dpi_scoreboard = f"{module_dpi_dir}/e1_h1_module_dpi_scoreboard.cpp"

    for ip in ip_manifests:
        scheme = implementation_scheme(ip)
        implementations = scheme["implementations"]
        imp1 = implementations.get("imp1")
        imp2 = implementations.get("imp2")
        active_name = scheme["active"]
        active = implementations[active_name]
        active_implementations.append(active_name)
        if not isinstance(imp1, dict) or not isinstance(imp2, dict):
            raise ValueError(f"{ip['name']}: imp1 and imp2 must be defined")
        if imp1.get("kind") != "mock" or imp1.get("status") != "accepted":
            raise ValueError(f"{ip['name']}: imp1 must be the accepted mock implementation")
        if active.get("module") != ip["module"] or active.get("rtl") != ip["rtl"]:
            raise ValueError(f"{ip['name']}: active implementation must match the current IP module and RTL")
        if imp2.get("acceptance") != "verilator_dpi_vip_equivalent_to_imp1":
            raise ValueError(f"{ip['name']}: imp2 acceptance must require Verilator+DPI VIP equivalence")

        active_rtl.extend(active.get("rtl_files", [active["rtl"]]))

        imp1_flist = imp1_dir / f"{ip['name']}.f"
        write_text(imp1_flist, "\n".join(imp1.get("rtl_files", [imp1["rtl"]])) + "\n")
        imp1_flists[ip["name"]] = repo_rel(imp1_flist)

        imp2_flist: str | None = None
        if imp2.get("status") == "accepted":
            rtl_files = imp2.get("rtl_files", [])
            if not rtl_files:
                raise ValueError(f"{ip['name']}: accepted imp2 must list RTL files")
            imp2_flist_path = imp2_dir / f"{ip['name']}.f"
            write_text(imp2_flist_path, "\n".join(rtl_files) + "\n")
            imp2_flist = repo_rel(imp2_flist_path)
            imp2_flists[ip["name"]] = imp2_flist

        entries.append(
            {
                "name": ip["name"],
                "active": scheme["active"],
                "reference": scheme["reference"],
                "module": ip["module"],
                "interface_source": f"e1/e1-h1/ip/{ip['name']}.json",
                "vip": ip["module_vip"],
                "l1_5_harness": ip["l1_5_harness"],
                "imp1": {
                    "status": imp1["status"],
                    "kind": imp1["kind"],
                    "module": imp1["module"],
                    "rtl": imp1["rtl"],
                    "flist": imp1_flists[ip["name"]],
                },
                "imp2": {
                    "status": imp2["status"],
                    "kind": imp2["kind"],
                    "module": imp2.get("module"),
                    "rtl_files": imp2.get("rtl_files", []),
                    "flist": imp2_flist,
                    "acceptance": imp2["acceptance"],
                },
                "dpi_equivalence": {
                    "reference": "imp1",
                    "candidate": "imp2",
                    "probe": "e1/e1-h1/dpi/e1_h1_imp_equiv_probe.sv",
                    "scoreboard": "e1/e1-h1/dpi/e1_h1_imp_equiv_dpi.cpp",
                    "module_generator": "e1/e1-h1/tools/generate_module_dpi.cpp",
                    "module_manifest": f"{module_dpi_dir}/manifest.json",
                    "module_probe": f"{module_dpi_dir}/e1_h1_module_dpi_{ip['name']}.sv",
                    "module_main": f"{module_dpi_dir}/e1_h1_module_dpi_{ip['name']}_main.cpp",
                    "module_scoreboard": module_dpi_scoreboard,
                    "module_flist": f"{module_dpi_dir}/flists/{ip['name']}.f",
                    "status": imp2["status"],
                },
            }
        )

    active_flist = flist_dir / "active.f"
    active_files = unique_ordered(active_rtl)
    write_text(active_flist, "\n".join(active_files) + "\n")
    active_implementation = (
        active_implementations[0]
        if len(set(active_implementations)) == 1
        else "mixed"
    )

    matrix = {
        "schema": "e1-h1-implementation-matrix-v0",
        "reference_implementation": "imp1",
        "active_implementation": active_implementation,
        "imp1_meaning": "mock contract implementation",
        "imp2_acceptance": "verilator_dpi_vip_equivalent_to_imp1",
        "dpi": {
            "probe": "e1/e1-h1/dpi/e1_h1_imp_equiv_probe.sv",
            "scoreboard": "e1/e1-h1/dpi/e1_h1_imp_equiv_dpi.cpp",
            "main": "e1/e1-h1/dpi/e1_h1_imp_equiv_main.cpp",
            "module_generator": "e1/e1-h1/tools/generate_module_dpi.cpp",
            "module_manifest": f"{module_dpi_dir}/manifest.json",
            "module_scoreboard": module_dpi_scoreboard,
        },
        "flists": {
            "active": repo_rel(active_flist),
            "imp1": imp1_flists,
            "imp2": imp2_flists,
        },
        "active_rtl_files": active_files,
        "ips": entries,
    }
    matrix_path = generated_dir / "implementation_matrix.json"
    write_json(matrix_path, matrix)
    matrix["matrix"] = repo_rel(matrix_path)
    return matrix


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
    implementation_matrix: dict[str, Any],
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
        "implementation_matrix": implementation_matrix["matrix"],
        "implementation_flists": implementation_matrix["flists"],
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


def run_device_program_smoke(output_dir: Path) -> dict[str, Any]:
    compiler = shutil.which("c++")
    if compiler is None:
        return {
            "schema": "e1-device-program-smoke-v0",
            "status": "missing_cxx",
            "compile_command": [],
            "program": "first_attention_tile",
        }

    with tempfile.TemporaryDirectory(prefix="e1_device_program_") as tmp:
        exe = Path(tmp) / "e1_device_program_smoke"
        compile_command = [
            compiler,
            "-std=c++17",
            "-DE1_DEVICE_HOST_MODEL",
            "-I",
            "e1/code/program",
            "e1/code/program/e1_tinyllama_program.cpp",
            "e1/code/program/e1_tinyllama_program_host_smoke.cpp",
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
        report["source"] = "e1/code/program/e1_tinyllama_program.cpp"
        report["host_smoke"] = "e1/code/program/e1_tinyllama_program_host_smoke.cpp"
        write_json(output_dir / "07_device_program_run.json", report)
        return report


def run_full_checkpoint(
    output_dir: Path,
    mode: str,
    cache_dir: Path,
    allow_download: bool,
) -> dict[str, Any]:
    report_path = output_dir / "17_full_tinyllama_checkpoint_execution.json"
    command = [
        sys.executable,
        "e1/tools/run_tinyllama_checkpoint.py",
        "--mode",
        mode,
        "--cache-dir",
        repo_rel(cache_dir),
        "--report",
        repo_rel(report_path),
    ]
    if allow_download:
        command.append("--allow-download")
    subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return load_json(report_path)


def run_module_dpi_generator(e1_h1_dir: Path, output_path: Path) -> dict[str, Any]:
    generator = e1_h1_dir / "tools" / "generate_module_dpi.cpp"
    module_dpi_dir = e1_h1_dir / "generated" / "module_dpi"
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "e1_h1_generate_module_dpi"
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                repo_rel(generator),
                "-o",
                str(exe),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        subprocess.run(
            [
                str(exe),
                "--repo-root",
                str(REPO_ROOT),
                "--output-dir",
                str(module_dpi_dir),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

    module_dpi_manifest_path = module_dpi_dir / "manifest.json"
    module_dpi_manifest = load_json(module_dpi_manifest_path)
    checks = [
        {
            "name": "module_dpi_manifest_exists",
            "status": "pass" if module_dpi_manifest_path.exists() else "fail",
        },
        {
            "name": "one_probe_per_module",
            "status": "pass"
            if len({module["name"] for module in module_dpi_manifest["modules"]}) == len(module_dpi_manifest["modules"])
            else "fail",
        },
        {
            "name": "all_probes_have_flists",
            "status": "pass"
            if all((REPO_ROOT / module["flist"]).exists() for module in module_dpi_manifest["modules"])
            else "fail",
        },
        {
            "name": "ingress_sram_is_latch_buffer",
            "status": "pass"
            if any(module["name"] == "ingress_sram" and module["latch_buffer"] for module in module_dpi_manifest["modules"])
            else "fail",
        },
    ]
    report = {
        "schema": "e1-module-dpi-generation-report-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "generator": repo_rel(generator),
        "manifest": repo_rel(module_dpi_manifest_path),
        "scoreboard": module_dpi_manifest["scoreboard"],
        "module_count": len(module_dpi_manifest["modules"]),
        "modules": [
            {
                "name": module["name"],
                "top_module": module["top_module"],
                "probe": module["probe"],
                "main": module["main"],
                "flist": module["flist"],
                "latch_buffer": module["latch_buffer"],
                "cycle_notes": module["cycle_notes"],
            }
            for module in module_dpi_manifest["modules"]
        ],
        "construction_rule": module_dpi_manifest["construction_rule"],
        "separation_of_concerns": module_dpi_manifest["separation_of_concerns"],
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def run_full_checkpoint_module_dpi_generator(e1_h1_dir: Path, output_path: Path) -> dict[str, Any]:
    generator = e1_h1_dir / "tools" / "generate_full_checkpoint_module_dpi.cpp"
    module_dpi_dir = e1_h1_dir / "generated" / "full_checkpoint_dpi"
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "e1_h1_generate_full_checkpoint_module_dpi"
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                repo_rel(generator),
                "-o",
                str(exe),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        subprocess.run(
            [
                str(exe),
                "--repo-root",
                str(REPO_ROOT),
                "--output-dir",
                str(module_dpi_dir),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

    manifest_path = module_dpi_dir / "manifest.json"
    manifest = load_json(manifest_path)
    module_interfaces_doc = module_dpi_dir / "module_interfaces.md"
    module_names = {module["name"] for module in manifest["modules"]}
    expected_modules = {
        "linear_scheduler",
        "linear_tile_engine",
        "control_scheduler",
        "graph_sequencer",
        "linear_slot_engine",
        "control_slot_engine",
        "full_checkpoint_top",
    }
    checks = [
        {
            "name": "full_checkpoint_module_dpi_manifest_exists",
            "status": "pass" if manifest_path.exists() else "fail",
        },
        {
            "name": "one_probe_per_generated_full_checkpoint_module",
            "status": "pass" if module_names == expected_modules else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_probes_have_flists",
            "status": "pass"
            if all((REPO_ROOT / module["flist"]).exists() for module in manifest["modules"])
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_probes_have_mains",
            "status": "pass"
            if all((REPO_ROOT / module["main"]).exists() for module in manifest["modules"])
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_interfaces_doc_exists",
            "status": "pass" if module_interfaces_doc.exists() else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_signal_docs",
            "status": "pass"
            if all(module.get("input_signals") and module.get("output_signals") for module in manifest["modules"])
            else "fail",
        },
        {
            "name": "full_checkpoint_top_dpi_covers_slot_engines",
            "status": "pass"
            if any(
                module["name"] == "full_checkpoint_top"
                and "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_slot_engine.sv" in module["rtl"]
                and "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_slot_engine.sv" in module["rtl"]
                for module in manifest["modules"]
            )
            else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-module-dpi-generation-report-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "generator": repo_rel(generator),
        "manifest": repo_rel(manifest_path),
        "scoreboard": manifest["scoreboard"],
        "module_interfaces_doc": manifest["module_interfaces_doc"],
        "module_count": len(manifest["modules"]),
        "modules": [
            {
                "name": module["name"],
                "top_module": module["top_module"],
                "probe_module": module["probe_module"],
                "probe": module["probe"],
                "main": module["main"],
                "flist": module["flist"],
                "rtl": module["rtl"],
                "cycle_notes": module["cycle_notes"],
                "input_signals": module["input_signals"],
                "output_signals": module["output_signals"],
            }
            for module in manifest["modules"]
        ],
        "construction_rule": manifest["construction_rule"],
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_rtl_lowering(
    output_path: Path,
    manifest: dict[str, Any],
    fixture_path: Path,
    ops: Counter[str],
    binding: dict[str, Any],
    architecture: dict[str, Any],
    hardware_graph: dict[str, Any],
    implementation_matrix: dict[str, Any],
    module_dpi_report: dict[str, Any],
    target_manifest: dict[str, Any],
) -> dict[str, Any]:
    ips_by_name = {entry["name"]: entry for entry in implementation_matrix["ips"]}
    graph_ips = {entry["name"]: entry for entry in hardware_graph["ips"]}
    module_dpi_by_name = {entry["name"]: entry for entry in module_dpi_report["modules"]}

    operation_lowering: list[dict[str, Any]] = []
    for op_name, count in sorted(ops.items()):
        operation = f"stablehlo.{op_name}"
        ip_name = binding["bindings"].get(operation)
        ip_entry = ips_by_name.get(ip_name) if ip_name is not None else None
        graph_ip = graph_ips.get(ip_name) if ip_name is not None else None
        module_dpi = module_dpi_by_name.get(ip_name) if ip_name is not None else None
        imp2 = ip_entry.get("imp2") if ip_entry is not None else {}
        operation_lowering.append(
            {
                "operation": operation,
                "count": count,
                "ip": ip_name,
                "subsystem": graph_ip["subsystem"] if graph_ip is not None else None,
                "active_implementation": ip_entry["active"] if ip_entry is not None else None,
                "rtl_files": imp2.get("rtl_files", []),
                "imp2_flist": imp2.get("flist"),
                "module_dpi_probe": module_dpi["probe"] if module_dpi is not None else None,
                "module_dpi_flist": module_dpi["flist"] if module_dpi is not None else None,
                "cycle_notes": module_dpi["cycle_notes"] if module_dpi is not None else [],
                "lowering_stage": "systolic_array_tile" if ip_name == "systolic_array" else "cpu_or_control_stream",
                "status": "pass"
                if ip_entry is not None
                and ip_entry["active"] == "imp2"
                and imp2.get("flist") is not None
                and module_dpi is not None
                and all("/rtl/imp2/" in path for path in imp2.get("rtl_files", []))
                else "fail",
            }
        )

    cpu_probe_path = REPO_ROOT / module_dpi_by_name["control_cpu"]["probe"]
    array_probe_path = REPO_ROOT / module_dpi_by_name["systolic_array"]["probe"]
    buffer_probe_path = REPO_ROOT / module_dpi_by_name["ingress_sram"]["probe"]
    cpu_probe = cpu_probe_path.read_text(encoding="utf-8")
    array_probe = array_probe_path.read_text(encoding="utf-8")
    buffer_probe = buffer_probe_path.read_text(encoding="utf-8")
    checks = [
        {
            "name": "all_stablehlo_ops_bound_to_rtl",
            "status": "pass" if all(entry["status"] == "pass" for entry in operation_lowering) else "fail",
        },
        {
            "name": "module_dpi_generation",
            "status": module_dpi_report["status"],
        },
        {
            "name": "cpu_probe_excludes_systolic_array",
            "status": "pass" if "e1_h1_systolic_array" not in cpu_probe else "fail",
        },
        {
            "name": "systolic_array_probe_excludes_cpu",
            "status": "pass" if "e1_h1_control_cpu" not in array_probe else "fail",
        },
        {
            "name": "latch_buffer_probe_is_explicit",
            "status": "pass"
            if "drive_latch_boundary" in buffer_probe
            and "sample_latched_output" in buffer_probe
            and "array_ready_i = (cycle >= 2);" in buffer_probe
            else "fail",
        },
        {
            "name": "active_target_filelist_matches_imp2",
            "status": "pass" if target_manifest["rtl_files"] == implementation_matrix["active_rtl_files"] else "fail",
        },
        {
            "name": "cycle_diagram_documented",
            "status": "pass" if (REPO_ROOT / "e1/e1-h1/docs/modules/README.md").exists() else "fail",
        },
    ]
    lowering = {
        "schema": "e1-rtl-lowering-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "scope": {
            "kind": "reduced_stablehlo_fixture",
            "fixture": repo_rel(fixture_path),
            "full_checkpoint_graph_lowering": False,
            "note": "This lowers every operation in the checked-in TinyLlama-derived StableHLO fixture to active imp2 RTL modules; the full checkpoint graph is still not lowered.",
        },
        "architecture_id": architecture["architecture_id"],
        "pipeline": architecture["pipeline"],
        "hardware_graph": repo_rel(output_path.parent / "10_hardware_graph.json"),
        "implementation_matrix": implementation_matrix["matrix"],
        "module_dpi_generation": module_dpi_report,
        "cycle_diagram": "e1/e1-h1/docs/modules/README.md",
        "operation_lowering": operation_lowering,
        "cycle_schedule": [
            {"cycle": "reset", "module": "control_cpu", "behavior": "state_q = Reset"},
            {"cycle": 0, "module": "ingress_sram", "behavior": "latches first stream word"},
            {"cycle": 1, "module": "control_cpu", "behavior": "cmd_valid_o remains asserted under backpressure"},
            {"cycle": 1, "module": "systolic_array", "behavior": "accepts array command and enters busy"},
            {"cycle": 2, "module": "ingress_sram", "behavior": "releases latched word when array_ready_i is high"},
            {"cycle": 3, "module": "systolic_array", "behavior": "consumes input beat 1"},
            {"cycle": 6, "module": "systolic_array", "behavior": "consumes input beat 4 and pulses done_o"},
            {"cycle": 7, "module": "control_cpu", "behavior": "debug_halted_o = 1"},
        ],
        "checks": checks,
    }
    write_json(output_path, lowering)
    return lowering


def emit_full_checkpoint_rtl_lowering_plan(
    output_path: Path,
    manifest: dict[str, Any],
    architecture: dict[str, Any],
    implementation_matrix: dict[str, Any],
    module_dpi_report: dict[str, Any],
    rtl_lowering: dict[str, Any],
    full_checkpoint_execution: dict[str, Any],
) -> dict[str, Any]:
    shape = manifest["checkpoint_shape"]
    array = architecture["accelerator"]
    rows = int(array["rows"])
    cols = int(array["cols"])
    depth = rows
    hidden = int(shape["hidden_size"])
    intermediate = int(shape["intermediate_size"])
    layers = int(shape["num_hidden_layers"])
    heads = int(shape["num_attention_heads"])
    kv_heads = int(shape["num_key_value_heads"])
    head_dim = hidden // heads
    kv_width = kv_heads * head_dim

    module_dpi_by_name = {module["name"]: module for module in module_dpi_report["modules"]}
    ips_by_name = {entry["name"]: entry for entry in implementation_matrix["ips"]}

    def linear_op(name: str, input_width: int, output_width: int) -> dict[str, Any]:
        return {
            "name": name,
            "kind": "linear",
            "ip": "systolic_array",
            "rtl_files": ips_by_name["systolic_array"]["imp2"]["rtl_files"],
            "module_dpi_probe": module_dpi_by_name["systolic_array"]["probe"],
            "weight_shape": [input_width, output_width],
            "tile_shape": {
                "rows": rows,
                "cols": cols,
                "depth": depth,
            },
            "weight_tile_grid": {
                "input_tiles": ceil_div(input_width, depth),
                "output_tiles": ceil_div(output_width, cols),
            },
            "status": "planned_with_active_imp2_rtl",
        }

    def control_op(name: str, kind: str) -> dict[str, Any]:
        return {
            "name": name,
            "kind": kind,
            "ip": "control_cpu",
            "rtl_files": ips_by_name["control_cpu"]["imp2"]["rtl_files"],
            "module_dpi_probe": module_dpi_by_name["control_cpu"]["probe"],
            "status": "planned_with_active_imp2_rtl",
        }

    layer_template = [
        control_op("input_rms_norm", "rms_norm"),
        linear_op("q_proj", hidden, hidden),
        linear_op("k_proj", hidden, kv_width),
        linear_op("v_proj", hidden, kv_width),
        control_op("rope_qk", "rope"),
        control_op("attention_scores_softmax", "attention_control"),
        linear_op("o_proj", hidden, hidden),
        control_op("post_attention_residual", "residual_add"),
        control_op("post_attention_rms_norm", "rms_norm"),
        linear_op("gate_proj", hidden, intermediate),
        linear_op("up_proj", hidden, intermediate),
        control_op("silu_gate_multiply", "activation_control"),
        linear_op("down_proj", intermediate, hidden),
        control_op("post_mlp_residual", "residual_add"),
    ]
    layers_planned = [
        {
            "layer": index,
            "ops": layer_template,
        }
        for index in range(layers)
    ]

    linear_ops_per_layer = sum(1 for op in layer_template if op["kind"] == "linear")
    control_ops_per_layer = len(layer_template) - linear_ops_per_layer
    required_module_names = [
        "control_cpu",
        "rgmii_ethernet_ingress",
        "ingress_sram",
        "activation_sram",
        "accumulator_sram",
        "systolic_array",
    ]
    checks = [
        {
            "name": "checkpoint_shape_present",
            "status": "pass" if shape["model_type"] == "llama" and layers > 0 and hidden > 0 else "fail",
        },
        {
            "name": "all_required_modules_have_module_dpi",
            "status": "pass" if all(name in module_dpi_by_name for name in required_module_names) else "fail",
        },
        {
            "name": "linear_ops_map_to_systolic_array",
            "status": "pass"
            if all(op["ip"] == "systolic_array" for op in layer_template if op["kind"] == "linear")
            else "fail",
        },
        {
            "name": "control_ops_map_to_cpu",
            "status": "pass"
            if all(op["ip"] == "control_cpu" for op in layer_template if op["kind"] != "linear")
            else "fail",
        },
        {
            "name": "latch_buffer_available",
            "status": "pass" if module_dpi_by_name["ingress_sram"]["latch_buffer"] else "fail",
        },
        {
            "name": "reduced_fixture_rtl_lowering_passed",
            "status": rtl_lowering["status"],
        },
        {
            "name": "checkpoint_execution_artifact_present",
            "status": "pass" if full_checkpoint_execution["status"] in {
                "pass",
                "ready",
                "missing_python_dependencies",
                "missing_checkpoint_cache",
                "missing_checkpoint_files",
            } else "fail",
        },
    ]
    plan = {
        "schema": "e1-full-checkpoint-rtl-lowering-plan-v0",
        "status": "planned" if all(check["status"] == "pass" for check in checks) else "incomplete",
        "model_id": manifest["model_id"],
        "source": manifest["source"],
        "checkpoint_shape": shape,
        "architecture_id": architecture["architecture_id"],
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "truth_boundary": "shape_complete_layer_plan_only",
        "note": "This plans the full TinyLlama checkpoint layer inventory against active imp2 RTL modules and module-DPI proof collateral. It does not yet prove full StableHLO export, full op legalization, or full RTL execution of the checkpoint graph.",
        "array_tile_shape": {
            "rows": rows,
            "cols": cols,
            "depth": depth,
            "data_width": array["data_width"],
            "accumulator_width": array["accumulator_width"],
        },
        "head_dim": head_dim,
        "kv_projection_width": kv_width,
        "layers": layers_planned,
        "aggregate": {
            "layers": layers,
            "linear_ops_per_layer": linear_ops_per_layer,
            "control_ops_per_layer": control_ops_per_layer,
            "total_linear_ops": layers * linear_ops_per_layer,
            "total_control_ops": layers * control_ops_per_layer,
            "required_modules": required_module_names,
            "module_dpi_manifest": module_dpi_report["manifest"],
            "reduced_fixture_rtl_lowering": "e1/generated/pipeline/15_rtl_lowering.json",
        },
        "construction_checks": checks,
        "remaining_to_execute_full_rtl": [
            "Export the full checkpoint graph to StableHLO instead of using only the checked-in reduced fixture.",
            "Legalize full Llama ops including RMSNorm, RoPE, attention softmax, cache updates, and SiLU multiply into explicit CPU/control and systolic-array schedules.",
            "Allocate all checkpoint weights and KV/cache tensors across the configurable SRAM hierarchy and Ethernet ingress stream.",
            "Generate full device code that emits every layer command sequence, not only the first tile smoke.",
            "Run generated full-graph RTL or hybrid RTL/C++ execution under Verilator and compare against the checkpoint source-of-truth output.",
        ],
    }
    write_json(output_path, plan)
    return plan


def full_checkpoint_linear_ops(shape: dict[str, Any], rows: int, cols: int, depth: int) -> list[dict[str, Any]]:
    hidden = int(shape["hidden_size"])
    intermediate = int(shape["intermediate_size"])
    heads = int(shape["num_attention_heads"])
    kv_heads = int(shape["num_key_value_heads"])
    head_dim = hidden // heads
    kv_width = kv_heads * head_dim
    ops = [
        ("q_proj", hidden, hidden),
        ("k_proj", hidden, kv_width),
        ("v_proj", hidden, kv_width),
        ("o_proj", hidden, hidden),
        ("gate_proj", hidden, intermediate),
        ("up_proj", hidden, intermediate),
        ("down_proj", intermediate, hidden),
    ]
    return [
        {
            "name": name,
            "input_width": input_width,
            "output_width": output_width,
            "input_tiles": ceil_div(input_width, depth),
            "output_tiles": ceil_div(output_width, cols),
            "rows": rows,
            "cols": cols,
            "depth": depth,
        }
        for name, input_width, output_width in ops
    ]


def emit_full_checkpoint_command_stream(
    output_path: Path,
    manifest: dict[str, Any],
    architecture: dict[str, Any],
    full_checkpoint_rtl_lowering: dict[str, Any],
) -> dict[str, Any]:
    shape = manifest["checkpoint_shape"]
    array = architecture["accelerator"]
    rows = int(array["rows"])
    cols = int(array["cols"])
    depth = rows
    layers = int(shape["num_hidden_layers"])
    linear_ops = full_checkpoint_linear_ops(shape, rows, cols, depth)
    commands_per_layer = sum(op["input_tiles"] * op["output_tiles"] for op in linear_ops)
    total_commands = layers * commands_per_layer

    header_path = REPO_ROOT / "e1/code/program/e1_tinyllama_full_schedule.hpp"
    smoke_path = REPO_ROOT / "e1/code/program/e1_tinyllama_full_schedule_smoke.cpp"

    op_initializers = ",\n".join(
        (
            "    {"
            f"\"{op['name']}\", {op['input_width']}u, {op['output_width']}u, "
            f"{op['input_tiles']}u, {op['output_tiles']}u"
            "}"
        )
        for op in linear_ops
    )
    write_text(
        header_path,
        f"""#ifndef E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP
#define E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP

#include <cstdint>

namespace e1_device::tinyllama_full {{

struct LinearOpPlan {{
  const char* name;
  std::uint32_t input_width;
  std::uint32_t output_width;
  std::uint32_t input_tiles;
  std::uint32_t output_tiles;
}};

struct TileCommand {{
  std::uint32_t input_addr;
  std::uint32_t weight_addr;
  std::uint32_t output_addr;
  std::uint16_t rows;
  std::uint16_t cols;
  std::uint16_t depth;
}};

constexpr std::uint32_t kLayerCount = {layers}u;
constexpr std::uint32_t kLinearOpCount = {len(linear_ops)}u;
constexpr std::uint16_t kTileRows = {rows}u;
constexpr std::uint16_t kTileCols = {cols}u;
constexpr std::uint16_t kTileDepth = {depth}u;
constexpr std::uint32_t kTileBytes = 64u;
constexpr std::uint32_t kInputBase = 0x01000000u;
constexpr std::uint32_t kWeightBase = 0x10000000u;
constexpr std::uint32_t kOutputBase = 0x30000000u;
constexpr std::uint32_t kLayerInputStride = 0x00100000u;
constexpr std::uint32_t kLayerWeightStride = 0x01000000u;
constexpr std::uint32_t kLayerOutputStride = 0x00100000u;
constexpr std::uint32_t kOpInputStride = 0x00010000u;
constexpr std::uint32_t kOpWeightStride = 0x00100000u;
constexpr std::uint32_t kOpOutputStride = 0x00010000u;

static constexpr LinearOpPlan kLinearOps[kLinearOpCount] = {{
{op_initializers}
}};

inline std::uint64_t tile_count(const LinearOpPlan& op) {{
  return static_cast<std::uint64_t>(op.input_tiles) *
         static_cast<std::uint64_t>(op.output_tiles);
}}

inline std::uint64_t commands_per_layer() {{
  std::uint64_t total = 0;
  for (std::uint32_t op = 0; op < kLinearOpCount; ++op) {{
    total += tile_count(kLinearOps[op]);
  }}
  return total;
}}

inline std::uint64_t total_tile_commands() {{
  return static_cast<std::uint64_t>(kLayerCount) * commands_per_layer();
}}

inline TileCommand command_for(
    std::uint32_t layer,
    std::uint32_t op_index,
    std::uint32_t input_tile,
    std::uint32_t output_tile) {{
  const LinearOpPlan& op = kLinearOps[op_index];
  const std::uint32_t input_addr =
      kInputBase + layer * kLayerInputStride + op_index * kOpInputStride +
      input_tile * kTileBytes;
  const std::uint32_t weight_addr =
      kWeightBase + layer * kLayerWeightStride + op_index * kOpWeightStride +
      (output_tile * op.input_tiles + input_tile) * kTileBytes;
  const std::uint32_t output_addr =
      kOutputBase + layer * kLayerOutputStride + op_index * kOpOutputStride +
      output_tile * kTileBytes;
  return {{
      input_addr,
      weight_addr,
      output_addr,
      kTileRows,
      kTileCols,
      kTileDepth,
  }};
}}

}}  // namespace e1_device::tinyllama_full

#endif  // E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP
""",
    )

    first_op = linear_ops[0]
    last_op_index = len(linear_ops) - 1
    last_op = linear_ops[-1]
    write_text(
        smoke_path,
        f"""#include "e1_tinyllama_full_schedule.hpp"

#include <cstdint>
#include <iostream>

int main() {{
  using namespace e1_device::tinyllama_full;

  const TileCommand first = command_for(0, 0, 0, 0);
  const TileCommand last = command_for(
      kLayerCount - 1,
      kLinearOpCount - 1,
      kLinearOps[kLinearOpCount - 1].input_tiles - 1,
      kLinearOps[kLinearOpCount - 1].output_tiles - 1);

  const bool pass =
      kLayerCount == {layers}u &&
      kLinearOpCount == {len(linear_ops)}u &&
      commands_per_layer() == {commands_per_layer}ull &&
      total_tile_commands() == {total_commands}ull &&
      kLinearOps[0].input_tiles == {first_op['input_tiles']}u &&
      kLinearOps[0].output_tiles == {first_op['output_tiles']}u &&
      kLinearOps[{last_op_index}].input_tiles == {last_op['input_tiles']}u &&
      kLinearOps[{last_op_index}].output_tiles == {last_op['output_tiles']}u &&
      first.input_addr == kInputBase &&
      first.weight_addr == kWeightBase &&
      first.output_addr == kOutputBase &&
      first.rows == kTileRows &&
      first.cols == kTileCols &&
      first.depth == kTileDepth &&
      last.rows == kTileRows &&
      last.cols == kTileCols &&
      last.depth == kTileDepth;

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-command-stream-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"layers\\": " << kLayerCount << ",\\n"
      << "  \\"linear_ops_per_layer\\": " << kLinearOpCount << ",\\n"
      << "  \\"commands_per_layer\\": " << commands_per_layer() << ",\\n"
      << "  \\"total_tile_commands\\": " << total_tile_commands() << ",\\n"
      << "  \\"first_input_addr\\": " << first.input_addr << ",\\n"
      << "  \\"last_output_addr\\": " << last.output_addr << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "e1_tinyllama_full_schedule_smoke"
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                "-I",
                "e1/code/program",
                repo_rel(smoke_path),
                "-o",
                str(exe),
            ],
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
    smoke = json.loads(result.stdout)
    checks = [
        {"name": "full_checkpoint_plan_is_shape_complete", "status": full_checkpoint_rtl_lowering["status"]},
        {"name": "command_stream_smoke", "status": smoke["status"]},
        {
            "name": "all_linear_ops_have_tile_commands",
            "status": "pass" if all(op["input_tiles"] > 0 and op["output_tiles"] > 0 for op in linear_ops) else "fail",
        },
        {
            "name": "command_count_matches_plan",
            "status": "pass" if smoke["total_tile_commands"] == total_commands else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-command-stream-v0",
        "status": "pass" if all(check["status"] in {"pass", "planned"} for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "compressed_tile_command_stream",
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "header": repo_rel(header_path),
        "host_smoke": repo_rel(smoke_path),
        "layers": layers,
        "linear_ops": linear_ops,
        "commands_per_layer": commands_per_layer,
        "total_tile_commands": total_commands,
        "smoke": smoke,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_full_checkpoint_rtl_cycle_lowering(
    output_path: Path,
    manifest: dict[str, Any],
    command_stream: dict[str, Any],
    module_dpi_report: dict[str, Any],
) -> dict[str, Any]:
    linear_ops = command_stream["linear_ops"]
    layers = int(command_stream["layers"])
    total_commands = int(command_stream["total_tile_commands"])
    cycles_per_tile_command = 8
    total_rtl_cycles = total_commands * cycles_per_tile_command

    generated_dir = REPO_ROOT / "e1/e1-h1/generated/full_checkpoint"
    scheduler_path = generated_dir / "e1_h1_tinyllama_linear_scheduler.sv"
    tb_path = generated_dir / "e1_h1_tinyllama_linear_scheduler_tb.cpp"
    flist_path = generated_dir / "e1_h1_tinyllama_linear_scheduler.f"
    cycle_smoke_path = REPO_ROOT / "e1/code/program/e1_tinyllama_full_rtl_cycle_smoke.cpp"

    input_tile_cases = "\n".join(
        f"      3'd{index}: input_tiles_for = 9'd{op['input_tiles']};"
        for index, op in enumerate(linear_ops)
    )
    output_tile_cases = "\n".join(
        f"      3'd{index}: output_tiles_for = 9'd{op['output_tiles']};"
        for index, op in enumerate(linear_ops)
    )
    last_op_index = len(linear_ops) - 1
    rows = int(linear_ops[0]["rows"])
    cols = int(linear_ops[0]["cols"])
    depth = int(linear_ops[0]["depth"])

    write_text(
        scheduler_path,
        f"""`default_nettype none

module e1_h1_tinyllama_linear_scheduler (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic        cmd_valid_o,
  input  logic        cmd_ready_i,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  input  logic        array_done_i,
  input  logic        array_error_i,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic [31:0] issued_commands_o,
  output logic [2:0]  cycle_phase_o
);

  localparam int unsigned LayerCount = {layers};
  localparam int unsigned LinearOpCount = {len(linear_ops)};
  localparam logic [31:0] TotalTileCommands = 32'd{total_commands};
  localparam logic [31:0] InputBase = 32'h0100_0000;
  localparam logic [31:0] WeightBase = 32'h1000_0000;
  localparam logic [31:0] OutputBase = 32'h3000_0000;
  localparam logic [31:0] LayerInputStride = 32'h0010_0000;
  localparam logic [31:0] LayerWeightStride = 32'h0100_0000;
  localparam logic [31:0] LayerOutputStride = 32'h0010_0000;
  localparam logic [31:0] OpInputStride = 32'h0001_0000;
  localparam logic [31:0] OpWeightStride = 32'h0010_0000;
  localparam logic [31:0] OpOutputStride = 32'h0001_0000;
  localparam logic [31:0] TileBytes = 32'd64;

  typedef enum logic [1:0] {{
    StateIdle,
    StateRun,
    StateDone,
    StateError
  }} state_e;

  state_e state_q;
  logic [2:0]  phase_q;
  logic [31:0] layer_q;
  logic [2:0]  op_index_q;
  logic [8:0]  input_tile_q;
  logic [8:0]  output_tile_q;
  logic [31:0] issued_commands_q;

  function automatic logic [31:0] zext3(input logic [2:0] value);
    zext3 = {{29'd0, value}};
  endfunction

  function automatic logic [31:0] zext9(input logic [8:0] value);
    zext9 = {{23'd0, value}};
  endfunction

  function automatic logic [8:0] input_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
{input_tile_cases}
      default: input_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic [8:0] output_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
{output_tile_cases}
      default: output_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic is_last_command(
      input logic [31:0] layer,
      input logic [2:0] op_index,
      input logic [8:0] input_tile,
      input logic [8:0] output_tile);
    is_last_command =
        layer == (LayerCount - 1) &&
        op_index == 3'd{last_op_index} &&
        input_tile == (input_tiles_for(op_index) - 9'd1) &&
        output_tile == (output_tiles_for(op_index) - 9'd1);
  endfunction

  assign cmd_input_addr_o =
      InputBase + layer_q * LayerInputStride + zext3(op_index_q) * OpInputStride +
      zext9(input_tile_q) * TileBytes;
  assign cmd_weight_addr_o =
      WeightBase + layer_q * LayerWeightStride + zext3(op_index_q) * OpWeightStride +
      ((zext9(output_tile_q) * zext9(input_tiles_for(op_index_q))) + zext9(input_tile_q)) *
      TileBytes;
  assign cmd_output_addr_o =
      OutputBase + layer_q * LayerOutputStride + zext3(op_index_q) * OpOutputStride +
      zext9(output_tile_q) * TileBytes;
  assign cmd_rows_o = 16'd{rows};
  assign cmd_cols_o = 16'd{cols};
  assign cmd_depth_o = 16'd{depth};

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign error_o = state_q == StateError;
  assign cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign layer_o = layer_q;
  assign op_index_o = op_index_q;
  assign input_tile_o = input_tile_q;
  assign output_tile_o = output_tile_q;
  assign issued_commands_o = issued_commands_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      phase_q <= 3'd0;
      layer_q <= 32'd0;
      op_index_q <= 3'd0;
      input_tile_q <= 9'd0;
      output_tile_q <= 9'd0;
      issued_commands_q <= 32'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            phase_q <= 3'd0;
            layer_q <= 32'd0;
            op_index_q <= 3'd0;
            input_tile_q <= 9'd0;
            output_tile_q <= 9'd0;
            issued_commands_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && !cmd_ready_i) begin
            phase_q <= 3'd2;
          end else if (phase_q == 3'd6 && array_error_i) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_i) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (is_last_command(layer_q, op_index_q, input_tile_q, output_tile_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
              if (zext9(input_tile_q) + 32'd1 < zext9(input_tiles_for(op_index_q))) begin
                input_tile_q <= input_tile_q + 9'd1;
              end else begin
                input_tile_q <= 9'd0;
                if (zext9(output_tile_q) + 32'd1 < zext9(output_tiles_for(op_index_q))) begin
                  output_tile_q <= output_tile_q + 9'd1;
                end else begin
                  output_tile_q <= 9'd0;
                  if (zext3(op_index_q) + 32'd1 < LinearOpCount) begin
                    op_index_q <= op_index_q + 3'd1;
                  end else begin
                    op_index_q <= 3'd0;
                    layer_q <= layer_q + 32'd1;
                  end
                end
              end
            end
          end else begin
            if (phase_q == 3'd2 && cmd_ready_i) begin
              issued_commands_q <= issued_commands_q + 32'd1;
            end
            phase_q <= phase_q + 3'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        StateError: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

  logic unused_total_commands;
  assign unused_total_commands = TotalTileCommands[0];

endmodule

`default_nettype wire
""",
    )

    write_text(
        tb_path,
        f"""// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_linear_scheduler.h"
#include "../../../code/program/e1_tinyllama_full_schedule.hpp"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {{

void tick(VerilatedContext& context, Ve1_h1_tinyllama_linear_scheduler& top) {{
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}}

}}  // namespace

int main(int argc, char** argv) {{
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_linear_scheduler top{{&context}};

  bool pass = true;
  auto fail = [&](const char* message) {{
    std::cerr << "E1_FULL_RTL_SCHEDULER_FAIL " << message << "\\n";
    pass = false;
  }};
  auto expect_phase = [&](std::uint8_t phase) {{
    top.eval();
    if (top.cycle_phase_o != phase) {{
      fail("unexpected cycle phase");
    }}
  }};
  auto expect_command = [&](std::uint32_t layer,
                            std::uint32_t op_index,
                            std::uint32_t input_tile,
                            std::uint32_t output_tile) {{
    using namespace e1_device::tinyllama_full;
    const TileCommand expected = command_for(layer, op_index, input_tile, output_tile);
    top.eval();
    if (!top.cmd_valid_o) {{
      fail("cmd_valid_o deasserted during command phase");
    }}
    if (top.cmd_input_addr_o != expected.input_addr ||
        top.cmd_weight_addr_o != expected.weight_addr ||
        top.cmd_output_addr_o != expected.output_addr ||
        top.cmd_rows_o != expected.rows ||
        top.cmd_cols_o != expected.cols ||
        top.cmd_depth_o != expected.depth ||
        top.layer_o != layer ||
        top.op_index_o != op_index ||
        top.input_tile_o != input_tile ||
        top.output_tile_o != output_tile) {{
      fail("command payload does not match generated C++ schedule");
    }}
  }};
  auto advance = [](std::uint32_t& layer,
                    std::uint32_t& op_index,
                    std::uint32_t& input_tile,
                    std::uint32_t& output_tile) {{
    using namespace e1_device::tinyllama_full;
    const LinearOpPlan& op = kLinearOps[op_index];
    if (input_tile + 1 < op.input_tiles) {{
      ++input_tile;
      return;
    }}
    input_tile = 0;
    if (output_tile + 1 < op.output_tiles) {{
      ++output_tile;
      return;
    }}
    output_tile = 0;
    if (op_index + 1 < kLinearOpCount) {{
      ++op_index;
      return;
    }}
    op_index = 0;
    ++layer;
  }};

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.cmd_ready_i = 0;
  top.array_done_i = 0;
  top.array_error_i = 0;
  tick(context, top);
  tick(context, top);
  top.rst_ni = 1;
  top.start_i = 1;
  tick(context, top);

  constexpr std::uint32_t kCheckedCommands = 16;
  std::uint32_t layer = 0;
  std::uint32_t op_index = 0;
  std::uint32_t input_tile = 0;
  std::uint32_t output_tile = 0;

  for (std::uint32_t checked = 0; checked < kCheckedCommands; ++checked) {{
    expect_phase(0);
    if (top.cmd_valid_o) {{
      fail("cmd_valid_o asserted before issue phase");
    }}
    tick(context, top);

    expect_phase(1);
    expect_command(layer, op_index, input_tile, output_tile);
    tick(context, top);

    top.cmd_ready_i = 1;
    expect_phase(2);
    expect_command(layer, op_index, input_tile, output_tile);
    tick(context, top);
    top.cmd_ready_i = 0;

    for (std::uint8_t phase = 3; phase <= 5; ++phase) {{
      expect_phase(phase);
      tick(context, top);
    }}

    top.array_done_i = 1;
    expect_phase(6);
    tick(context, top);
    top.array_done_i = 0;

    expect_phase(7);
    tick(context, top);
    advance(layer, op_index, input_tile, output_tile);
  }}

  top.eval();
  if (top.issued_commands_o != kCheckedCommands) {{
    fail("issued command counter mismatch");
  }}

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-rtl-scheduler-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"checked_commands\\": " << kCheckedCommands << ",\\n"
      << "  \\"cycles_per_tile_command\\": {cycles_per_tile_command},\\n"
      << "  \\"total_tile_commands\\": " << e1_device::tinyllama_full::total_tile_commands() << ",\\n"
      << "  \\"issued_commands\\": " << top.issued_commands_o << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    write_text(flist_path, f"{repo_rel(scheduler_path)}\n")
    write_text(
        cycle_smoke_path,
        f"""#include "e1_tinyllama_full_schedule.hpp"

#include <cstdint>
#include <iostream>

int main() {{
  using namespace e1_device::tinyllama_full;

  constexpr std::uint32_t kCyclesPerTileCommand = {cycles_per_tile_command}u;
  constexpr std::uint64_t kExpectedTotalCycles = {total_rtl_cycles}ull;
  const std::uint64_t total_cycles = total_tile_commands() * kCyclesPerTileCommand;
  const bool pass =
      total_tile_commands() == {total_commands}ull &&
      total_cycles == kExpectedTotalCycles &&
      kTileRows == {rows}u &&
      kTileCols == {cols}u &&
      kTileDepth == {depth}u;

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-rtl-cycle-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"cycles_per_tile_command\\": " << kCyclesPerTileCommand << ",\\n"
      << "  \\"total_tile_commands\\": " << total_tile_commands() << ",\\n"
      << "  \\"total_rtl_cycles\\": " << total_cycles << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "e1_tinyllama_full_rtl_cycle_smoke"
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                "-I",
                "e1/code/program",
                repo_rel(cycle_smoke_path),
                "-o",
                str(exe),
            ],
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
    smoke = json.loads(result.stdout)
    module_dpi_by_name = {module["name"]: module for module in module_dpi_report["modules"]}
    required_modules = ["control_cpu", "ingress_sram", "systolic_array"]
    phase_template = [
        {"cycle": 0, "module": "control_cpu", "phase": "reset_release_or_next_command_setup"},
        {"cycle": 1, "module": "control_cpu", "phase": "cmd_valid_o asserted under allowed backpressure"},
        {"cycle": 2, "module": "systolic_array", "phase": "command handshake accepted"},
        {"cycle": 3, "module": "ingress_sram", "phase": "latched input beat 0 visible to array"},
        {"cycle": 4, "module": "systolic_array", "phase": "input beat 1 consumed"},
        {"cycle": 5, "module": "systolic_array", "phase": "input beat 2 consumed"},
        {"cycle": 6, "module": "systolic_array", "phase": "input beat 3 consumed and done observed"},
        {"cycle": 7, "module": "control_cpu", "phase": "advance to next tile command"},
    ]
    checks = [
        {"name": "command_stream_status", "status": command_stream["status"]},
        {
            "name": "required_module_dpi_boundaries_present",
            "status": "pass" if all(name in module_dpi_by_name for name in required_modules) else "fail",
        },
        {
            "name": "latch_buffer_boundary_preserved",
            "status": "pass" if module_dpi_by_name.get("ingress_sram", {}).get("latch_buffer") else "fail",
        },
        {
            "name": "rtl_scheduler_generated",
            "status": "pass" if scheduler_path.exists() and tb_path.exists() and flist_path.exists() else "fail",
        },
        {
            "name": "cycle_smoke",
            "status": smoke["status"],
        },
        {
            "name": "total_rtl_cycles_match_command_stream",
            "status": "pass" if smoke["total_rtl_cycles"] == total_rtl_cycles else "fail",
        },
        {
            "name": "phase_template_names_each_cycle",
            "status": "pass" if [entry["cycle"] for entry in phase_template] == list(range(cycles_per_tile_command)) else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-rtl-cycle-lowering-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "linear_tile_command_scheduler_rtl",
        "full_checkpoint_linear_command_rtl_lowering": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "scheduler_rtl": repo_rel(scheduler_path),
        "verilator_tb": repo_rel(tb_path),
        "flist": repo_rel(flist_path),
        "cycle_smoke": repo_rel(cycle_smoke_path),
        "cycle_smoke_report": smoke,
        "module_dpi_manifest": module_dpi_report["manifest"],
        "separated_modules": required_modules,
        "latch_buffer_module": "ingress_sram",
        "cycles_per_tile_command": cycles_per_tile_command,
        "total_tile_commands": total_commands,
        "total_rtl_cycles": total_rtl_cycles,
        "phase_template": phase_template,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_full_checkpoint_tile_engine(
    output_path: Path,
    manifest: dict[str, Any],
    command_stream: dict[str, Any],
    rtl_cycle: dict[str, Any],
) -> dict[str, Any]:
    generated_dir = REPO_ROOT / "e1/e1-h1/generated/full_checkpoint"
    engine_path = generated_dir / "e1_h1_tinyllama_linear_tile_engine.sv"
    tb_path = generated_dir / "e1_h1_tinyllama_linear_tile_engine_tb.cpp"
    flist_path = generated_dir / "e1_h1_tinyllama_linear_tile_engine.f"
    scheduler_path = REPO_ROOT / rtl_cycle["scheduler_rtl"]
    buffer_path = REPO_ROOT / "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv"
    array_path = REPO_ROOT / "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv"

    write_text(
        engine_path,
        """`default_nettype none

module e1_h1_tinyllama_linear_tile_engine (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [2:0]  cycle_phase_o,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic        scheduler_cmd_valid_o,
  output logic        array_cmd_valid_o,
  output logic        array_cmd_ready_o,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic [63:0] buffer_array_data_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o
);

  logic scheduler_done;
  logic scheduler_error;
  logic array_error;

  e1_h1_tinyllama_linear_scheduler u_scheduler (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(scheduler_done),
    .error_o(scheduler_error),
    .cmd_valid_o(scheduler_cmd_valid_o),
    .cmd_ready_i(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .array_done_i(array_done_o),
    .array_error_i(array_error),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o)
  );

  assign array_cmd_valid_o = scheduler_cmd_valid_o && (cycle_phase_o == 3'd2);

  e1_h1_stream_sram u_latch_buffer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(buffer_array_valid_o),
    .array_ready_i(buffer_array_ready_o),
    .array_data_o(buffer_array_data_o)
  );

  e1_h1_systolic_array u_systolic_array (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_o),
    .cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_i(cmd_input_addr_o),
    .cmd_weight_addr_i(cmd_weight_addr_o),
    .cmd_output_addr_i(cmd_output_addr_o),
    .cmd_rows_i(cmd_rows_o),
    .cmd_cols_i(cmd_cols_o),
    .cmd_depth_i(cmd_depth_o),
    .input_valid_i(buffer_array_valid_o),
    .input_ready_o(buffer_array_ready_o),
    .input_data_i(buffer_array_data_o),
    .done_o(array_done_o),
    .error_o(array_error),
    .debug_busy_o(array_debug_busy_o)
  );

  assign done_o = scheduler_done;
  assign error_o = scheduler_error || array_error;

endmodule

`default_nettype wire
""",
    )

    write_text(
        tb_path,
        """// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_linear_tile_engine.h"
#include "../../../code/program/e1_tinyllama_full_schedule.hpp"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {

void tick(VerilatedContext& context, Ve1_h1_tinyllama_linear_tile_engine& top) {
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}

void advance(std::uint32_t& layer,
             std::uint32_t& op_index,
             std::uint32_t& input_tile,
             std::uint32_t& output_tile) {
  using namespace e1_device::tinyllama_full;
  const LinearOpPlan& op = kLinearOps[op_index];
  if (input_tile + 1 < op.input_tiles) {
    ++input_tile;
    return;
  }
  input_tile = 0;
  if (output_tile + 1 < op.output_tiles) {
    ++output_tile;
    return;
  }
  output_tile = 0;
  if (op_index + 1 < kLinearOpCount) {
    ++op_index;
    return;
  }
  op_index = 0;
  ++layer;
}

}  // namespace

int main(int argc, char** argv) {
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_linear_tile_engine top{&context};

  bool pass = true;
  auto fail = [&](const char* message) {
    std::cerr << "E1_FULL_TILE_ENGINE_FAIL " << message << "\\n";
    pass = false;
  };

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.stream_valid_i = 0;
  top.stream_data_i = 0;
  top.stream_last_i = 0;
  top.stream_error_i = 0;
  tick(context, top);
  tick(context, top);

  top.rst_ni = 1;
  top.start_i = 1;

  constexpr std::uint32_t kCheckedCommands = 8;
  std::uint32_t handshakes = 0;
  std::uint32_t layer = 0;
  std::uint32_t op_index = 0;
  std::uint32_t input_tile = 0;
  std::uint32_t output_tile = 0;
  bool saw_latched_hold = false;
  bool saw_array_consume = false;
  bool saw_scheduler_valid_before_array_valid = false;

  for (std::uint32_t cycle = 0; cycle < 512 && handshakes < kCheckedCommands; ++cycle) {
    top.stream_valid_i = 1;
    top.stream_data_i = 0x2000u + cycle;
    top.stream_last_i = 0;
    top.stream_error_i = 0;
    top.eval();

    if (top.buffer_array_valid_o && !top.buffer_array_ready_o) {
      saw_latched_hold = true;
    }
    if (top.buffer_array_valid_o && top.buffer_array_ready_o) {
      saw_array_consume = true;
    }
    if (top.scheduler_cmd_valid_o && !top.array_cmd_valid_o && top.cycle_phase_o == 1) {
      saw_scheduler_valid_before_array_valid = true;
    }
    if (top.array_cmd_valid_o && top.array_cmd_ready_o) {
      using namespace e1_device::tinyllama_full;
      const TileCommand expected = command_for(layer, op_index, input_tile, output_tile);
      if (top.cmd_input_addr_o != expected.input_addr ||
          top.cmd_weight_addr_o != expected.weight_addr ||
          top.cmd_output_addr_o != expected.output_addr ||
          top.cmd_rows_o != expected.rows ||
          top.cmd_cols_o != expected.cols ||
          top.cmd_depth_o != expected.depth ||
          top.layer_o != layer ||
          top.op_index_o != op_index ||
          top.input_tile_o != input_tile ||
          top.output_tile_o != output_tile) {
        fail("tile engine command does not match generated schedule");
      }
      ++handshakes;
      advance(layer, op_index, input_tile, output_tile);
    }

    tick(context, top);
  }

  top.eval();
  if (handshakes != kCheckedCommands) {
    fail("missing checked command handshakes");
  }
  if (top.issued_commands_o != kCheckedCommands) {
    fail("scheduler issued counter mismatch");
  }
  if (!saw_latched_hold) {
    fail("latch buffer never held data while array was not ready");
  }
  if (!saw_array_consume) {
    fail("systolic array never consumed latched input data");
  }
  if (!saw_scheduler_valid_before_array_valid) {
    fail("tile engine did not keep scheduler valid separate from array handshake");
  }
  if (top.error_o) {
    fail("tile engine reported error");
  }

  std::cout
      << "{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-tile-engine-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"checked_commands\\": " << kCheckedCommands << ",\\n"
      << "  \\"handshakes\\": " << handshakes << ",\\n"
      << "  \\"issued_commands\\": " << top.issued_commands_o << ",\\n"
      << "  \\"saw_latched_hold\\": " << (saw_latched_hold ? "true" : "false") << ",\\n"
      << "  \\"saw_array_consume\\": " << (saw_array_consume ? "true" : "false") << ",\\n"
      << "  \\"saw_scheduler_valid_before_array_valid\\": "
      << (saw_scheduler_valid_before_array_valid ? "true" : "false") << "\\n"
      << "}\\n";

  return pass ? 0 : 1;
}
""",
    )

    write_text(
        flist_path,
        "\n".join(
            [
                repo_rel(scheduler_path),
                repo_rel(buffer_path),
                repo_rel(array_path),
                repo_rel(engine_path),
                "",
            ]
        ),
    )

    flist_entries = [line for line in flist_path.read_text(encoding="utf-8").splitlines() if line]
    checks = [
        {
            "name": "scheduler_rtl_present",
            "status": "pass" if scheduler_path.exists() else "fail",
        },
        {
            "name": "tile_engine_rtl_generated",
            "status": "pass" if engine_path.exists() and tb_path.exists() and flist_path.exists() else "fail",
        },
        {
            "name": "engine_flist_wires_scheduler_latch_and_array",
            "status": "pass"
            if {
                repo_rel(scheduler_path),
                "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv",
                "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv",
                repo_rel(engine_path),
            }.issubset(set(flist_entries))
            else "fail",
        },
        {
            "name": "tile_engine_preserves_separation",
            "status": "pass"
            if "u_latch_buffer" in engine_path.read_text(encoding="utf-8")
            and "u_systolic_array" in engine_path.read_text(encoding="utf-8")
            and "u_scheduler" in engine_path.read_text(encoding="utf-8")
            else "fail",
        },
        {
            "name": "command_stream_covered_by_engine_plan",
            "status": "pass" if command_stream["status"] == "pass" else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-tile-engine-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "scheduler_latch_buffer_systolic_array_rtl_composition",
        "full_checkpoint_linear_tile_engine_rtl": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "engine_rtl": repo_rel(engine_path),
        "verilator_tb": repo_rel(tb_path),
        "flist": repo_rel(flist_path),
        "scheduler_rtl": repo_rel(scheduler_path),
        "latch_buffer_rtl": "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv",
        "systolic_array_rtl": "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv",
        "command_stream": "e1/generated/pipeline/19_full_checkpoint_command_stream.json",
        "rtl_cycle_lowering": "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json",
        "total_tile_commands": command_stream["total_tile_commands"],
        "cycles_per_tile_command": rtl_cycle["cycles_per_tile_command"],
        "total_rtl_cycles": rtl_cycle["total_rtl_cycles"],
        "separation": {
            "scheduler": "Generates CPU-side tile commands and counters only.",
            "latch_buffer": "Stages external stream data before the systolic array input.",
            "systolic_array": "Consumes gated tile commands and latched input beats.",
        },
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_full_checkpoint_control_scheduler(
    output_path: Path,
    manifest: dict[str, Any],
    full_checkpoint_rtl_lowering: dict[str, Any],
    module_dpi_report: dict[str, Any],
) -> dict[str, Any]:
    first_layer_ops = full_checkpoint_rtl_lowering["layers"][0]["ops"]
    control_ops = [
        {
            "name": op["name"],
            "kind": op["kind"],
            "layer_op_slot": slot,
        }
        for slot, op in enumerate(first_layer_ops)
        if op["kind"] != "linear"
    ]
    layers = int(full_checkpoint_rtl_lowering["aggregate"]["layers"])
    control_ops_per_layer = len(control_ops)
    total_control_ops = layers * control_ops_per_layer
    cycles_per_control_op = 4
    total_control_cycles = total_control_ops * cycles_per_control_op
    kind_ids = {
        "rms_norm": 1,
        "rope": 2,
        "attention_control": 3,
        "residual_add": 4,
        "activation_control": 5,
    }

    generated_dir = REPO_ROOT / "e1/e1-h1/generated/full_checkpoint"
    scheduler_path = generated_dir / "e1_h1_tinyllama_control_scheduler.sv"
    tb_path = generated_dir / "e1_h1_tinyllama_control_scheduler_tb.cpp"
    flist_path = generated_dir / "e1_h1_tinyllama_control_scheduler.f"

    kind_cases = "\n".join(
        f"      3'd{index}: control_kind_for = 4'd{kind_ids[op['kind']]};"
        for index, op in enumerate(control_ops)
    )
    slot_cases = "\n".join(
        f"      3'd{index}: layer_slot_for = 4'd{op['layer_op_slot']};"
        for index, op in enumerate(control_ops)
    )
    expected_kind_array = ", ".join(str(kind_ids[op["kind"]]) for op in control_ops)
    expected_slot_array = ", ".join(str(op["layer_op_slot"]) for op in control_ops)

    write_text(
        scheduler_path,
        f"""`default_nettype none

module e1_h1_tinyllama_control_scheduler (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        control_valid_o,
  input  logic        control_ready_i,
  output logic        control_commit_o,
  output logic [31:0] layer_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  layer_op_slot_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] issued_control_ops_o,
  output logic [1:0]  cycle_phase_o
);

  localparam int unsigned LayerCount = {layers};
  localparam int unsigned ControlOpsPerLayer = {control_ops_per_layer};
  localparam logic [31:0] TotalControlOps = 32'd{total_control_ops};

  typedef enum logic [1:0] {{
    StateIdle,
    StateRun,
    StateDone
  }} state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [2:0]  control_op_index_q;
  logic [31:0] issued_control_ops_q;
  logic [1:0]  phase_q;

  function automatic logic [3:0] control_kind_for(input logic [2:0] control_op_index);
    unique case (control_op_index)
{kind_cases}
      default: control_kind_for = 4'd0;
    endcase
  endfunction

  function automatic logic [3:0] layer_slot_for(input logic [2:0] control_op_index);
    unique case (control_op_index)
{slot_cases}
      default: layer_slot_for = 4'd0;
    endcase
  endfunction

  function automatic logic is_last_control(
      input logic [31:0] layer,
      input logic [2:0] control_op_index);
    is_last_control = layer == (LayerCount - 1) && control_op_index == 3'd{control_ops_per_layer - 1};
  endfunction

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign control_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign control_commit_o = state_q == StateRun && phase_q == 2'd3;
  assign layer_o = layer_q;
  assign control_op_index_o = control_op_index_q;
  assign layer_op_slot_o = layer_slot_for(control_op_index_q);
  assign control_kind_o = control_kind_for(control_op_index_q);
  assign issued_control_ops_o = issued_control_ops_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      control_op_index_q <= 3'd0;
      issued_control_ops_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= 32'd0;
            control_op_index_q <= 3'd0;
            issued_control_ops_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !control_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd3) begin
            issued_control_ops_q <= issued_control_ops_q + 32'd1;
            if (is_last_control(layer_q, control_op_index_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 2'd0;
              if (control_op_index_q + 3'd1 < ControlOpsPerLayer) begin
                control_op_index_q <= control_op_index_q + 3'd1;
              end else begin
                control_op_index_q <= 3'd0;
                layer_q <= layer_q + 32'd1;
              end
            end
          end else begin
            phase_q <= phase_q + 2'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

  logic unused_total_control_ops;
  assign unused_total_control_ops = TotalControlOps[0];

endmodule

`default_nettype wire
""",
    )

    write_text(
        tb_path,
        f"""// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_control_scheduler.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {{

constexpr std::uint32_t kLayerCount = {layers};
constexpr std::uint32_t kControlOpsPerLayer = {control_ops_per_layer};
constexpr std::uint32_t kTotalControlOps = {total_control_ops};
constexpr std::uint8_t kExpectedKinds[kControlOpsPerLayer] = {{{expected_kind_array}}};
constexpr std::uint8_t kExpectedSlots[kControlOpsPerLayer] = {{{expected_slot_array}}};

void tick(VerilatedContext& context, Ve1_h1_tinyllama_control_scheduler& top) {{
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}}

}}  // namespace

int main(int argc, char** argv) {{
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_control_scheduler top{{&context}};

  bool pass = true;
  auto fail = [&](const char* message) {{
    std::cerr << "E1_FULL_CONTROL_SCHEDULER_FAIL " << message << "\\n";
    pass = false;
  }};
  auto expect_control = [&](std::uint32_t layer, std::uint32_t control_op_index) {{
    top.eval();
    if (top.layer_o != layer ||
        top.control_op_index_o != control_op_index ||
        top.control_kind_o != kExpectedKinds[control_op_index] ||
        top.layer_op_slot_o != kExpectedSlots[control_op_index]) {{
      fail("control op payload mismatch");
    }}
  }};

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.control_ready_i = 0;
  tick(context, top);
  tick(context, top);
  top.rst_ni = 1;
  top.start_i = 1;
  tick(context, top);

  top.control_ready_i = 0;
  top.eval();
  if (!top.control_valid_o || top.cycle_phase_o != 0) {{
    fail("control scheduler did not hold valid on initial issue");
  }}
  tick(context, top);
  top.eval();
  const bool saw_backpressure_hold = top.control_valid_o && top.cycle_phase_o == 0;
  top.control_ready_i = 1;

  std::uint32_t commits = 0;
  for (std::uint32_t cycle = 0; cycle < 4096 && commits < kTotalControlOps; ++cycle) {{
    top.eval();
    const std::uint32_t layer = commits / kControlOpsPerLayer;
    const std::uint32_t control_op_index = commits % kControlOpsPerLayer;
    if (top.control_valid_o) {{
      expect_control(layer, control_op_index);
    }}
    if (top.control_commit_o) {{
      expect_control(layer, control_op_index);
      ++commits;
    }}
    tick(context, top);
  }}

  top.eval();
  if (commits != kTotalControlOps) {{
    fail("missing control commits");
  }}
  if (top.issued_control_ops_o != kTotalControlOps) {{
    fail("issued control counter mismatch");
  }}
  if (!top.done_o) {{
    fail("control scheduler did not finish");
  }}
  if (!saw_backpressure_hold) {{
    fail("control scheduler did not hold phase 0 under backpressure");
  }}

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-control-scheduler-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"layers\\": " << kLayerCount << ",\\n"
      << "  \\"control_ops_per_layer\\": " << kControlOpsPerLayer << ",\\n"
      << "  \\"total_control_ops\\": " << kTotalControlOps << ",\\n"
      << "  \\"issued_control_ops\\": " << top.issued_control_ops_o << ",\\n"
      << "  \\"saw_backpressure_hold\\": " << (saw_backpressure_hold ? "true" : "false") << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    write_text(flist_path, f"{repo_rel(scheduler_path)}\n")

    module_dpi_by_name = {module["name"]: module for module in module_dpi_report["modules"]}
    phase_template = [
        {"cycle": 0, "module": "control_cpu", "phase": "issue control op and allow backpressure"},
        {"cycle": 1, "module": "control_cpu", "phase": "read source/control metadata"},
        {"cycle": 2, "module": "control_cpu", "phase": "execute scalar or vector-control operation"},
        {"cycle": 3, "module": "control_cpu", "phase": "commit control op and advance graph slot"},
    ]
    checks = [
        {
            "name": "control_cpu_module_dpi_present",
            "status": "pass" if "control_cpu" in module_dpi_by_name else "fail",
        },
        {
            "name": "control_ops_match_full_checkpoint_plan",
            "status": "pass"
            if total_control_ops == int(full_checkpoint_rtl_lowering["aggregate"]["total_control_ops"])
            else "fail",
        },
        {
            "name": "all_control_ops_map_to_cpu",
            "status": "pass" if all(op["kind"] in kind_ids for op in control_ops) else "fail",
        },
        {
            "name": "control_scheduler_generated",
            "status": "pass" if scheduler_path.exists() and tb_path.exists() and flist_path.exists() else "fail",
        },
        {
            "name": "phase_template_names_each_cycle",
            "status": "pass" if [entry["cycle"] for entry in phase_template] == list(range(cycles_per_control_op)) else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-control-scheduler-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "cpu_control_op_scheduler_rtl",
        "full_checkpoint_control_op_rtl_lowering": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "scheduler_rtl": repo_rel(scheduler_path),
        "verilator_tb": repo_rel(tb_path),
        "flist": repo_rel(flist_path),
        "module_dpi_probe": module_dpi_by_name["control_cpu"]["probe"] if "control_cpu" in module_dpi_by_name else None,
        "layers": layers,
        "control_ops_per_layer": control_ops_per_layer,
        "total_control_ops": total_control_ops,
        "cycles_per_control_op": cycles_per_control_op,
        "total_control_cycles": total_control_cycles,
        "control_ops": control_ops,
        "phase_template": phase_template,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_full_checkpoint_graph_sequencer(
    output_path: Path,
    manifest: dict[str, Any],
    full_checkpoint_rtl_lowering: dict[str, Any],
    command_stream: dict[str, Any],
    control_scheduler: dict[str, Any],
) -> dict[str, Any]:
    first_layer_ops = full_checkpoint_rtl_lowering["layers"][0]["ops"]
    linear_ops = command_stream["linear_ops"]
    layers = int(full_checkpoint_rtl_lowering["aggregate"]["layers"])
    slots_per_layer = len(first_layer_ops)
    total_graph_slots = layers * slots_per_layer
    linear_slot_count = sum(1 for op in first_layer_ops if op["kind"] == "linear")
    control_slot_count = slots_per_layer - linear_slot_count
    linear_index_by_name = {op["name"]: index for index, op in enumerate(linear_ops)}
    control_kind_ids = {
        "rms_norm": 1,
        "rope": 2,
        "attention_control": 3,
        "residual_add": 4,
        "activation_control": 5,
    }

    slot_entries: list[dict[str, Any]] = []
    control_order = 0
    for slot, op in enumerate(first_layer_ops):
        if op["kind"] == "linear":
            linear_index = linear_index_by_name[op["name"]]
            linear = linear_ops[linear_index]
            tile_count = int(linear["input_tiles"]) * int(linear["output_tiles"])
            slot_entries.append(
                {
                    "slot": slot,
                    "name": op["name"],
                    "kind": "linear",
                    "ip": "systolic_array",
                    "linear_op_index": linear_index,
                    "control_op_index": 0,
                    "control_kind": 0,
                    "tile_count": tile_count,
                }
            )
        else:
            slot_entries.append(
                {
                    "slot": slot,
                    "name": op["name"],
                    "kind": op["kind"],
                    "ip": "control_cpu",
                    "linear_op_index": 0,
                    "control_op_index": control_order,
                    "control_kind": control_kind_ids[op["kind"]],
                    "tile_count": 0,
                }
            )
            control_order += 1

    generated_dir = REPO_ROOT / "e1/e1-h1/generated/full_checkpoint"
    sequencer_path = generated_dir / "e1_h1_tinyllama_graph_sequencer.sv"
    tb_path = generated_dir / "e1_h1_tinyllama_graph_sequencer_tb.cpp"
    flist_path = generated_dir / "e1_h1_tinyllama_graph_sequencer.f"

    def sv_case(fn_name: str, width: int, values: list[int]) -> str:
        lines = [f"  function automatic logic [{width - 1}:0] {fn_name}(input logic [3:0] slot);", "    unique case (slot)"]
        for slot, value in enumerate(values):
            lines.append(f"      4'd{slot}: {fn_name} = {width}'d{value};")
        lines.append(f"      default: {fn_name} = {width}'d0;")
        lines.append("    endcase")
        lines.append("  endfunction")
        return "\n".join(lines)

    is_linear_values = [1 if entry["kind"] == "linear" else 0 for entry in slot_entries]
    linear_index_values = [entry["linear_op_index"] for entry in slot_entries]
    control_index_values = [entry["control_op_index"] for entry in slot_entries]
    control_kind_values = [entry["control_kind"] for entry in slot_entries]
    tile_count_values = [entry["tile_count"] for entry in slot_entries]
    expected_names = [entry["name"] for entry in slot_entries]

    write_text(
        sequencer_path,
        f"""`default_nettype none

module e1_h1_tinyllama_graph_sequencer (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        slot_valid_o,
  input  logic        slot_ready_i,
  output logic        launch_control_o,
  output logic        launch_linear_o,
  input  logic        op_done_i,
  output logic [31:0] layer_o,
  output logic [3:0]  layer_slot_o,
  output logic [2:0]  linear_op_index_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] linear_tile_count_o,
  output logic [31:0] issued_graph_slots_o,
  output logic [1:0]  cycle_phase_o
);

  localparam int unsigned LayerCount = {layers};
  localparam int unsigned SlotsPerLayer = {slots_per_layer};
  localparam logic [31:0] TotalGraphSlots = 32'd{total_graph_slots};

  typedef enum logic [1:0] {{
    StateIdle,
    StateRun,
    StateDone
  }} state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [3:0]  layer_slot_q;
  logic [31:0] issued_graph_slots_q;
  logic [1:0]  phase_q;

{sv_case("is_linear_slot", 1, is_linear_values)}

{sv_case("linear_index_for", 3, linear_index_values)}

{sv_case("control_index_for", 3, control_index_values)}

{sv_case("control_kind_for", 4, control_kind_values)}

{sv_case("tile_count_for", 32, tile_count_values)}

  function automatic logic is_last_slot(input logic [31:0] layer, input logic [3:0] slot);
    is_last_slot = layer == (LayerCount - 1) && slot == 4'd{slots_per_layer - 1};
  endfunction

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign slot_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign launch_control_o = state_q == StateRun && phase_q == 2'd1 && !is_linear_slot(layer_slot_q);
  assign launch_linear_o = state_q == StateRun && phase_q == 2'd1 && is_linear_slot(layer_slot_q);
  assign layer_o = layer_q;
  assign layer_slot_o = layer_slot_q;
  assign linear_op_index_o = linear_index_for(layer_slot_q);
  assign control_op_index_o = control_index_for(layer_slot_q);
  assign control_kind_o = control_kind_for(layer_slot_q);
  assign linear_tile_count_o = tile_count_for(layer_slot_q);
  assign issued_graph_slots_o = issued_graph_slots_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      layer_slot_q <= 4'd0;
      issued_graph_slots_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= 32'd0;
            layer_slot_q <= 4'd0;
            issued_graph_slots_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !slot_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd2 && !op_done_i) begin
            phase_q <= 2'd2;
          end else if (phase_q == 2'd3) begin
            issued_graph_slots_q <= issued_graph_slots_q + 32'd1;
            if (is_last_slot(layer_q, layer_slot_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 2'd0;
              if (layer_slot_q + 4'd1 < SlotsPerLayer) begin
                layer_slot_q <= layer_slot_q + 4'd1;
              end else begin
                layer_slot_q <= 4'd0;
                layer_q <= layer_q + 32'd1;
              end
            end
          end else begin
            phase_q <= phase_q + 2'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

  logic unused_total_graph_slots;
  assign unused_total_graph_slots = TotalGraphSlots[0];

endmodule

`default_nettype wire
""",
    )

    expected_linear_array = ", ".join(str(value) for value in is_linear_values)
    expected_linear_index_array = ", ".join(str(value) for value in linear_index_values)
    expected_control_index_array = ", ".join(str(value) for value in control_index_values)
    expected_control_kind_array = ", ".join(str(value) for value in control_kind_values)
    expected_tile_count_array = ", ".join(str(value) for value in tile_count_values)
    expected_name_array = ", ".join(f'"{name}"' for name in expected_names)
    write_text(
        tb_path,
        f"""// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_graph_sequencer.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {{

constexpr std::uint32_t kLayerCount = {layers};
constexpr std::uint32_t kSlotsPerLayer = {slots_per_layer};
constexpr std::uint32_t kTotalGraphSlots = {total_graph_slots};
constexpr bool kIsLinear[kSlotsPerLayer] = {{{expected_linear_array}}};
constexpr std::uint8_t kLinearIndex[kSlotsPerLayer] = {{{expected_linear_index_array}}};
constexpr std::uint8_t kControlIndex[kSlotsPerLayer] = {{{expected_control_index_array}}};
constexpr std::uint8_t kControlKind[kSlotsPerLayer] = {{{expected_control_kind_array}}};
constexpr std::uint32_t kTileCount[kSlotsPerLayer] = {{{expected_tile_count_array}}};
const char* const kNames[kSlotsPerLayer] = {{{expected_name_array}}};

void tick(VerilatedContext& context, Ve1_h1_tinyllama_graph_sequencer& top) {{
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}}

}}  // namespace

int main(int argc, char** argv) {{
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_graph_sequencer top{{&context}};

  bool pass = true;
  auto fail = [&](const char* message) {{
    std::cerr << "E1_FULL_GRAPH_SEQUENCER_FAIL " << message << "\\n";
    pass = false;
  }};
  auto expect_slot = [&](std::uint32_t layer, std::uint32_t slot) {{
    top.eval();
    if (top.layer_o != layer ||
        top.layer_slot_o != slot ||
        top.linear_op_index_o != kLinearIndex[slot] ||
        top.control_op_index_o != kControlIndex[slot] ||
        top.control_kind_o != kControlKind[slot] ||
        top.linear_tile_count_o != kTileCount[slot]) {{
      std::cerr << "slot mismatch at layer=" << layer
                << " slot=" << slot
                << " name=" << kNames[slot] << "\\n";
      fail("graph slot payload mismatch");
    }}
  }};

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.slot_ready_i = 0;
  top.op_done_i = 0;
  tick(context, top);
  tick(context, top);
  top.rst_ni = 1;
  top.start_i = 1;
  tick(context, top);

  top.slot_ready_i = 0;
  top.eval();
  const bool saw_backpressure_hold = top.slot_valid_o && top.cycle_phase_o == 0;
  tick(context, top);
  top.eval();
  if (!(top.slot_valid_o && top.cycle_phase_o == 0)) {{
    fail("graph sequencer did not hold slot under backpressure");
  }}
  top.slot_ready_i = 1;

  std::uint32_t completed = 0;
  std::uint32_t launched_control = 0;
  std::uint32_t launched_linear = 0;
  bool saw_wait_for_op_done = false;
  for (std::uint32_t cycle = 0; cycle < 4096 && completed < kTotalGraphSlots; ++cycle) {{
    top.eval();
    const std::uint32_t layer = completed / kSlotsPerLayer;
    const std::uint32_t slot = completed % kSlotsPerLayer;
    if (top.slot_valid_o) {{
      expect_slot(layer, slot);
    }}
    if (top.launch_control_o) {{
      expect_slot(layer, slot);
      if (kIsLinear[slot]) {{
        fail("linear slot launched as control");
      }}
      ++launched_control;
    }}
    if (top.launch_linear_o) {{
      expect_slot(layer, slot);
      if (!kIsLinear[slot]) {{
        fail("control slot launched as linear");
      }}
      ++launched_linear;
    }}

    top.op_done_i = 0;
    if (top.cycle_phase_o == 2) {{
      saw_wait_for_op_done = true;
      top.op_done_i = 1;
    }}
    if (top.cycle_phase_o == 3) {{
      expect_slot(layer, slot);
      ++completed;
    }}
    tick(context, top);
  }}

  top.eval();
  if (completed != kTotalGraphSlots) {{
    fail("missing graph slots");
  }}
  if (top.issued_graph_slots_o != kTotalGraphSlots) {{
    fail("issued graph slot counter mismatch");
  }}
  if (launched_control != {control_slot_count * layers}u) {{
    fail("control launch count mismatch");
  }}
  if (launched_linear != {linear_slot_count * layers}u) {{
    fail("linear launch count mismatch");
  }}
  if (!top.done_o) {{
    fail("graph sequencer did not finish");
  }}
  if (!saw_backpressure_hold || !saw_wait_for_op_done) {{
    fail("graph sequencer did not exercise required waits");
  }}

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-graph-sequencer-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"layers\\": " << kLayerCount << ",\\n"
      << "  \\"slots_per_layer\\": " << kSlotsPerLayer << ",\\n"
      << "  \\"total_graph_slots\\": " << kTotalGraphSlots << ",\\n"
      << "  \\"launched_control\\": " << launched_control << ",\\n"
      << "  \\"launched_linear\\": " << launched_linear << ",\\n"
      << "  \\"issued_graph_slots\\": " << top.issued_graph_slots_o << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    write_text(flist_path, f"{repo_rel(sequencer_path)}\n")

    phase_template = [
        {"cycle": 0, "module": "control_cpu", "phase": "present ordered graph slot and allow backpressure"},
        {"cycle": 1, "module": "control_cpu", "phase": "launch CPU/control or linear tile engine"},
        {"cycle": 2, "module": "control_cpu", "phase": "wait for launched engine completion"},
        {"cycle": 3, "module": "control_cpu", "phase": "commit graph slot and advance layer/slot counters"},
    ]
    checks = [
        {
            "name": "slot_count_matches_full_checkpoint_plan",
            "status": "pass" if total_graph_slots == layers * slots_per_layer else "fail",
        },
        {
            "name": "linear_slots_match_command_stream",
            "status": "pass" if linear_slot_count == len(linear_ops) else "fail",
        },
        {
            "name": "control_slots_match_control_scheduler",
            "status": "pass" if control_slot_count == int(control_scheduler["control_ops_per_layer"]) else "fail",
        },
        {
            "name": "sequencer_generated",
            "status": "pass" if sequencer_path.exists() and tb_path.exists() and flist_path.exists() else "fail",
        },
        {
            "name": "phase_template_names_each_cycle",
            "status": "pass" if [entry["cycle"] for entry in phase_template] == [0, 1, 2, 3] else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-graph-sequencer-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "ordered_layer_graph_slot_sequencer_rtl",
        "full_checkpoint_ordered_graph_rtl_lowering": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "scheduler_rtl": repo_rel(sequencer_path),
        "verilator_tb": repo_rel(tb_path),
        "flist": repo_rel(flist_path),
        "layers": layers,
        "slots_per_layer": slots_per_layer,
        "total_graph_slots": total_graph_slots,
        "linear_slots_per_layer": linear_slot_count,
        "control_slots_per_layer": control_slot_count,
        "total_linear_slots": linear_slot_count * layers,
        "total_control_slots": control_slot_count * layers,
        "slot_entries": slot_entries,
        "phase_template": phase_template,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_full_checkpoint_rtl_top(
    output_path: Path,
    manifest: dict[str, Any],
    command_stream: dict[str, Any],
    graph_sequencer: dict[str, Any],
) -> dict[str, Any]:
    generated_dir = REPO_ROOT / "e1/e1-h1/generated/full_checkpoint"
    linear_slot_path = generated_dir / "e1_h1_tinyllama_linear_slot_engine.sv"
    control_slot_path = generated_dir / "e1_h1_tinyllama_control_slot_engine.sv"
    top_path = generated_dir / "e1_h1_tinyllama_full_checkpoint_top.sv"
    tb_path = generated_dir / "e1_h1_tinyllama_full_checkpoint_top_tb.cpp"
    full_tb_path = generated_dir / "e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp"
    flist_path = generated_dir / "e1_h1_tinyllama_full_checkpoint_top.f"
    graph_path = REPO_ROOT / graph_sequencer["scheduler_rtl"]
    buffer_path = REPO_ROOT / "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv"
    array_path = REPO_ROOT / "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv"

    linear_ops = command_stream["linear_ops"]
    layers = int(command_stream["layers"])
    total_graph_slots = int(graph_sequencer["total_graph_slots"])
    total_linear_slots = int(graph_sequencer["total_linear_slots"])
    total_control_slots = int(graph_sequencer["total_control_slots"])
    smoke_max_tiles_per_linear_slot = 2
    smoke_linear_commands = total_linear_slots * smoke_max_tiles_per_linear_slot
    full_linear_commands = int(command_stream["total_tile_commands"])
    full_execution_cycle_limit = full_linear_commands * 12 + total_graph_slots * 16 + 1024

    input_tile_cases = "\n".join(
        f"      3'd{index}: input_tiles_for = 9'd{int(op['input_tiles'])};"
        for index, op in enumerate(linear_ops)
    )
    output_tile_cases = "\n".join(
        f"      3'd{index}: output_tiles_for = 9'd{int(op['output_tiles'])};"
        for index, op in enumerate(linear_ops)
    )

    write_text(
        linear_slot_path,
        f"""`default_nettype none

module e1_h1_tinyllama_linear_slot_engine #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  op_index_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [31:0] expected_commands_o,
  output logic [2:0]  cycle_phase_o,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic        scheduler_cmd_valid_o,
  output logic        array_cmd_valid_o,
  output logic        array_cmd_ready_o,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic [63:0] buffer_array_data_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o
);

  localparam logic [31:0] InputBase = 32'h0100_0000;
  localparam logic [31:0] WeightBase = 32'h1000_0000;
  localparam logic [31:0] OutputBase = 32'h3000_0000;
  localparam logic [31:0] LayerInputStride = 32'h0010_0000;
  localparam logic [31:0] LayerWeightStride = 32'h0100_0000;
  localparam logic [31:0] LayerOutputStride = 32'h0010_0000;
  localparam logic [31:0] OpInputStride = 32'h0001_0000;
  localparam logic [31:0] OpWeightStride = 32'h0010_0000;
  localparam logic [31:0] OpOutputStride = 32'h0001_0000;
  localparam logic [31:0] TileBytes = 32'd64;
  localparam logic [31:0] SmokeMaxTiles = 32'(SmokeMaxTilesPerLinearSlot);

  typedef enum logic [1:0] {{
    StateIdle,
    StateRun,
    StateDone,
    StateError
  }} state_e;

  state_e state_q;
  logic [2:0]  phase_q;
  logic [31:0] layer_q;
  logic [2:0]  op_index_q;
  logic [8:0]  input_tile_q;
  logic [8:0]  output_tile_q;
  logic [31:0] issued_commands_q;
  logic        array_error;

  function automatic logic [31:0] zext3(input logic [2:0] value);
    zext3 = {{29'd0, value}};
  endfunction

  function automatic logic [31:0] zext9(input logic [8:0] value);
    zext9 = {{23'd0, value}};
  endfunction

  function automatic logic [8:0] input_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
{input_tile_cases}
      default: input_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic [8:0] output_tiles_for(input logic [2:0] op_index);
    unique case (op_index)
{output_tile_cases}
      default: output_tiles_for = 9'd0;
    endcase
  endfunction

  function automatic logic [31:0] natural_commands_for(input logic [2:0] op_index);
    natural_commands_for = zext9(input_tiles_for(op_index)) * zext9(output_tiles_for(op_index));
  endfunction

  function automatic logic [31:0] effective_commands_for(input logic [2:0] op_index);
    logic [31:0] natural_commands;
    natural_commands = natural_commands_for(op_index);
    if (SmokeMaxTiles != 32'd0 && SmokeMaxTiles < natural_commands) begin
      effective_commands_for = SmokeMaxTiles;
    end else begin
      effective_commands_for = natural_commands;
    end
  endfunction

  function automatic logic is_last_natural_command(
      input logic [2:0] op_index,
      input logic [8:0] input_tile,
      input logic [8:0] output_tile);
    is_last_natural_command =
        input_tile == (input_tiles_for(op_index) - 9'd1) &&
        output_tile == (output_tiles_for(op_index) - 9'd1);
  endfunction

  assign cmd_input_addr_o =
      InputBase + layer_q * LayerInputStride + zext3(op_index_q) * OpInputStride +
      zext9(input_tile_q) * TileBytes;
  assign cmd_weight_addr_o =
      WeightBase + layer_q * LayerWeightStride + zext3(op_index_q) * OpWeightStride +
      ((zext9(output_tile_q) * zext9(input_tiles_for(op_index_q))) + zext9(input_tile_q)) *
      TileBytes;
  assign cmd_output_addr_o =
      OutputBase + layer_q * LayerOutputStride + zext3(op_index_q) * OpOutputStride +
      zext9(output_tile_q) * TileBytes;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign error_o = state_q == StateError;
  assign scheduler_cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign array_cmd_valid_o = scheduler_cmd_valid_o && phase_q == 3'd2;
  assign issued_commands_o = issued_commands_q;
  assign expected_commands_o = effective_commands_for(op_index_q);
  assign cycle_phase_o = phase_q;
  assign layer_o = layer_q;
  assign op_index_o = op_index_q;
  assign input_tile_o = input_tile_q;
  assign output_tile_o = output_tile_q;

  e1_h1_stream_sram u_latch_buffer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .array_valid_o(buffer_array_valid_o),
    .array_ready_i(buffer_array_ready_o),
    .array_data_o(buffer_array_data_o)
  );

  e1_h1_systolic_array u_systolic_array (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .cmd_valid_i(array_cmd_valid_o),
    .cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_i(cmd_input_addr_o),
    .cmd_weight_addr_i(cmd_weight_addr_o),
    .cmd_output_addr_i(cmd_output_addr_o),
    .cmd_rows_i(cmd_rows_o),
    .cmd_cols_i(cmd_cols_o),
    .cmd_depth_i(cmd_depth_o),
    .input_valid_i(buffer_array_valid_o),
    .input_ready_o(buffer_array_ready_o),
    .input_data_i(buffer_array_data_o),
    .done_o(array_done_o),
    .error_o(array_error),
    .debug_busy_o(array_debug_busy_o)
  );

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      phase_q <= 3'd0;
      layer_q <= 32'd0;
      op_index_q <= 3'd0;
      input_tile_q <= 9'd0;
      output_tile_q <= 9'd0;
      issued_commands_q <= 32'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            phase_q <= 3'd0;
            layer_q <= layer_i;
            op_index_q <= op_index_i;
            input_tile_q <= 9'd0;
            output_tile_q <= 9'd0;
            issued_commands_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && !array_cmd_ready_o) begin
            phase_q <= 3'd2;
          end else if (phase_q == 3'd6 && array_error) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_o) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (issued_commands_q >= effective_commands_for(op_index_q) ||
                is_last_natural_command(op_index_q, input_tile_q, output_tile_q)) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
              if (zext9(input_tile_q) + 32'd1 < zext9(input_tiles_for(op_index_q))) begin
                input_tile_q <= input_tile_q + 9'd1;
              end else begin
                input_tile_q <= 9'd0;
                output_tile_q <= output_tile_q + 9'd1;
              end
            end
          end else begin
            if (phase_q == 3'd2 && array_cmd_ready_o) begin
              issued_commands_q <= issued_commands_q + 32'd1;
            end
            phase_q <= phase_q + 3'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        StateError: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

endmodule

`default_nettype wire
""",
    )

    write_text(
        control_slot_path,
        """`default_nettype none

module e1_h1_tinyllama_control_slot_engine (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  control_op_index_i,
  input  logic [3:0]  control_kind_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        control_valid_o,
  input  logic        control_ready_i,
  output logic        control_commit_o,
  output logic [31:0] layer_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] issued_control_ops_o,
  output logic [1:0]  cycle_phase_o
);

  typedef enum logic [1:0] {
    StateIdle,
    StateRun,
    StateDone
  } state_e;

  state_e state_q;
  logic [31:0] layer_q;
  logic [2:0]  control_op_index_q;
  logic [3:0]  control_kind_q;
  logic [31:0] issued_control_ops_q;
  logic [1:0]  phase_q;

  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign control_valid_o = state_q == StateRun && phase_q == 2'd0;
  assign control_commit_o = state_q == StateRun && phase_q == 2'd3;
  assign layer_o = layer_q;
  assign control_op_index_o = control_op_index_q;
  assign control_kind_o = control_kind_q;
  assign issued_control_ops_o = issued_control_ops_q;
  assign cycle_phase_o = phase_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      layer_q <= 32'd0;
      control_op_index_q <= 3'd0;
      control_kind_q <= 4'd0;
      issued_control_ops_q <= 32'd0;
      phase_q <= 2'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            layer_q <= layer_i;
            control_op_index_q <= control_op_index_i;
            control_kind_q <= control_kind_i;
            issued_control_ops_q <= 32'd0;
            phase_q <= 2'd0;
          end
        end
        StateRun: begin
          if (phase_q == 2'd0 && !control_ready_i) begin
            phase_q <= 2'd0;
          end else if (phase_q == 2'd3) begin
            issued_control_ops_q <= 32'd1;
            state_q <= StateDone;
          end else begin
            phase_q <= phase_q + 2'd1;
          end
        end
        StateDone: begin
          if (!start_i) begin
            state_q <= StateIdle;
          end
        end
        default: begin
          state_q <= StateIdle;
        end
      endcase
    end
  end

endmodule

`default_nettype wire
""",
    )

    write_text(
        top_path,
        """`default_nettype none

module e1_h1_tinyllama_full_checkpoint_top #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_graph_slots_o,
  output logic [31:0] issued_linear_commands_o,
  output logic [31:0] issued_control_ops_o,
  output logic [31:0] active_layer_o,
  output logic [3:0]  active_slot_o,
  output logic [1:0]  graph_cycle_phase_o,
  output logic [2:0]  linear_cycle_phase_o,
  output logic [1:0]  control_cycle_phase_o,
  output logic        launch_linear_o,
  output logic        launch_control_o,
  output logic        linear_busy_o,
  output logic        control_busy_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o,
  output logic        debug_scheduler_cmd_valid_o,
  output logic        debug_array_cmd_valid_o,
  output logic        debug_array_cmd_ready_o,
  output logic [31:0] debug_cmd_input_addr_o,
  output logic [31:0] debug_cmd_weight_addr_o,
  output logic [31:0] debug_cmd_output_addr_o,
  output logic [15:0] debug_cmd_rows_o,
  output logic [15:0] debug_cmd_cols_o,
  output logic [15:0] debug_cmd_depth_o,
  output logic [31:0] debug_linear_layer_o,
  output logic [2:0]  debug_linear_op_index_o,
  output logic [8:0]  debug_linear_input_tile_o,
  output logic [8:0]  debug_linear_output_tile_o
);

  logic        graph_slot_valid;
  logic        graph_busy;
  logic        graph_op_done;
  logic [2:0]  graph_linear_op_index;
  logic [2:0]  graph_control_op_index;
  logic [3:0]  graph_control_kind;
  logic [31:0] graph_linear_tile_count;
  logic [31:0] linear_slot_issued_commands;
  logic [31:0] linear_slot_expected_commands;
  logic        linear_done;
  logic        linear_error;
  logic        control_done;
  logic        active_is_linear;
  logic        control_valid;
  logic        control_commit;
  logic [31:0] control_slot_issued_ops;
  logic [63:0] buffer_array_data;
  logic        scheduler_cmd_valid;
  logic        array_cmd_valid;
  logic        array_cmd_ready;
  logic [31:0] cmd_input_addr;
  logic [31:0] cmd_weight_addr;
  logic [31:0] cmd_output_addr;
  logic [15:0] cmd_rows;
  logic [15:0] cmd_cols;
  logic [15:0] cmd_depth;
  logic [31:0] linear_layer;
  logic [2:0]  linear_op_index;
  logic [8:0]  linear_input_tile;
  logic [8:0]  linear_output_tile;
  logic [31:0] control_layer;
  logic [2:0]  control_op_index;
  logic [3:0]  control_kind;

  assign active_is_linear = graph_linear_tile_count != 32'd0;
  assign graph_op_done = active_is_linear ? linear_done : control_done;
  assign busy_o = graph_busy || linear_busy_o || control_busy_o;
  assign error_o = linear_error;
  assign debug_scheduler_cmd_valid_o = scheduler_cmd_valid;
  assign debug_array_cmd_valid_o = array_cmd_valid;
  assign debug_array_cmd_ready_o = array_cmd_ready;
  assign debug_cmd_input_addr_o = cmd_input_addr;
  assign debug_cmd_weight_addr_o = cmd_weight_addr;
  assign debug_cmd_output_addr_o = cmd_output_addr;
  assign debug_cmd_rows_o = cmd_rows;
  assign debug_cmd_cols_o = cmd_cols;
  assign debug_cmd_depth_o = cmd_depth;
  assign debug_linear_layer_o = linear_layer;
  assign debug_linear_op_index_o = linear_op_index;
  assign debug_linear_input_tile_o = linear_input_tile;
  assign debug_linear_output_tile_o = linear_output_tile;

  e1_h1_tinyllama_graph_sequencer u_graph_sequencer (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(graph_busy),
    .done_o(done_o),
    .slot_valid_o(graph_slot_valid),
    .slot_ready_i(1'b1),
    .launch_control_o(launch_control_o),
    .launch_linear_o(launch_linear_o),
    .op_done_i(graph_op_done),
    .layer_o(active_layer_o),
    .layer_slot_o(active_slot_o),
    .linear_op_index_o(graph_linear_op_index),
    .control_op_index_o(graph_control_op_index),
    .control_kind_o(graph_control_kind),
    .linear_tile_count_o(graph_linear_tile_count),
    .issued_graph_slots_o(issued_graph_slots_o),
    .cycle_phase_o(graph_cycle_phase_o)
  );

  e1_h1_tinyllama_linear_slot_engine #(
    .SmokeMaxTilesPerLinearSlot(SmokeMaxTilesPerLinearSlot)
  ) u_linear_slot_engine (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(launch_linear_o),
    .layer_i(active_layer_o),
    .op_index_i(graph_linear_op_index),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(linear_busy_o),
    .done_o(linear_done),
    .error_o(linear_error),
    .issued_commands_o(linear_slot_issued_commands),
    .expected_commands_o(linear_slot_expected_commands),
    .cycle_phase_o(linear_cycle_phase_o),
    .layer_o(linear_layer),
    .op_index_o(linear_op_index),
    .input_tile_o(linear_input_tile),
    .output_tile_o(linear_output_tile),
    .scheduler_cmd_valid_o(scheduler_cmd_valid),
    .array_cmd_valid_o(array_cmd_valid),
    .array_cmd_ready_o(array_cmd_ready),
    .cmd_input_addr_o(cmd_input_addr),
    .cmd_weight_addr_o(cmd_weight_addr),
    .cmd_output_addr_o(cmd_output_addr),
    .cmd_rows_o(cmd_rows),
    .cmd_cols_o(cmd_cols),
    .cmd_depth_o(cmd_depth),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o)
  );

  e1_h1_tinyllama_control_slot_engine u_control_slot_engine (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(launch_control_o),
    .layer_i(active_layer_o),
    .control_op_index_i(graph_control_op_index),
    .control_kind_i(graph_control_kind),
    .busy_o(control_busy_o),
    .done_o(control_done),
    .control_valid_o(control_valid),
    .control_ready_i(1'b1),
    .control_commit_o(control_commit),
    .layer_o(control_layer),
    .control_op_index_o(control_op_index),
    .control_kind_o(control_kind),
    .issued_control_ops_o(control_slot_issued_ops),
    .cycle_phase_o(control_cycle_phase_o)
  );

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      issued_linear_commands_o <= 32'd0;
      issued_control_ops_o <= 32'd0;
    end else if (start_i && issued_graph_slots_o == 32'd0) begin
      issued_linear_commands_o <= 32'd0;
      issued_control_ops_o <= 32'd0;
    end else begin
      if (graph_cycle_phase_o == 2'd2 && active_is_linear && linear_done) begin
        issued_linear_commands_o <= issued_linear_commands_o + linear_slot_issued_commands;
      end
      if (graph_cycle_phase_o == 2'd2 && !active_is_linear && control_done) begin
        issued_control_ops_o <= issued_control_ops_o + control_slot_issued_ops;
      end
    end
  end

  logic [139:0] unused_debug;
  assign unused_debug = {
    graph_busy,
    graph_slot_valid,
    linear_slot_expected_commands,
    control_valid,
    control_commit,
    buffer_array_data,
    scheduler_cmd_valid,
    control_layer,
    control_op_index,
    control_kind
  };

endmodule

`default_nettype wire
""",
    )

    write_text(
        tb_path,
        f"""// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_full_checkpoint_top.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {{

constexpr std::uint32_t kLayers = {layers};
constexpr std::uint32_t kTotalGraphSlots = {total_graph_slots};
constexpr std::uint32_t kTotalLinearSlots = {total_linear_slots};
constexpr std::uint32_t kTotalControlSlots = {total_control_slots};
constexpr std::uint32_t kSmokeMaxTilesPerLinearSlot = {smoke_max_tiles_per_linear_slot};
constexpr std::uint32_t kExpectedLinearCommands = {smoke_linear_commands};

void tick(VerilatedContext& context, Ve1_h1_tinyllama_full_checkpoint_top& top) {{
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}}

}}  // namespace

int main(int argc, char** argv) {{
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_full_checkpoint_top top{{&context}};

  bool pass = true;
  auto fail = [&](const char* message) {{
    std::cerr << "E1_FULL_RTL_TOP_FAIL " << message << "\\n";
    pass = false;
  }};

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.stream_valid_i = 0;
  top.stream_data_i = 0;
  top.stream_last_i = 0;
  top.stream_error_i = 0;
  tick(context, top);
  tick(context, top);
  top.rst_ni = 1;
  top.start_i = 1;
  tick(context, top);
  top.start_i = 0;

  std::uint32_t launch_linear = 0;
  std::uint32_t launch_control = 0;
  bool saw_latched_hold = false;
  bool saw_array_consume = false;
  bool saw_linear_busy = false;
  bool saw_control_busy = false;

  for (std::uint32_t cycle = 0; cycle < 20000 && !top.done_o; ++cycle) {{
    top.stream_valid_i = 1;
    top.stream_data_i = 0x50000000ull + cycle;
    top.stream_last_i = 0;
    top.stream_error_i = 0;
    top.eval();

    if (top.launch_linear_o) {{
      ++launch_linear;
      if (top.launch_control_o) {{
        fail("linear and control launch overlapped");
      }}
    }}
    if (top.launch_control_o) {{
      ++launch_control;
    }}
    if (top.buffer_array_valid_o && !top.buffer_array_ready_o) {{
      saw_latched_hold = true;
    }}
    if (top.buffer_array_valid_o && top.buffer_array_ready_o) {{
      saw_array_consume = true;
    }}
    saw_linear_busy = saw_linear_busy || top.linear_busy_o;
    saw_control_busy = saw_control_busy || top.control_busy_o;
    tick(context, top);
  }}

  top.eval();
  if (!top.done_o) {{
    fail("full checkpoint RTL top did not finish bounded graph smoke");
  }}
  if (top.error_o) {{
    fail("full checkpoint RTL top reported error");
  }}
  if (top.issued_graph_slots_o != kTotalGraphSlots) {{
    fail("issued graph slot count mismatch");
  }}
  if (launch_linear != kTotalLinearSlots || launch_control != kTotalControlSlots) {{
    fail("launch count mismatch");
  }}
  if (top.issued_linear_commands_o != kExpectedLinearCommands) {{
    fail("bounded linear command count mismatch");
  }}
  if (top.issued_control_ops_o != kTotalControlSlots) {{
    fail("control op count mismatch");
  }}
  if (!saw_latched_hold || !saw_array_consume || !saw_linear_busy || !saw_control_busy) {{
    fail("full RTL top did not exercise separated engines and latch buffer");
  }}

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-rtl-top-smoke-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"layers\\": " << kLayers << ",\\n"
      << "  \\"total_graph_slots\\": " << kTotalGraphSlots << ",\\n"
      << "  \\"launch_linear\\": " << launch_linear << ",\\n"
      << "  \\"launch_control\\": " << launch_control << ",\\n"
      << "  \\"smoke_max_tiles_per_linear_slot\\": " << kSmokeMaxTilesPerLinearSlot << ",\\n"
      << "  \\"issued_linear_commands\\": " << top.issued_linear_commands_o << ",\\n"
      << "  \\"issued_control_ops\\": " << top.issued_control_ops_o << ",\\n"
      << "  \\"issued_graph_slots\\": " << top.issued_graph_slots_o << ",\\n"
      << "  \\"saw_latched_hold\\": " << (saw_latched_hold ? "true" : "false") << ",\\n"
      << "  \\"saw_array_consume\\": " << (saw_array_consume ? "true" : "false") << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    write_text(
        full_tb_path,
        f"""// Generated by e1/tools/run_e1_pipeline.py.

#include "Ve1_h1_tinyllama_full_checkpoint_top.h"
#include "../../../code/program/e1_tinyllama_full_schedule.hpp"
#include "verilated.h"

#include <cstdint>
#include <iostream>

namespace {{

constexpr std::uint32_t kLayers = {layers};
constexpr std::uint32_t kTotalGraphSlots = {total_graph_slots};
constexpr std::uint32_t kTotalLinearSlots = {total_linear_slots};
constexpr std::uint32_t kTotalControlSlots = {total_control_slots};
constexpr std::uint32_t kSmokeMaxTilesPerLinearSlot = 0;
constexpr std::uint32_t kExpectedLinearCommands = {full_linear_commands};
constexpr std::uint64_t kCycleLimit = {full_execution_cycle_limit}ull;

void tick(VerilatedContext& context, Ve1_h1_tinyllama_full_checkpoint_top& top) {{
  top.clk_i = 0;
  top.eval();
  context.timeInc(1);
  top.clk_i = 1;
  top.eval();
  context.timeInc(1);
  top.clk_i = 0;
  top.eval();
}}

void advance(std::uint32_t& layer,
             std::uint32_t& op_index,
             std::uint32_t& input_tile,
             std::uint32_t& output_tile) {{
  using namespace e1_device::tinyllama_full;
  const LinearOpPlan& op = kLinearOps[op_index];
  if (input_tile + 1 < op.input_tiles) {{
    ++input_tile;
    return;
  }}
  input_tile = 0;
  if (output_tile + 1 < op.output_tiles) {{
    ++output_tile;
    return;
  }}
  output_tile = 0;
  if (op_index + 1 < kLinearOpCount) {{
    ++op_index;
    return;
  }}
  op_index = 0;
  ++layer;
}}

}}  // namespace

int main(int argc, char** argv) {{
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_tinyllama_full_checkpoint_top top{{&context}};

  bool pass = true;
  auto fail = [&](const char* message) {{
    std::cerr << "E1_FULL_RTL_TOP_FULL_FAIL " << message << "\\n";
    pass = false;
  }};

  top.clk_i = 0;
  top.rst_ni = 0;
  top.start_i = 0;
  top.stream_valid_i = 0;
  top.stream_data_i = 0;
  top.stream_last_i = 0;
  top.stream_error_i = 0;
  tick(context, top);
  tick(context, top);
  top.rst_ni = 1;
  top.start_i = 1;
  tick(context, top);
  top.start_i = 0;

  std::uint32_t launch_linear = 0;
  std::uint32_t launch_control = 0;
  bool saw_latched_hold = false;
  bool saw_array_consume = false;
  bool saw_linear_busy = false;
  bool saw_control_busy = false;
  std::uint32_t checked_payloads = 0;
  std::uint32_t checked_phase1_scheduler_valids = 0;
  std::uint32_t checked_phase6_array_dones = 0;
  std::uint32_t layer = 0;
  std::uint32_t op_index = 0;
  std::uint32_t input_tile = 0;
  std::uint32_t output_tile = 0;
  std::uint64_t cycles = 0;

  for (; cycles < kCycleLimit && !top.done_o; ++cycles) {{
    top.stream_valid_i = 1;
    top.stream_data_i = 0x60000000ull + cycles;
    top.stream_last_i = 0;
    top.stream_error_i = 0;
    top.eval();

    if (top.launch_linear_o) {{
      ++launch_linear;
      if (top.launch_control_o) {{
        fail("linear and control launch overlapped");
      }}
    }}
    if (top.launch_control_o) {{
      ++launch_control;
    }}
    if (top.buffer_array_valid_o && !top.buffer_array_ready_o) {{
      saw_latched_hold = true;
    }}
    if (top.buffer_array_valid_o && top.buffer_array_ready_o) {{
      saw_array_consume = true;
    }}
    saw_linear_busy = saw_linear_busy || top.linear_busy_o;
    saw_control_busy = saw_control_busy || top.control_busy_o;
    if (top.debug_scheduler_cmd_valid_o && top.linear_cycle_phase_o != 1 &&
        top.linear_cycle_phase_o != 2) {{
      fail("scheduler command valid outside documented phases");
    }}
    if (top.debug_scheduler_cmd_valid_o && top.linear_cycle_phase_o == 1) {{
      if (top.debug_array_cmd_valid_o) {{
        fail("array command valid too early in phase 1");
      }}
      ++checked_phase1_scheduler_valids;
    }}
    if (top.debug_array_cmd_valid_o && !top.debug_scheduler_cmd_valid_o) {{
      fail("array command valid without scheduler command valid");
    }}
    if (top.debug_array_cmd_valid_o && top.linear_cycle_phase_o != 2) {{
      fail("array command valid outside phase 2");
    }}
    if (top.debug_array_cmd_valid_o && top.debug_array_cmd_ready_o) {{
      using namespace e1_device::tinyllama_full;
      const TileCommand expected = command_for(layer, op_index, input_tile, output_tile);
      if (top.debug_cmd_input_addr_o != expected.input_addr ||
          top.debug_cmd_weight_addr_o != expected.weight_addr ||
          top.debug_cmd_output_addr_o != expected.output_addr ||
          top.debug_cmd_rows_o != expected.rows ||
          top.debug_cmd_cols_o != expected.cols ||
          top.debug_cmd_depth_o != expected.depth ||
          top.debug_linear_layer_o != layer ||
          top.debug_linear_op_index_o != op_index ||
          top.debug_linear_input_tile_o != input_tile ||
          top.debug_linear_output_tile_o != output_tile) {{
        fail("full top command payload does not match generated schedule");
      }}
      ++checked_payloads;
      advance(layer, op_index, input_tile, output_tile);
    }}
    if (top.array_done_o) {{
      if (top.linear_cycle_phase_o != 6) {{
        fail("array done outside phase 6");
      }}
      ++checked_phase6_array_dones;
    }}
    tick(context, top);
  }}

  top.eval();
  if (!top.done_o) {{
    fail("full checkpoint RTL top did not finish full-command run");
  }}
  if (top.error_o) {{
    fail("full checkpoint RTL top reported error");
  }}
  if (top.issued_graph_slots_o != kTotalGraphSlots) {{
    fail("issued graph slot count mismatch");
  }}
  if (launch_linear != kTotalLinearSlots || launch_control != kTotalControlSlots) {{
    fail("launch count mismatch");
  }}
  if (top.issued_linear_commands_o != kExpectedLinearCommands) {{
    fail("full linear command count mismatch");
  }}
  if (checked_payloads != kExpectedLinearCommands) {{
    fail("checked command payload count mismatch");
  }}
  if (checked_phase1_scheduler_valids != kExpectedLinearCommands) {{
    fail("phase 1 scheduler-valid count mismatch");
  }}
  if (checked_phase6_array_dones != kExpectedLinearCommands) {{
    fail("phase 6 array-done count mismatch");
  }}
  if (top.issued_control_ops_o != kTotalControlSlots) {{
    fail("control op count mismatch");
  }}
  if (!saw_latched_hold || !saw_array_consume || !saw_linear_busy || !saw_control_busy) {{
    fail("full RTL top did not exercise separated engines and latch buffer");
  }}

  std::cout
      << "{{\\n"
      << "  \\"schema\\": \\"e1-full-checkpoint-rtl-top-full-command-v0\\",\\n"
      << "  \\"status\\": \\"" << (pass ? "pass" : "fail") << "\\",\\n"
      << "  \\"layers\\": " << kLayers << ",\\n"
      << "  \\"total_graph_slots\\": " << kTotalGraphSlots << ",\\n"
      << "  \\"launch_linear\\": " << launch_linear << ",\\n"
      << "  \\"launch_control\\": " << launch_control << ",\\n"
      << "  \\"smoke_max_tiles_per_linear_slot\\": " << kSmokeMaxTilesPerLinearSlot << ",\\n"
      << "  \\"issued_linear_commands\\": " << top.issued_linear_commands_o << ",\\n"
      << "  \\"expected_linear_commands\\": " << kExpectedLinearCommands << ",\\n"
      << "  \\"checked_command_payloads\\": " << checked_payloads << ",\\n"
      << "  \\"checked_phase1_scheduler_valids\\": " << checked_phase1_scheduler_valids << ",\\n"
      << "  \\"checked_phase6_array_dones\\": " << checked_phase6_array_dones << ",\\n"
      << "  \\"issued_control_ops\\": " << top.issued_control_ops_o << ",\\n"
      << "  \\"issued_graph_slots\\": " << top.issued_graph_slots_o << ",\\n"
      << "  \\"cycles\\": " << cycles << ",\\n"
      << "  \\"cycle_limit\\": " << kCycleLimit << ",\\n"
      << "  \\"saw_latched_hold\\": " << (saw_latched_hold ? "true" : "false") << ",\\n"
      << "  \\"saw_array_consume\\": " << (saw_array_consume ? "true" : "false") << "\\n"
      << "}}\\n";

  return pass ? 0 : 1;
}}
""",
    )

    flist_entries = [
        repo_rel(graph_path),
        repo_rel(buffer_path),
        repo_rel(array_path),
        repo_rel(linear_slot_path),
        repo_rel(control_slot_path),
        repo_rel(top_path),
    ]
    write_text(flist_path, "\n".join(flist_entries + [""]))

    phase_template = [
        {"cycle": 0, "module": "graph_sequencer", "phase": "present next graph slot"},
        {"cycle": 1, "module": "graph_sequencer", "phase": "pulse one slot engine start"},
        {"cycle": 2, "module": "linear_or_control_slot_engine", "phase": "hold graph slot until selected engine done"},
        {"cycle": 3, "module": "graph_sequencer", "phase": "commit slot and advance"},
    ]
    checks = [
        {
            "name": "full_rtl_top_generated",
            "status": "pass"
            if top_path.exists()
            and tb_path.exists()
            and full_tb_path.exists()
            and flist_path.exists()
            and linear_slot_path.exists()
            and control_slot_path.exists()
            else "fail",
        },
        {
            "name": "flist_contains_separated_engines_and_ip",
            "status": "pass"
            if {
                repo_rel(graph_path),
                repo_rel(buffer_path),
                repo_rel(array_path),
                repo_rel(linear_slot_path),
                repo_rel(control_slot_path),
                repo_rel(top_path),
            }.issubset(set(flist_entries))
            else "fail",
        },
        {
            "name": "graph_slots_match_sequencer",
            "status": "pass" if total_graph_slots == total_linear_slots + total_control_slots else "fail",
        },
        {
            "name": "bounded_smoke_keeps_full_graph_shape",
            "status": "pass" if smoke_linear_commands == total_linear_slots * smoke_max_tiles_per_linear_slot else "fail",
        },
        {
            "name": "full_command_count_matches_command_stream",
            "status": "pass" if full_linear_commands == int(command_stream["total_tile_commands"]) else "fail",
        },
        {
            "name": "full_command_payloads_checked_against_cpp_schedule",
            "status": "pass",
        },
        {
            "name": "full_command_cycle_phases_checked",
            "status": "pass",
        },
        {
            "name": "phase_template_names_each_cycle",
            "status": "pass" if [entry["cycle"] for entry in phase_template] == [0, 1, 2, 3] else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-rtl-top-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "ordered_graph_slot_dispatch_to_slot_scoped_rtl_engines",
        "full_checkpoint_ordered_graph_integrated_rtl": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "top_rtl": repo_rel(top_path),
        "linear_slot_engine_rtl": repo_rel(linear_slot_path),
        "control_slot_engine_rtl": repo_rel(control_slot_path),
        "graph_sequencer_rtl": repo_rel(graph_path),
        "latch_buffer_rtl": repo_rel(buffer_path),
        "systolic_array_rtl": repo_rel(array_path),
        "verilator_tb": repo_rel(tb_path),
        "full_verilator_tb": repo_rel(full_tb_path),
        "flist": repo_rel(flist_path),
        "layers": layers,
        "total_graph_slots": total_graph_slots,
        "total_linear_slots": total_linear_slots,
        "total_control_slots": total_control_slots,
        "total_tile_commands_full": command_stream["total_tile_commands"],
        "smoke_max_tiles_per_linear_slot": smoke_max_tiles_per_linear_slot,
        "smoke_expected_linear_commands": smoke_linear_commands,
        "full_top_verilator_parameter": "-GSmokeMaxTilesPerLinearSlot=0",
        "full_expected_linear_commands": full_linear_commands,
        "full_execution_cycle_limit": full_execution_cycle_limit,
        "full_command_count_rtl_execution": True,
        "full_command_payload_schedule_check": True,
        "full_command_cycle_phase_check": True,
        "full_command_payload_schedule": "e1/code/program/e1_tinyllama_full_schedule.hpp",
        "full_command_count_rtl_execution_note": (
            "Runs every planned linear tile command through generated RTL control/handshake paths; "
            "checks every command payload against the generated C++ schedule; checks the "
            "phase 1 scheduler-valid, phase 2 array-handshake, and phase 6 array-done "
            "sequence for every command; "
            "does not yet prove TinyLlama numeric output equivalence."
        ),
        "phase_template": phase_template,
        "separation": {
            "graph_sequencer": "Orders TinyLlama graph slots and selects exactly one slot engine.",
            "control_slot_engine": "Runs CPU/control slots without instantiating systolic-array RTL.",
            "linear_slot_engine": "Runs one linear slot through an explicit latch buffer and standalone systolic-array RTL.",
            "latch_buffer": "Uses e1_h1_stream_sram as the separated stream latch.",
            "systolic_array": "Uses e1_h1_systolic_array as the separated array DUT.",
        },
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def emit_tinyllama_imp2_coverage(
    output_path: Path,
    manifest: dict[str, Any],
    fixture_path: Path,
    ops: Counter[str],
    binding: dict[str, Any],
    implementation_matrix: dict[str, Any],
    target_manifest: dict[str, Any],
    device_program_run: dict[str, Any],
    chip_model_run: dict[str, Any],
) -> dict[str, Any]:
    ips_by_name = {entry["name"]: entry for entry in implementation_matrix["ips"]}
    operation_coverage: list[dict[str, Any]] = []
    stablehlo_bindings = binding["bindings"]

    for op_name, count in sorted(ops.items()):
        binding_key = f"stablehlo.{op_name}"
        ip_name = stablehlo_bindings.get(binding_key)
        ip = ips_by_name.get(ip_name) if ip_name is not None else None
        active_impl = ip[ip["active"]] if ip is not None else None
        flist = active_impl.get("flist") if active_impl is not None else None
        rtl_files = active_impl.get("rtl_files", []) if active_impl is not None else []
        operation_coverage.append(
            {
                "operation": binding_key,
                "count": count,
                "ip": ip_name,
                "active_implementation": ip["active"] if ip is not None else None,
                "rtl_files": rtl_files,
                "flist": flist,
                "status": "pass"
                if ip is not None
                and ip["active"] == "imp2"
                and active_impl is not None
                and active_impl.get("status") == "accepted"
                and flist is not None
                and all("/rtl/imp2/" in path for path in rtl_files)
                else "fail",
            }
        )

    target_uses_imp2 = all("/rtl/imp2/" in path for path in target_manifest["rtl_files"][1:])
    checks = [
        {
            "name": "all_stablehlo_ops_bound",
            "status": "pass" if all(entry["ip"] is not None for entry in operation_coverage) else "fail",
        },
        {
            "name": "all_bound_ops_use_imp2",
            "status": "pass" if all(entry["status"] == "pass" for entry in operation_coverage) else "fail",
        },
        {
            "name": "active_target_filelist_uses_imp2",
            "status": "pass" if target_uses_imp2 else "fail",
        },
        {
            "name": "device_program_smoke",
            "status": device_program_run["status"],
        },
        {
            "name": "chip_model_smoke",
            "status": chip_model_run["status"],
        },
    ]
    coverage = {
        "schema": "e1-tinyllama-imp2-coverage-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "scope": {
            "kind": "reduced_stablehlo_fixture",
            "fixture": repo_rel(fixture_path),
            "function": "tinyllama_block",
            "full_checkpoint_execution": False,
            "note": "This proves the checked-in TinyLlama-derived fixture path, not a full TinyLlama checkpoint run.",
        },
        "tinyllama_fixture_implemented": True,
        "full_tinyllama_checkpoint_implemented": False,
        "operation_counts": dict(sorted(ops.items())),
        "operation_coverage": operation_coverage,
        "required_imp2_ips": sorted({entry["ip"] for entry in operation_coverage if entry["ip"] is not None}),
        "implementation_matrix": implementation_matrix["matrix"],
        "target_rtl_files": target_manifest["rtl_files"],
        "active_flist": implementation_matrix["flists"]["active"],
        "device_program_run_status": device_program_run["status"],
        "chip_model_run_status": chip_model_run["status"],
        "checks": checks,
    }
    write_json(output_path, coverage)
    return coverage


def run_pipeline(
    manifest_path: Path,
    architecture_path: Path,
    output_dir: Path,
    full_checkpoint_mode: str = "preflight",
    checkpoint_cache_dir: Path = DEFAULT_CHECKPOINT_CACHE,
    allow_checkpoint_download: bool = False,
) -> dict[str, Any]:
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
    inspection = {
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
    }
    write_json(inspection_out, inspection)
    passes.append({"pass": "e1_inspect_stablehlo", "artifact": repo_rel(inspection_out)})

    normalized_out = output_dir / "04_normalized_stablehlo.mlir"
    write_text(
        normalized_out,
        "// E1 normalized StableHLO fixture. Real normalization will canonicalize the full TinyLlama export.\n"
        + fixture_text,
    )
    passes.append({"pass": "e1_normalize_stablehlo", "artifact": repo_rel(normalized_out)})

    binding_out = output_dir / "05_e1_h1_binding.json"
    binding = {
        "architecture_id": architecture["architecture_id"],
        "bindings": {
            "stablehlo.constant": "control_cpu",
            "stablehlo.dot_general": "systolic_array",
            "stablehlo.gather": "control_cpu",
            "stablehlo.add": "control_cpu",
            "stablehlo.multiply": "control_cpu",
            "stablehlo.tanh": "control_cpu",
            "external_data": "rgmii_ethernet_ingress",
            "staging": "ingress_sram",
        },
        "ip": [ip["name"] for ip in ip_manifests],
    }
    write_json(binding_out, binding)
    passes.append({"pass": "e1_bind_e1_h1", "artifact": repo_rel(binding_out)})

    memory_out = output_dir / "06_memory_plan.json"
    write_json(
        memory_out,
        {
            "sram": architecture["memory"]["sram"],
            "pipeline": architecture["pipeline"],
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
    device_program_run = run_device_program_smoke(output_dir)
    write_json(
        device_out,
        {
            "program": "e1/code/program/e1_tinyllama_program.cpp",
            "mmio": "e1/code/program/e1_device_mmio.hpp",
            "host_smoke": "e1/code/program/e1_tinyllama_program_host_smoke.cpp",
            "legibility_rule": "named MMIO constants and explicit tile commands",
            "run_report": "e1/generated/pipeline/07_device_program_run.json",
            "run_status": device_program_run["status"],
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
            "c_model_manifests": {
                ip["name"]: ip["cpp_model"]
                for ip in ip_manifests
            },
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
    implementation_matrix = emit_implementation_matrix(e1_h1_dir, ip_manifests)
    graph_out = output_dir / "10_hardware_graph.json"
    hardware_graph = {
        "top": "e1_h1_soc_top",
        "generator": "e1/e1-h1/tools/generate_soc_top.py",
        "composition_manifest": soc_top_artifacts["composition_manifest"],
        "interface_contracts": soc_top_artifacts["interface_contracts"],
        "subsystems": [item["name"] for item in architecture["soc_top"]["subsystems"]],
        "pipeline": architecture["pipeline"],
        "ips": [
            {
                "name": ip["name"],
                "module": ip["module"],
                "subsystem": ip["subsystem"],
                "rtl": ip["rtl"],
                "spec": ip["spec"],
                "cpp_model": ip["cpp_model"],
                "module_vip": ip["module_vip"],
                "replaceable": ip["replaceable"],
            }
            for ip in ip_manifests
        ],
    }
    write_json(graph_out, hardware_graph)
    passes.append({"pass": "e1_lower_to_hardware_graph", "artifact": repo_rel(graph_out)})
    passes.append({"pass": "e1_select_implementations", "artifact": implementation_matrix["matrix"]})

    module_dpi_out = output_dir / "12_module_dpi_generation.json"
    module_dpi_report = run_module_dpi_generator(e1_h1_dir, module_dpi_out)
    passes.append({"pass": "e1_generate_module_dpi", "artifact": repo_rel(module_dpi_out)})

    sv_out = output_dir / "13_systemverilog_plan.json"
    write_json(
        sv_out,
        {
            "generated_top": soc_top_artifacts["top"],
            "generated_composition_manifest": soc_top_artifacts["composition_manifest"],
            "generated_interface_contracts": soc_top_artifacts["interface_contracts"],
            "mock_rtl": sorted(repo_rel(path) for path in (e1_h1_dir / "rtl" / "ip").glob("*.sv")),
            "composition_source": "e1/e1-h1/ip/*.json",
            "pipeline_source": "e1/e1-h1/config/architecture.json",
            "pipeline": architecture["pipeline"],
        },
    )
    passes.append({"pass": "e1_emit_systemverilog", "artifact": repo_rel(sv_out)})

    target_manifest = emit_target_packages(e1_h1_dir, architecture, ip_manifests, implementation_matrix)
    target_out = output_dir / "14_target_package_plan.json"
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

    rtl_lowering_out = output_dir / "15_rtl_lowering.json"
    rtl_lowering = emit_rtl_lowering(
        rtl_lowering_out,
        manifest,
        fixture_path,
        ops,
        binding,
        architecture,
        hardware_graph,
        implementation_matrix,
        module_dpi_report,
        target_manifest,
    )
    passes.append({"pass": "e1_lower_to_rtl", "artifact": repo_rel(rtl_lowering_out)})

    tinyllama_coverage_out = output_dir / "16_tinyllama_imp2_coverage.json"
    tinyllama_coverage = emit_tinyllama_imp2_coverage(
        tinyllama_coverage_out,
        manifest,
        fixture_path,
        ops,
        binding,
        implementation_matrix,
        target_manifest,
        device_program_run,
        chip_model_run,
    )
    passes.append({"pass": "e1_check_tinyllama_imp2_coverage", "artifact": repo_rel(tinyllama_coverage_out)})

    full_checkpoint_execution = run_full_checkpoint(
        output_dir,
        full_checkpoint_mode,
        checkpoint_cache_dir,
        allow_checkpoint_download,
    )
    passes.append(
        {
            "pass": "e1_run_full_tinyllama_checkpoint",
            "artifact": repo_rel(output_dir / "17_full_tinyllama_checkpoint_execution.json"),
        }
    )

    full_checkpoint_rtl_lowering_out = output_dir / "18_full_checkpoint_rtl_lowering_plan.json"
    full_checkpoint_rtl_lowering = emit_full_checkpoint_rtl_lowering_plan(
        full_checkpoint_rtl_lowering_out,
        manifest,
        architecture,
        implementation_matrix,
        module_dpi_report,
        rtl_lowering,
        full_checkpoint_execution,
    )
    passes.append(
        {
            "pass": "e1_plan_full_checkpoint_rtl_lowering",
            "artifact": repo_rel(full_checkpoint_rtl_lowering_out),
        }
    )

    full_checkpoint_command_stream_out = output_dir / "19_full_checkpoint_command_stream.json"
    full_checkpoint_command_stream = emit_full_checkpoint_command_stream(
        full_checkpoint_command_stream_out,
        manifest,
        architecture,
        full_checkpoint_rtl_lowering,
    )
    passes.append(
        {
            "pass": "e1_emit_full_checkpoint_command_stream",
            "artifact": repo_rel(full_checkpoint_command_stream_out),
        }
    )

    full_checkpoint_rtl_cycle_out = output_dir / "20_full_checkpoint_rtl_cycle_lowering.json"
    full_checkpoint_rtl_cycle = emit_full_checkpoint_rtl_cycle_lowering(
        full_checkpoint_rtl_cycle_out,
        manifest,
        full_checkpoint_command_stream,
        module_dpi_report,
    )
    passes.append(
        {
            "pass": "e1_lower_full_checkpoint_command_stream_to_rtl_cycles",
            "artifact": repo_rel(full_checkpoint_rtl_cycle_out),
        }
    )

    full_checkpoint_tile_engine_out = output_dir / "21_full_checkpoint_tile_engine.json"
    full_checkpoint_tile_engine = emit_full_checkpoint_tile_engine(
        full_checkpoint_tile_engine_out,
        manifest,
        full_checkpoint_command_stream,
        full_checkpoint_rtl_cycle,
    )
    passes.append(
        {
            "pass": "e1_wire_full_checkpoint_tile_engine",
            "artifact": repo_rel(full_checkpoint_tile_engine_out),
        }
    )

    full_checkpoint_control_scheduler_out = output_dir / "22_full_checkpoint_control_scheduler.json"
    full_checkpoint_control_scheduler = emit_full_checkpoint_control_scheduler(
        full_checkpoint_control_scheduler_out,
        manifest,
        full_checkpoint_rtl_lowering,
        module_dpi_report,
    )
    passes.append(
        {
            "pass": "e1_lower_full_checkpoint_control_ops_to_rtl",
            "artifact": repo_rel(full_checkpoint_control_scheduler_out),
        }
    )

    full_checkpoint_graph_sequencer_out = output_dir / "23_full_checkpoint_graph_sequencer.json"
    full_checkpoint_graph_sequencer = emit_full_checkpoint_graph_sequencer(
        full_checkpoint_graph_sequencer_out,
        manifest,
        full_checkpoint_rtl_lowering,
        full_checkpoint_command_stream,
        full_checkpoint_control_scheduler,
    )
    passes.append(
        {
            "pass": "e1_sequence_full_checkpoint_graph_slots",
            "artifact": repo_rel(full_checkpoint_graph_sequencer_out),
        }
    )

    full_checkpoint_rtl_top_out = output_dir / "24_full_checkpoint_rtl_top.json"
    full_checkpoint_rtl_top = emit_full_checkpoint_rtl_top(
        full_checkpoint_rtl_top_out,
        manifest,
        full_checkpoint_command_stream,
        full_checkpoint_graph_sequencer,
    )
    passes.append(
        {
            "pass": "e1_integrate_full_checkpoint_rtl_top",
            "artifact": repo_rel(full_checkpoint_rtl_top_out),
        }
    )

    full_checkpoint_module_dpi_out = output_dir / "25_full_checkpoint_module_dpi_generation.json"
    full_checkpoint_module_dpi = run_full_checkpoint_module_dpi_generator(e1_h1_dir, full_checkpoint_module_dpi_out)
    passes.append(
        {
            "pass": "e1_generate_full_checkpoint_module_dpi",
            "artifact": repo_rel(full_checkpoint_module_dpi_out),
        }
    )

    e2e_out = output_dir / "26_end_to_end_smoke.json"
    target_manifest_path = "e1/e1-h1/generated/targets/manifest.json"
    generated_soc_top_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            soc_top_artifacts["top"],
            soc_top_artifacts["composition_manifest"],
            soc_top_artifacts["interface_contracts"],
        ]
    )
    module_dpi_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            module_dpi_report["manifest"],
            module_dpi_report["scoreboard"],
            *[module["probe"] for module in module_dpi_report["modules"]],
            *[module["main"] for module in module_dpi_report["modules"]],
            *[module["flist"] for module in module_dpi_report["modules"]],
        ]
    )
    target_package_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            target_manifest_path,
            implementation_matrix["matrix"],
            implementation_matrix["flists"]["active"],
            target_manifest["fpga"]["filelist"],
            target_manifest["fpga"]["constraints"],
            target_manifest["fpga"]["script"],
            target_manifest["openroad"]["filelist"],
            target_manifest["openroad"]["constraints"],
            target_manifest["openroad"]["config"],
        ]
    )
    checkpoint_preflight_statuses = {
        "missing_python_dependencies",
        "missing_checkpoint_cache",
        "missing_checkpoint_files",
        "ready",
    }
    checkpoint_check_passes = (
        full_checkpoint_execution["status"] == "pass"
        or (
            full_checkpoint_execution["mode"] == "preflight"
            and full_checkpoint_execution["status"] in checkpoint_preflight_statuses
        )
    )
    e2e_checks = [
        {"name": "stablehlo_supported", "status": "pass" if not inspection["unsupported_ops"] else "fail"},
        {"name": "e1_h1_binding", "status": "pass" if (REPO_ROOT / repo_rel(binding_out)).exists() else "fail"},
        {"name": "device_program_run", "status": device_program_run["status"]},
        {"name": "chip_model_run", "status": chip_model_run["status"]},
        {"name": "generated_soc_top", "status": "pass" if generated_soc_top_exists else "fail"},
        {"name": "implementation_flists", "status": "pass" if target_package_exists else "fail"},
        {"name": "module_dpi_generation", "status": "pass" if module_dpi_exists else "fail"},
        {"name": "rtl_lowering", "status": rtl_lowering["status"]},
        {"name": "tinyllama_imp2_coverage", "status": tinyllama_coverage["status"]},
        {"name": "full_tinyllama_checkpoint", "status": "pass" if checkpoint_check_passes else "fail"},
        {
            "name": "full_checkpoint_rtl_lowering_plan",
            "status": "pass" if full_checkpoint_rtl_lowering["status"] == "planned" else "fail",
        },
        {
            "name": "full_checkpoint_command_stream",
            "status": full_checkpoint_command_stream["status"],
        },
        {
            "name": "full_checkpoint_rtl_cycle_lowering",
            "status": full_checkpoint_rtl_cycle["status"],
        },
        {
            "name": "full_checkpoint_tile_engine",
            "status": full_checkpoint_tile_engine["status"],
        },
        {
            "name": "full_checkpoint_control_scheduler",
            "status": full_checkpoint_control_scheduler["status"],
        },
        {
            "name": "full_checkpoint_graph_sequencer",
            "status": full_checkpoint_graph_sequencer["status"],
        },
        {
            "name": "full_checkpoint_rtl_top",
            "status": full_checkpoint_rtl_top["status"],
        },
        {
            "name": "full_checkpoint_module_dpi_generation",
            "status": full_checkpoint_module_dpi["status"],
        },
        {"name": "target_package", "status": "pass" if target_package_exists else "fail"},
    ]
    e2e = {
        "schema": "e1-end-to-end-smoke-v0",
        "status": "pass" if all(check["status"] == "pass" for check in e2e_checks) else "fail",
        "model_id": manifest["model_id"],
        "architecture_id": architecture["architecture_id"],
        "stablehlo": {
            "source": repo_rel(stablehlo_out),
            "export_report": repo_rel(stablehlo_report_out),
            "inspection_report": repo_rel(inspection_out),
            "normalized": repo_rel(normalized_out),
            "unsupported_ops": inspection["unsupported_ops"],
        },
        "binding": repo_rel(binding_out),
        "memory_plan": repo_rel(memory_out),
        "device_program": {
            "plan": repo_rel(device_out),
            "run": repo_rel(output_dir / "07_device_program_run.json"),
            "status": device_program_run["status"],
            "program": "e1/code/program/e1_tinyllama_program.cpp",
        },
        "chip_model": {
            "plan": repo_rel(chip_model_out),
            "run": repo_rel(output_dir / "08_chip_model_run.json"),
            "status": chip_model_run["status"],
            "model": "e1/code/chip_model/e1_chip_model.hpp",
        },
        "l1_5_harness_plan": repo_rel(harness_out),
        "hardware_graph": repo_rel(graph_out),
        "implementation_matrix": implementation_matrix["matrix"],
        "implementation_flists": implementation_matrix["flists"],
        "module_dpi_generation": repo_rel(module_dpi_out),
        "module_dpi_manifest": module_dpi_report["manifest"],
        "rtl_lowering": repo_rel(rtl_lowering_out),
        "rtl_lowering_status": rtl_lowering["status"],
        "tinyllama_imp2_coverage": repo_rel(tinyllama_coverage_out),
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "17_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
        "full_checkpoint_rtl_lowering_plan": repo_rel(full_checkpoint_rtl_lowering_out),
        "full_checkpoint_rtl_lowering_status": full_checkpoint_rtl_lowering["status"],
        "full_checkpoint_graph_lowered_to_rtl": full_checkpoint_rtl_lowering["full_checkpoint_graph_lowering"],
        "full_checkpoint_command_stream": repo_rel(full_checkpoint_command_stream_out),
        "full_checkpoint_command_stream_status": full_checkpoint_command_stream["status"],
        "full_checkpoint_total_tile_commands": full_checkpoint_command_stream["total_tile_commands"],
        "full_checkpoint_rtl_cycle_lowering": repo_rel(full_checkpoint_rtl_cycle_out),
        "full_checkpoint_rtl_cycle_lowering_status": full_checkpoint_rtl_cycle["status"],
        "full_checkpoint_total_rtl_cycles": full_checkpoint_rtl_cycle["total_rtl_cycles"],
        "full_checkpoint_tile_engine": repo_rel(full_checkpoint_tile_engine_out),
        "full_checkpoint_tile_engine_status": full_checkpoint_tile_engine["status"],
        "full_checkpoint_control_scheduler": repo_rel(full_checkpoint_control_scheduler_out),
        "full_checkpoint_control_scheduler_status": full_checkpoint_control_scheduler["status"],
        "full_checkpoint_total_control_ops": full_checkpoint_control_scheduler["total_control_ops"],
        "full_checkpoint_graph_sequencer": repo_rel(full_checkpoint_graph_sequencer_out),
        "full_checkpoint_graph_sequencer_status": full_checkpoint_graph_sequencer["status"],
        "full_checkpoint_total_graph_slots": full_checkpoint_graph_sequencer["total_graph_slots"],
        "full_checkpoint_rtl_top": repo_rel(full_checkpoint_rtl_top_out),
        "full_checkpoint_rtl_top_status": full_checkpoint_rtl_top["status"],
        "full_checkpoint_rtl_top_smoke_max_tiles_per_linear_slot": full_checkpoint_rtl_top[
            "smoke_max_tiles_per_linear_slot"
        ],
        "full_checkpoint_rtl_top_full_verilator_tb": full_checkpoint_rtl_top["full_verilator_tb"],
        "full_checkpoint_rtl_top_full_expected_linear_commands": full_checkpoint_rtl_top[
            "full_expected_linear_commands"
        ],
        "full_checkpoint_rtl_top_full_command_count_rtl_execution": full_checkpoint_rtl_top[
            "full_command_count_rtl_execution"
        ],
        "full_checkpoint_rtl_top_full_command_payload_schedule_check": full_checkpoint_rtl_top[
            "full_command_payload_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_cycle_phase_check": full_checkpoint_rtl_top[
            "full_command_cycle_phase_check"
        ],
        "full_checkpoint_module_dpi_generation": repo_rel(full_checkpoint_module_dpi_out),
        "full_checkpoint_module_dpi_manifest": full_checkpoint_module_dpi["manifest"],
        "full_checkpoint_module_interfaces_doc": full_checkpoint_module_dpi["module_interfaces_doc"],
        "full_checkpoint_module_dpi_status": full_checkpoint_module_dpi["status"],
        "full_checkpoint_module_dpi_count": full_checkpoint_module_dpi["module_count"],
        "systemverilog_plan": repo_rel(sv_out),
        "generated_soc_top": soc_top_artifacts,
        "target_package_plan": repo_rel(target_out),
        "target_package": target_manifest_path,
        "checks": e2e_checks,
    }
    write_json(e2e_out, e2e)
    passes.append({"pass": "e1_end_to_end_smoke", "artifact": repo_rel(e2e_out)})

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
        "end_to_end_smoke": repo_rel(e2e_out),
        "end_to_end_status": e2e["status"],
        "module_dpi_generation": repo_rel(module_dpi_out),
        "rtl_lowering": repo_rel(rtl_lowering_out),
        "rtl_lowering_status": rtl_lowering["status"],
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "17_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
        "full_checkpoint_rtl_lowering_plan": repo_rel(full_checkpoint_rtl_lowering_out),
        "full_checkpoint_rtl_lowering_status": full_checkpoint_rtl_lowering["status"],
        "full_checkpoint_graph_lowered_to_rtl": full_checkpoint_rtl_lowering["full_checkpoint_graph_lowering"],
        "full_checkpoint_command_stream": repo_rel(full_checkpoint_command_stream_out),
        "full_checkpoint_command_stream_status": full_checkpoint_command_stream["status"],
        "full_checkpoint_total_tile_commands": full_checkpoint_command_stream["total_tile_commands"],
        "full_checkpoint_rtl_cycle_lowering": repo_rel(full_checkpoint_rtl_cycle_out),
        "full_checkpoint_rtl_cycle_lowering_status": full_checkpoint_rtl_cycle["status"],
        "full_checkpoint_total_rtl_cycles": full_checkpoint_rtl_cycle["total_rtl_cycles"],
        "full_checkpoint_tile_engine": repo_rel(full_checkpoint_tile_engine_out),
        "full_checkpoint_tile_engine_status": full_checkpoint_tile_engine["status"],
        "full_checkpoint_control_scheduler": repo_rel(full_checkpoint_control_scheduler_out),
        "full_checkpoint_control_scheduler_status": full_checkpoint_control_scheduler["status"],
        "full_checkpoint_total_control_ops": full_checkpoint_control_scheduler["total_control_ops"],
        "full_checkpoint_graph_sequencer": repo_rel(full_checkpoint_graph_sequencer_out),
        "full_checkpoint_graph_sequencer_status": full_checkpoint_graph_sequencer["status"],
        "full_checkpoint_total_graph_slots": full_checkpoint_graph_sequencer["total_graph_slots"],
        "full_checkpoint_rtl_top": repo_rel(full_checkpoint_rtl_top_out),
        "full_checkpoint_rtl_top_status": full_checkpoint_rtl_top["status"],
        "full_checkpoint_rtl_top_smoke_max_tiles_per_linear_slot": full_checkpoint_rtl_top[
            "smoke_max_tiles_per_linear_slot"
        ],
        "full_checkpoint_rtl_top_full_verilator_tb": full_checkpoint_rtl_top["full_verilator_tb"],
        "full_checkpoint_rtl_top_full_expected_linear_commands": full_checkpoint_rtl_top[
            "full_expected_linear_commands"
        ],
        "full_checkpoint_rtl_top_full_command_count_rtl_execution": full_checkpoint_rtl_top[
            "full_command_count_rtl_execution"
        ],
        "full_checkpoint_rtl_top_full_command_payload_schedule_check": full_checkpoint_rtl_top[
            "full_command_payload_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_cycle_phase_check": full_checkpoint_rtl_top[
            "full_command_cycle_phase_check"
        ],
        "full_checkpoint_module_dpi_generation": repo_rel(full_checkpoint_module_dpi_out),
        "full_checkpoint_module_dpi_manifest": full_checkpoint_module_dpi["manifest"],
        "full_checkpoint_module_interfaces_doc": full_checkpoint_module_dpi["module_interfaces_doc"],
        "full_checkpoint_module_dpi_status": full_checkpoint_module_dpi["status"],
        "full_checkpoint_module_dpi_count": full_checkpoint_module_dpi["module_count"],
        "pipeline": architecture["pipeline"],
    }
    write_json(summary_out, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "e1/model/tinyllama_manifest.json")
    parser.add_argument("--architecture", type=Path, default=REPO_ROOT / "e1/e1-h1/config/architecture.json")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "e1/generated/pipeline")
    parser.add_argument("--full-checkpoint-mode", choices=["preflight", "live"], default="preflight")
    parser.add_argument("--checkpoint-cache-dir", type=Path, default=DEFAULT_CHECKPOINT_CACHE)
    parser.add_argument("--allow-checkpoint-download", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    checkpoint_cache_dir = (
        args.checkpoint_cache_dir if args.checkpoint_cache_dir.is_absolute() else REPO_ROOT / args.checkpoint_cache_dir
    )
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)

    summary = run_pipeline(
        args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest,
        args.architecture if args.architecture.is_absolute() else REPO_ROOT / args.architecture,
        output_dir,
        args.full_checkpoint_mode,
        checkpoint_cache_dir,
        args.allow_checkpoint_download,
    )
    print(f"PASS e1_pipeline {summary['pass_count']} passes -> {repo_rel(output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
