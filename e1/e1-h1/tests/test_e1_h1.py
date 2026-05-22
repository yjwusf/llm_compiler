#!/usr/bin/env python3
"""E1-H1 tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
E1_H1 = REPO_ROOT / "e1" / "e1-h1"
ARCH = E1_H1 / "config" / "architecture.json"
IP_DIR = E1_H1 / "ip"
GENERATED_TOP = E1_H1 / "generated" / "e1_h1_soc_top.sv"
GENERATED_TOP_MANIFEST = E1_H1 / "generated" / "e1_h1_soc_top_manifest.json"
GENERATED_INTERFACE_CONTRACTS = E1_H1 / "generated" / "e1_h1_interface_contracts.json"
GENERATOR = E1_H1 / "tools" / "generate_soc_top.py"
L1_5_DIR = E1_H1 / "l1_5"
E1_PIPELINE = REPO_ROOT / "e1" / "tools" / "run_e1_pipeline.py"
E1_FETCH = REPO_ROOT / "e1" / "tools" / "fetch_tinyllama.py"
E1_EXPORT = REPO_ROOT / "e1" / "tools" / "export_stablehlo.py"
E1_PIPELINE_OUT = REPO_ROOT / "e1" / "generated" / "pipeline"
TARGETS = E1_H1 / "generated" / "targets"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_soc_top", GENERATOR)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
        **kwargs,
    )


def interface_signature_payload(interface: dict[str, object]) -> dict[str, object]:
    return {
        "name": interface["name"],
        "subsystem": interface["subsystem"],
        "parameters": interface["parameters"],
        "ports": interface["ports"],
        "perf_counters": interface["perf_counters"],
    }


def interface_signature(interface: dict[str, object]) -> str:
    payload = json.dumps(interface_signature_payload(interface), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def minimal_ip(name: str, order: int, ports: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "e1-h1-ip-v0",
        "name": name,
        "module": f"test_{name}",
        "subsystem": "cpu_subsystem",
        "order": order,
        "description": f"Test IP {name}.",
        "replaceable": True,
        "rtl": "e1/e1-h1/rtl/ip/e1_h1_control_cpu.sv",
        "spec": "e1/e1-h1/docs/modules/control_cpu.md",
        "cpp_model": "e1/code/chip_model/e1_chip_model.*",
        "l1_5_hybrid": f"test_{name}_hybrid",
        "l1_5_harness": "e1/e1-h1/l1_5/control_cpu.json",
        "perf_counters": ["cycles"],
        "ports": ports,
    }


class E1H1Tests(unittest.TestCase):
    def test_architecture_json_contract(self) -> None:
        arch = json.loads(ARCH.read_text(encoding="utf-8"))
        self.assertEqual(arch["example"], "e1")
        self.assertEqual(arch["architecture_id"], "e1-h1")
        self.assertEqual(arch["soc_top"]["style_reference"]["name"], "wujian100_open")
        self.assertEqual(arch["soc_top"]["style_reference"]["url"], "https://github.com/XUANTIE-RV/wujian100_open")
        self.assertEqual(arch["soc_top"]["generation"]["kind"], "manifest_driven")
        self.assertEqual(arch["soc_top"]["generation"]["source"], "e1/e1-h1/ip/*.json")
        self.assertEqual(
            arch["soc_top"]["generation"]["generated_interface_contracts"],
            "e1/e1-h1/generated/e1_h1_interface_contracts.json",
        )
        self.assertEqual(
            {subsystem["name"] for subsystem in arch["soc_top"]["subsystems"]},
            {"cpu_subsystem", "io_subsystem", "memory_subsystem", "accelerator_subsystem"},
        )
        self.assertEqual(arch["cpu"]["issue_width"], 3)
        self.assertTrue(arch["cpu"]["bare_metal_only"])
        self.assertTrue(arch["cpu"]["strip_linux_boot_features"])
        self.assertEqual(arch["io"]["external_data_source"]["kind"], "ethernet")
        self.assertEqual(arch["io"]["external_data_source"]["mac_interface"], "rgmii")
        self.assertTrue(arch["io"]["external_data_source"]["digital_only"])
        self.assertEqual(arch["accelerator"]["kind"], "systolic_array")
        self.assertTrue(arch["replaceability"]["required_for_every_module"])

    def test_ip_manifests_are_replaceable_and_connected(self) -> None:
        arch = json.loads(ARCH.read_text(encoding="utf-8"))
        valid_subsystems = {subsystem["name"] for subsystem in arch["soc_top"]["subsystems"]}
        manifests = sorted(IP_DIR.glob("*.json"))
        self.assertGreaterEqual(len(manifests), 6)
        seen = {path.stem for path in manifests}
        self.assertIn("control_cpu", seen)
        self.assertIn("systolic_array", seen)
        self.assertIn("rgmii_ethernet_ingress", seen)

        for path in manifests:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "e1-h1-ip-v0", path)
            self.assertTrue(data["replaceable"], path)
            self.assertIn("module", data, path)
            self.assertIn("rtl", data, path)
            self.assertIn("order", data, path)
            self.assertIn(data["subsystem"], valid_subsystems, path)
            self.assertIn("spec", data, path)
            self.assertIn("cpp_model", data, path)
            self.assertIn("l1_5_hybrid", data, path)
            self.assertIn("l1_5_harness", data, path)
            self.assertIn("perf_counters", data, path)
            self.assertTrue((REPO_ROOT / data["rtl"]).exists(), data["rtl"])
            self.assertGreater(len(data["perf_counters"]), 0, path)
            self.assertGreater(len(data["ports"]), 0, path)
            for port in data["ports"]:
                self.assertIn(port["direction"], {"input", "output", "inout"}, path)
                self.assertGreater(int(port["width"]), 0, path)
                self.assertRegex(port["connect"], r"^(top|net)\.[a-zA-Z_][a-zA-Z0-9_]*$")

    def test_ip_module_specs_cover_interfaces_and_hybrid_runs(self) -> None:
        required_sections = [
            "## Purpose",
            "## Parameters",
            "## Input Signals",
            "## Output Signals",
            "## Interface Protocol",
            "## Replacement Compatibility",
            "## Mock Behavior",
            "## C++ Model Contract",
            "## L1.5 Hybrid Execution",
            "## C++ Performance Counters",
            "## Tests",
        ]

        for manifest in sorted(IP_DIR.glob("*.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            spec_path = REPO_ROOT / data["spec"]
            self.assertTrue(spec_path.exists(), data["spec"])
            text = spec_path.read_text(encoding="utf-8")
            self.assertIn(data["module"], text, spec_path)
            self.assertIn(data["cpp_model"], text, spec_path)
            self.assertIn(data["l1_5_hybrid"], text, spec_path)
            self.assertIn(data["l1_5_harness"], text, spec_path)
            for section in required_sections:
                self.assertIn(section, text, spec_path)
            for port in data["ports"]:
                self.assertIn(f"`{port['name']}`", text, spec_path)
            for counter in data["perf_counters"]:
                self.assertIn(f"`{counter}`", text, spec_path)

    def test_l1_5_harnesses_match_ip_manifests(self) -> None:
        for manifest in sorted(IP_DIR.glob("*.json")):
            ip = json.loads(manifest.read_text(encoding="utf-8"))
            harness_path = REPO_ROOT / ip["l1_5_harness"]
            self.assertTrue(harness_path.exists(), harness_path)
            harness = json.loads(harness_path.read_text(encoding="utf-8"))
            self.assertEqual(harness["schema"], "e1-h1-l1_5-harness-v0")
            self.assertEqual(harness["ip_manifest"], str(manifest.relative_to(REPO_ROOT)))
            self.assertEqual(harness["top_module"], ip["module"])
            self.assertEqual(harness["rtl"], ip["rtl"])
            self.assertTrue((REPO_ROOT / ip["rtl"]).exists(), harness)
            self.assertTrue((REPO_ROOT / harness["cpp_testbench"]).exists(), harness)
            self.assertGreater(len(harness["cpp_environment"]), 0, harness)
            self.assertEqual(harness["perf_counters"], ip["perf_counters"])

    def test_l1_5_hybrid_runs_each_ip_individually(self) -> None:
        harnesses = sorted(L1_5_DIR.glob("*.json"))
        self.assertGreaterEqual(len(harnesses), 6)
        for harness in harnesses:
            result = run([
                "python3",
                "e1/e1-h1/tools/run_l1_5.py",
                "--harness",
                str(harness.relative_to(REPO_ROOT)),
            ])
            self.assertIn(f"PASS {harness.stem}", result.stdout)

    def test_generated_soc_top_matches_manifests(self) -> None:
        generator = load_generator()
        expected = generator.generate(ARCH, IP_DIR)
        actual = GENERATED_TOP.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)
        expected_manifest = generator.generate_composition_manifest(ARCH, IP_DIR)
        actual_manifest = json.loads(GENERATED_TOP_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(actual_manifest, expected_manifest)
        expected_interfaces = generator.generate_interface_contracts(ARCH, IP_DIR)
        actual_interfaces = json.loads(GENERATED_INTERFACE_CONTRACTS.read_text(encoding="utf-8"))
        self.assertEqual(actual_interfaces, expected_interfaces)
        self.assertIn("module e1_h1_soc_top", actual)
        self.assertIn("SoC top style: wujian100_open", actual)
        self.assertIn("SoC top reference: https://github.com/XUANTIE-RV/wujian100_open", actual)
        self.assertIn("Subsystem: cpu_subsystem", actual)
        self.assertIn("Subsystem: io_subsystem", actual)
        self.assertIn("Subsystem: memory_subsystem", actual)
        self.assertIn("Subsystem: accelerator_subsystem", actual)
        self.assertIn("u_control_cpu", actual)
        self.assertIn("u_rgmii_ethernet_ingress", actual)
        self.assertIn("u_ingress_sram", actual)
        self.assertIn("u_systolic_array", actual)
        self.assertIn("Source of composition: e1/e1-h1/ip/*.json", actual)
        self.assertEqual(actual_manifest["schema"], "e1-h1-soc-top-composition-v0")
        self.assertEqual(actual_manifest["style_reference"]["name"], "wujian100_open")
        self.assertEqual(
            [subsystem["name"] for subsystem in actual_manifest["subsystems"]],
            ["cpu_subsystem", "io_subsystem", "memory_subsystem", "accelerator_subsystem"],
        )
        self.assertIn("rgmii_rx_clk_i", {port["name"] for port in actual_manifest["top_ports"]})
        for net in actual_manifest["nets"]:
            self.assertEqual(len(net["drivers"]), 1, net["name"])
            self.assertGreaterEqual(len(net["loads"]), 1, net["name"])
            self.assertEqual(net["inouts"], [], net["name"])
            self.assertTrue(net["validation"]["single_driver"], net["name"])
            self.assertTrue(net["validation"]["has_load"], net["name"])
        self.assertEqual(actual_interfaces["schema"], "e1-h1-interface-contracts-v0")
        self.assertEqual(actual_interfaces["source"], "e1/e1-h1/ip/*.json")
        self.assertEqual(
            {item["name"] for item in actual_interfaces["interfaces"]},
            {path.stem for path in IP_DIR.glob("*.json")},
        )
        for interface in actual_interfaces["interfaces"]:
            self.assertEqual(interface["signature_sha256"], interface_signature(interface))
            self.assertRegex(interface["signature_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((REPO_ROOT / interface["spec"]).exists())
            self.assertTrue((REPO_ROOT / interface["l1_5_harness"]).exists())
            self.assertNotIn("implementation_module", interface_signature_payload(interface))

    def test_soc_top_generator_cli_emits_all_review_artifacts(self) -> None:
        generator = load_generator()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            top_out = tmp_path / "e1_h1_soc_top.sv"
            manifest_out = tmp_path / "e1_h1_soc_top_manifest.json"
            interfaces_out = tmp_path / "e1_h1_interface_contracts.json"
            run([
                "python3",
                str(GENERATOR.relative_to(REPO_ROOT)),
                "--architecture",
                str(ARCH.relative_to(REPO_ROOT)),
                "--ip-dir",
                str(IP_DIR.relative_to(REPO_ROOT)),
                "--output",
                str(top_out),
                "--manifest-output",
                str(manifest_out),
                "--interfaces-output",
                str(interfaces_out),
            ])

            self.assertEqual(top_out.read_text(encoding="utf-8"), generator.generate(ARCH, IP_DIR))
            self.assertEqual(
                json.loads(manifest_out.read_text(encoding="utf-8")),
                generator.generate_composition_manifest(ARCH, IP_DIR),
            )
            self.assertEqual(
                json.loads(interfaces_out.read_text(encoding="utf-8")),
                generator.generate_interface_contracts(ARCH, IP_DIR),
            )

    def test_soc_top_generator_rejects_bad_net_roles(self) -> None:
        generator = load_generator()
        bad_cases = {
            "multi_driver": [
                minimal_ip(
                    "driver_a",
                    10,
                    [{"name": "bus_o", "direction": "output", "width": 1, "connect": "net.bad_bus"}],
                ),
                minimal_ip(
                    "driver_b",
                    20,
                    [{"name": "bus_o", "direction": "output", "width": 1, "connect": "net.bad_bus"}],
                ),
                minimal_ip(
                    "sink",
                    30,
                    [{"name": "bus_i", "direction": "input", "width": 1, "connect": "net.bad_bus"}],
                ),
            ],
            "no_load": [
                minimal_ip(
                    "driver",
                    10,
                    [{"name": "bus_o", "direction": "output", "width": 1, "connect": "net.bad_bus"}],
                )
            ],
            "no_driver": [
                minimal_ip(
                    "sink",
                    10,
                    [{"name": "bus_i", "direction": "input", "width": 1, "connect": "net.bad_bus"}],
                )
            ],
        }

        for name, manifests in bad_cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                ip_dir = Path(tmp)
                for manifest in manifests:
                    path = ip_dir / f"{manifest['name']}.json"
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "net bad_bus"):
                    generator.generate(ARCH, ip_dir)

    def test_e1_pipeline_generates_e1_h1_artifacts(self) -> None:
        result = run(["python3", str(E1_PIPELINE.relative_to(REPO_ROOT)), "--clean"])
        self.assertIn("PASS e1_pipeline 12 passes", result.stdout)

        summary = json.loads((E1_PIPELINE_OUT / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["schema"], "e1-pipeline-summary-v0")
        self.assertEqual(summary["model_id"], "tinyllama-1.1b-chat-v1.0")
        self.assertEqual(summary["architecture_id"], "e1-h1")
        self.assertEqual(summary["pass_count"], 12)
        self.assertEqual(summary["operation_counts"]["dot_general"], 6)
        self.assertTrue(summary["all_current_modules_have_l1_5_harnesses"])
        self.assertEqual(summary["generated_top"], "e1/e1-h1/generated/e1_h1_soc_top.sv")

        expected_passes = [
            "e1_fetch_model",
            "e1_export_stablehlo",
            "e1_inspect_stablehlo",
            "e1_normalize_stablehlo",
            "e1_bind_e1_h1",
            "e1_plan_memory",
            "e1_plan_device_program",
            "e1_generate_chip_model",
            "e1_generate_l1_5_harnesses",
            "e1_lower_to_hardware_graph",
            "e1_emit_systemverilog",
            "e1_package_targets",
        ]
        self.assertEqual([entry["pass"] for entry in summary["passes"]], expected_passes)

        inspection = json.loads((E1_PIPELINE_OUT / "03_stablehlo_inspection.json").read_text(encoding="utf-8"))
        self.assertEqual(inspection["unsupported_ops"], [])
        self.assertIn("dot_general", inspection["systolic_array_ops"])
        self.assertIn("Ethernet/RGMII", inspection["answers"]["external_data_source"])

        binding = json.loads((E1_PIPELINE_OUT / "05_e1_h1_binding.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["bindings"]["stablehlo.dot_general"], "systolic_array")
        self.assertEqual(binding["bindings"]["external_data"], "rgmii_ethernet_ingress")

        hardware_graph = json.loads((E1_PIPELINE_OUT / "10_hardware_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(hardware_graph["top"], "e1_h1_soc_top")
        self.assertEqual(hardware_graph["generator"], "e1/e1-h1/tools/generate_soc_top.py")
        self.assertEqual(hardware_graph["composition_manifest"], "e1/e1-h1/generated/e1_h1_soc_top_manifest.json")
        self.assertEqual(hardware_graph["interface_contracts"], "e1/e1-h1/generated/e1_h1_interface_contracts.json")
        self.assertEqual(
            hardware_graph["subsystems"],
            ["cpu_subsystem", "io_subsystem", "memory_subsystem", "accelerator_subsystem"],
        )
        self.assertIn("systolic_array", {ip["name"] for ip in hardware_graph["ips"]})
        self.assertIn("accelerator_subsystem", {ip["subsystem"] for ip in hardware_graph["ips"]})
        for ip in hardware_graph["ips"]:
            self.assertTrue((REPO_ROOT / ip["rtl"]).exists(), ip)

        fetch = json.loads((E1_PIPELINE_OUT / "01_fetch_model.json").read_text(encoding="utf-8"))
        self.assertEqual(fetch["schema"], "e1-fetch-model-report-v0")
        self.assertEqual(fetch["source"]["repo"], "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
        self.assertEqual(fetch["source"]["revision"], "2539c747f7b95a4dac517d6620f2244efdca3543")
        self.assertEqual(fetch["command"][:4], ["hf", "download", "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "--revision"])
        self.assertFalse(fetch["large_artifacts_committed"])

        export = json.loads((E1_PIPELINE_OUT / "02_stablehlo_export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["schema"], "e1-stablehlo-export-report-v0")
        self.assertEqual(export["status"], "offline_fixture")
        self.assertEqual(export["stablehlo_out"], "e1/generated/pipeline/02_stablehlo.mlir")
        self.assertEqual(export["fixture"], "e1/fixtures/stablehlo/tinyllama_block.mlir")

        chip_model_plan = json.loads((E1_PIPELINE_OUT / "08_chip_model_plan.json").read_text(encoding="utf-8"))
        self.assertIn("e1/code/chip_model/e1_chip_smoke.cpp", chip_model_plan["chip_model"])
        self.assertEqual(chip_model_plan["run_report"], "e1/generated/pipeline/08_chip_model_run.json")
        self.assertEqual(chip_model_plan["run_status"], "pass")

        chip_model_run = json.loads((E1_PIPELINE_OUT / "08_chip_model_run.json").read_text(encoding="utf-8"))
        self.assertEqual(chip_model_run["schema"], "e1-chip-model-smoke-v0")
        self.assertEqual(chip_model_run["source"], "e1/code/chip_model/e1_chip_smoke.cpp")
        self.assertEqual(chip_model_run["status"], "pass")
        self.assertEqual(chip_model_run["program"], "first_attention_tile")
        self.assertGreater(chip_model_run["counters"]["cycles"], 0)
        self.assertEqual(chip_model_run["counters"]["instructions"], 1)
        self.assertEqual(chip_model_run["counters"]["array_commands"], 1)
        self.assertEqual(chip_model_run["counters"]["output_transfers"], 1)
        self.assertEqual(chip_model_run["counters"]["error_events"], 0)

        target_plan = json.loads((E1_PIPELINE_OUT / "12_target_package_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(target_plan["manifest"], "e1/e1-h1/generated/targets/manifest.json")
        self.assertTrue(target_plan["digital_only"])
        self.assertEqual(target_plan["fpga"]["top"], "e1_h1_soc_top")
        self.assertEqual(target_plan["asic_openroad"]["top"], "e1_h1_soc_top")
        self.assertEqual(
            target_plan["fpga"]["package"]["filelist"],
            "e1/e1-h1/generated/targets/fpga/rtl.filelist",
        )

        sv_plan = json.loads((E1_PIPELINE_OUT / "11_systemverilog_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(sv_plan["generated_top"], "e1/e1-h1/generated/e1_h1_soc_top.sv")
        self.assertEqual(sv_plan["generated_composition_manifest"], "e1/e1-h1/generated/e1_h1_soc_top_manifest.json")
        self.assertEqual(sv_plan["generated_interface_contracts"], "e1/e1-h1/generated/e1_h1_interface_contracts.json")

    def test_tinyllama_fetch_and_export_tools_have_offline_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fetch_report = tmp_path / "fetch.json"
            stablehlo_out = tmp_path / "stablehlo.mlir"
            export_report = tmp_path / "export.json"
            fetch_result = run([
                "python3",
                str(E1_FETCH.relative_to(REPO_ROOT)),
                "--mode",
                "offline",
                "--report",
                str(fetch_report),
            ])
            self.assertIn("PASS e1_fetch_model offline_fixture", fetch_result.stdout)
            export_result = run([
                "python3",
                str(E1_EXPORT.relative_to(REPO_ROOT)),
                "--mode",
                "offline",
                "--fetch-report",
                str(fetch_report),
                "--stablehlo-out",
                str(stablehlo_out),
                "--report",
                str(export_report),
            ])
            self.assertIn("PASS e1_export_stablehlo offline_fixture", export_result.stdout)
            fetch = json.loads(fetch_report.read_text(encoding="utf-8"))
            export = json.loads(export_report.read_text(encoding="utf-8"))
            self.assertEqual(fetch["schema"], "e1-fetch-model-report-v0")
            self.assertEqual(export["schema"], "e1-stablehlo-export-report-v0")
            self.assertTrue(stablehlo_out.exists())
            self.assertIn("stablehlo.dot_general", stablehlo_out.read_text(encoding="utf-8"))

    def test_target_packages_cover_fpga_and_openroad(self) -> None:
        run(["python3", str(E1_PIPELINE.relative_to(REPO_ROOT)), "--clean"])
        manifest_path = TARGETS / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "e1-h1-target-package-v0")
        self.assertEqual(manifest["top"], "e1_h1_soc_top")
        self.assertTrue(manifest["digital_only"])
        self.assertEqual(manifest["external_data_source"]["kind"], "ethernet")
        self.assertEqual(manifest["external_data_source"]["mac_interface"], "rgmii")
        self.assertEqual(manifest["rtl_source"], "e1/e1-h1/ip/*.json")

        rtl_files = manifest["rtl_files"]
        ip_manifests = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(IP_DIR.glob("*.json"))
        ]
        ordered_ip_rtl = []
        for ip in sorted(ip_manifests, key=lambda item: (item["order"], item["name"])):
            if ip["rtl"] not in ordered_ip_rtl:
                ordered_ip_rtl.append(ip["rtl"])
        expected_rtl_files = ["e1/e1-h1/generated/e1_h1_soc_top.sv", *ordered_ip_rtl]
        self.assertEqual(rtl_files, expected_rtl_files)
        self.assertEqual(
            [entry["name"] for entry in manifest["ip_rtl"]],
            [ip["name"] for ip in sorted(ip_manifests, key=lambda item: (item["order"], item["name"]))],
        )
        self.assertEqual(
            [entry["rtl"] for entry in manifest["ip_rtl"]],
            [ip["rtl"] for ip in sorted(ip_manifests, key=lambda item: (item["order"], item["name"]))],
        )
        self.assertIn("e1/e1-h1/rtl/ip/e1_h1_systolic_array.sv", rtl_files)
        self.assertEqual(len(rtl_files), len(set(rtl_files)))
        for rtl_file in rtl_files:
            self.assertTrue((REPO_ROOT / rtl_file).exists(), rtl_file)

        fpga_filelist = (REPO_ROOT / manifest["fpga"]["filelist"]).read_text(encoding="utf-8").splitlines()
        openroad_filelist = (REPO_ROOT / manifest["openroad"]["filelist"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(fpga_filelist, rtl_files)
        self.assertEqual(openroad_filelist, rtl_files)

        fpga_constraints = (REPO_ROOT / manifest["fpga"]["constraints"]).read_text(encoding="utf-8")
        openroad_constraints = (REPO_ROOT / manifest["openroad"]["constraints"]).read_text(encoding="utf-8")
        openroad_config = (REPO_ROOT / manifest["openroad"]["config"]).read_text(encoding="utf-8")
        self.assertIn("rgmii_rx_clk_i", fpga_constraints)
        self.assertIn("Digital-only RGMII", fpga_constraints)
        self.assertIn("No mixed-signal PHY", openroad_constraints)
        self.assertIn("DESIGN_NAME := e1_h1_soc_top", openroad_config)

    def test_verilator_lints_generated_top_and_mock_ips(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for E1-H1 RTL lint")
        manifest = json.loads((TARGETS / "manifest.json").read_text(encoding="utf-8"))
        rtl_files = manifest["rtl_files"]
        cmd = [
            verilator,
            "--lint-only",
            "--sv",
            "-Wall",
            "-Wno-DECLFILENAME",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "-Wno-MULTITOP",
            *rtl_files,
        ]
        run(cmd)

    def test_cpp_chip_model_compiles_and_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            test_cpp = tmp_path / "test_e1_chip_model.cpp"
            exe = tmp_path / "test_e1_chip_model"
            test_cpp.write_text(
                textwrap.dedent(
                    """
                    #include "e1_chip_model.hpp"
                    #include <cassert>
                    #include <cstdint>
                    #include <vector>

                    int main() {
                      e1::ChipModel chip;
                      chip.reset();
                      chip.load_ethernet_payload({1, 2, 3, 4});
                      chip.write_array_command({
                          0x00010000u,
                          0x00040000u,
                          0x00080000u,
                          16,
                          16,
                          16,
                      });
                      chip.run_until_idle(1024);
                      const e1::PerfCounters& counters = chip.counters();
                      assert(counters.cycles > 0);
                      assert(counters.instructions == 1);
                      assert(counters.array_commands == 1);
                      assert(counters.output_transfers == 1);
                      assert(counters.error_events == 0);
                      return 0;
                    }
                    """
                ),
                encoding="utf-8",
            )
            run(
                [
                    "c++",
                    "-std=c++17",
                    "-I",
                    "e1/code/chip_model",
                    "e1/code/chip_model/e1_chip_model.cpp",
                    str(test_cpp),
                    "-o",
                    str(exe),
                ]
            )
            run([str(exe)])

    def test_device_program_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "e1_tinyllama_program.o"
            run(
                [
                    "c++",
                    "-std=c++17",
                    "-I",
                    "e1/code/program",
                    "-c",
                    "e1/code/program/e1_tinyllama_program.cpp",
                    "-o",
                    str(obj),
                ]
            )
            self.assertTrue(obj.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
