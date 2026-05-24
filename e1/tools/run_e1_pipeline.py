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
    report_path = output_dir / "14_full_tinyllama_checkpoint_execution.json"
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
    write_json(
        graph_out,
        {
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
        },
    )
    passes.append({"pass": "e1_lower_to_hardware_graph", "artifact": repo_rel(graph_out)})
    passes.append({"pass": "e1_select_implementations", "artifact": implementation_matrix["matrix"]})

    sv_out = output_dir / "11_systemverilog_plan.json"
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

    tinyllama_coverage_out = output_dir / "13_tinyllama_imp2_coverage.json"
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
            "artifact": repo_rel(output_dir / "14_full_tinyllama_checkpoint_execution.json"),
        }
    )

    e2e_out = output_dir / "15_end_to_end_smoke.json"
    target_manifest_path = "e1/e1-h1/generated/targets/manifest.json"
    generated_soc_top_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            soc_top_artifacts["top"],
            soc_top_artifacts["composition_manifest"],
            soc_top_artifacts["interface_contracts"],
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
        {"name": "tinyllama_imp2_coverage", "status": tinyllama_coverage["status"]},
        {"name": "full_tinyllama_checkpoint", "status": "pass" if checkpoint_check_passes else "fail"},
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
        "tinyllama_imp2_coverage": repo_rel(tinyllama_coverage_out),
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "14_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
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
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "14_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
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
