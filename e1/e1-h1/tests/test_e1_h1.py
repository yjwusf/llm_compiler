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
SOC_TOP_TB = E1_H1 / "tests" / "e1_h1_soc_top_tb.cpp"


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
        "cpp_model": "e1/e1-h1/cmodels/control_cpu.json",
        "l1_5_hybrid": f"test_{name}_hybrid",
        "l1_5_harness": "e1/e1-h1/l1_5/control_cpu.json",
        "module_vip": "e1/e1-h1/vip/control_cpu.json",
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
            self.assertIn("module_vip", data, path)
            self.assertIn("perf_counters", data, path)
            self.assertTrue((REPO_ROOT / data["rtl"]).exists(), data["rtl"])
            self.assertTrue((REPO_ROOT / data["cpp_model"]).exists(), data["cpp_model"])
            self.assertTrue((REPO_ROOT / data["module_vip"]).exists(), data["module_vip"])
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
            "## Module VIP",
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
            self.assertIn(data["module_vip"], text, spec_path)
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
            self.assertEqual(harness["module_vip"], ip["module_vip"])
            self.assertTrue((REPO_ROOT / ip["rtl"]).exists(), harness)
            self.assertTrue((REPO_ROOT / harness["cpp_testbench"]).exists(), harness)
            self.assertTrue((REPO_ROOT / harness["module_vip"]).exists(), harness)
            self.assertGreater(len(harness["cpp_environment"]), 0, harness)
            self.assertEqual(harness["perf_counters"], ip["perf_counters"])

    def test_module_vips_are_single_dut_contracts(self) -> None:
        for manifest in sorted(IP_DIR.glob("*.json")):
            ip = json.loads(manifest.read_text(encoding="utf-8"))
            harness = json.loads((REPO_ROOT / ip["l1_5_harness"]).read_text(encoding="utf-8"))
            vip_path = REPO_ROOT / ip["module_vip"]
            vip = json.loads(vip_path.read_text(encoding="utf-8"))
            self.assertEqual(vip["schema"], "e1-h1-module-vip-v0", vip_path)
            self.assertEqual(vip["name"], ip["name"], vip_path)
            self.assertEqual(vip["ip_manifest"], str(manifest.relative_to(REPO_ROOT)), vip_path)
            self.assertEqual(vip["top_module"], ip["module"], vip_path)
            self.assertEqual(vip["rtl"], ip["rtl"], vip_path)
            self.assertEqual(vip["cpp_testbench"], harness["cpp_testbench"], vip_path)
            self.assertEqual(vip["cpp_environment"], harness["cpp_environment"], vip_path)
            self.assertEqual(vip["perf_counters"], ip["perf_counters"], vip_path)
            self.assertEqual(vip["scope"]["kind"], "module_only", vip_path)
            self.assertEqual(vip["scope"]["allowed_systemverilog_modules"], [ip["module"]], vip_path)
            self.assertEqual(vip["scope"]["neighbors"], "cpp_environment", vip_path)

    def test_cpp_models_match_ip_manifests(self) -> None:
        for manifest in sorted(IP_DIR.glob("*.json")):
            ip = json.loads(manifest.read_text(encoding="utf-8"))
            cmodel_path = REPO_ROOT / ip["cpp_model"]
            cmodel = json.loads(cmodel_path.read_text(encoding="utf-8"))
            self.assertEqual(cmodel["schema"], "e1-h1-cpp-model-v0", cmodel_path)
            self.assertEqual(cmodel["name"], ip["name"], cmodel_path)
            self.assertEqual(cmodel["ip_manifest"], str(manifest.relative_to(REPO_ROOT)), cmodel_path)
            self.assertTrue((REPO_ROOT / cmodel["header"]).exists(), cmodel_path)
            self.assertTrue((REPO_ROOT / cmodel["source"]).exists(), cmodel_path)
            self.assertGreater(len(cmodel["inputs"]), 0, cmodel_path)
            self.assertGreater(len(cmodel["outputs"]), 0, cmodel_path)
            self.assertEqual(cmodel["perf_counters"], ip["perf_counters"], cmodel_path)
            header = (REPO_ROOT / cmodel["header"]).read_text(encoding="utf-8")
            source = (REPO_ROOT / cmodel["source"]).read_text(encoding="utf-8")
            class_name = cmodel["class"].split("::")[-1]
            self.assertIn(class_name, header, cmodel_path)
            self.assertIn(f"{class_name}::", source, cmodel_path)
            for counter in cmodel["perf_counters"]:
                self.assertIn(counter, header, cmodel_path)

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
        self.assertIn("Pipeline source: e1/e1-h1/config/architecture.json", actual)
        self.assertIn("cpu_to_accelerator_valid_q_0", actual)
        self.assertIn("array_input_valid_q_1", actual)
        self.assertIn("array_output_array_done_q_1", actual)
        self.assertEqual(actual_manifest["schema"], "e1-h1-soc-top-composition-v0")
        self.assertEqual(actual_manifest["style_reference"]["name"], "wujian100_open")
        self.assertEqual(
            {entry["status"] for entry in actual_manifest["rtl_validation"]},
            {"pass"},
        )
        self.assertEqual(actual_manifest["architecture_validation"]["schema"], "e1-h1-architecture-validation-v0")
        self.assertEqual(
            {entry["status"] for entry in actual_manifest["architecture_validation"]["checks"]},
            {"pass"},
        )
        self.assertEqual(
            {entry["name"] for entry in actual_manifest["architecture_validation"]["checks"]},
            {"ingress_sram", "activation_sram", "accumulator_sram", "systolic_array"},
        )
        self.assertEqual(actual_manifest["pipeline_validation"]["schema"], "e1-h1-pipeline-validation-v0")
        pipeline_depths = {
            entry["name"]: entry["depth"]
            for entry in actual_manifest["pipeline_validation"]["checks"]
        }
        self.assertEqual(
            pipeline_depths,
            {
                "cpu_to_accelerator": 1,
                "array_input": 2,
                "array_output": 2,
            },
        )
        self.assertEqual(
            {entry["status"] for entry in actual_manifest["pipeline_validation"]["checks"]},
            {"pass"},
        )
        self.assertEqual(
            [subsystem["name"] for subsystem in actual_manifest["subsystems"]],
            ["cpu_subsystem", "io_subsystem", "memory_subsystem", "accelerator_subsystem"],
        )
        self.assertIn("rgmii_rx_clk_i", {port["name"] for port in actual_manifest["top_ports"]})
        for port in actual_manifest["top_ports"]:
            if port["direction"] == "input":
                self.assertEqual(port["drivers"], [], port["name"])
                self.assertGreaterEqual(len(port["loads"]), 1, port["name"])
                self.assertTrue(port["validation"]["has_input_load"], port["name"])
            elif port["direction"] == "output":
                self.assertEqual(len(port["drivers"]), 1, port["name"])
                self.assertEqual(port["loads"], [], port["name"])
                self.assertTrue(port["validation"]["single_output_driver"], port["name"])
            self.assertEqual(port["inouts"], [], port["name"])
        for net in actual_manifest["nets"]:
            self.assertEqual(len(net["drivers"]), 1, net["name"])
            self.assertGreaterEqual(len(net["loads"]), 1, net["name"])
            self.assertEqual(net["inouts"], [], net["name"])
            self.assertTrue(net["validation"]["single_driver"], net["name"])
            self.assertTrue(net["validation"]["has_load"], net["name"])
        self.assertEqual(actual_interfaces["schema"], "e1-h1-interface-contracts-v0")
        self.assertEqual(actual_interfaces["architecture_validation"], actual_manifest["architecture_validation"])
        self.assertEqual(actual_interfaces["pipeline_validation"], actual_manifest["pipeline_validation"])
        self.assertEqual(actual_interfaces["source"], "e1/e1-h1/ip/*.json")
        self.assertEqual(
            {item["name"] for item in actual_interfaces["interfaces"]},
            {path.stem for path in IP_DIR.glob("*.json")},
        )
        for interface in actual_interfaces["interfaces"]:
            self.assertEqual(interface["signature_sha256"], interface_signature(interface))
            self.assertRegex(interface["signature_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((REPO_ROOT / interface["spec"]).exists())
            self.assertTrue((REPO_ROOT / interface["cpp_model"]).exists())
            self.assertTrue((REPO_ROOT / interface["l1_5_harness"]).exists())
            self.assertTrue((REPO_ROOT / interface["module_vip"]).exists())
            self.assertEqual(interface["rtl_validation"]["status"], "pass")
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

    def test_soc_top_generator_pipeline_depth_variants_lint(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for E1-H1 pipeline variant lint")
        generator = load_generator()
        ip_rtl = []
        for manifest in sorted(IP_DIR.glob("*.json")):
            ip = json.loads(manifest.read_text(encoding="utf-8"))
            if ip["rtl"] not in ip_rtl:
                ip_rtl.append(ip["rtl"])

        variants = {
            "shallow": {
                "cpu_to_accelerator_depth": 0,
                "array_input_depth": 0,
                "array_output_depth": 0,
            },
            "deep": {
                "cpu_to_accelerator_depth": 2,
                "array_input_depth": 3,
                "array_output_depth": 4,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name, pipeline in variants.items():
                arch = json.loads(ARCH.read_text(encoding="utf-8"))
                arch["pipeline"].update(pipeline)
                arch_handle = tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=ARCH.parent,
                    prefix=f".{name}_",
                    suffix=".json",
                    delete=False,
                )
                arch_path = Path(arch_handle.name)
                try:
                    json.dump(arch, arch_handle, indent=2)
                    arch_handle.write("\n")
                    arch_handle.close()
                    top_text = generator.generate(arch_path, IP_DIR)
                    manifest = generator.generate_composition_manifest(arch_path, IP_DIR)
                finally:
                    if not arch_handle.closed:
                        arch_handle.close()
                    arch_path.unlink(missing_ok=True)

                depths = {entry["name"]: entry["depth"] for entry in manifest["pipeline_validation"]["checks"]}
                if name == "shallow":
                    self.assertEqual(depths, {"cpu_to_accelerator": 0, "array_input": 0, "array_output": 0})
                    self.assertIn(".cmd_valid_i(accel_cmd_valid)", top_text)
                    self.assertNotIn("_valid_q_0", top_text)
                else:
                    self.assertEqual(depths, {"cpu_to_accelerator": 2, "array_input": 3, "array_output": 4})
                    self.assertIn("cpu_to_accelerator_valid_q_1", top_text)
                    self.assertIn("array_input_valid_q_2", top_text)
                    self.assertIn("array_output_array_done_q_3", top_text)

                top_out = tmp_path / f"{name}_e1_h1_soc_top.sv"
                top_out.write_text(top_text, encoding="utf-8")
                run([
                    verilator,
                    "--lint-only",
                    "--sv",
                    "-Wall",
                    "-Wno-DECLFILENAME",
                    "-Wno-UNUSEDSIGNAL",
                    "-Wno-UNUSEDPARAM",
                    "-Wno-MULTITOP",
                    "--top-module",
                    "e1_h1_soc_top",
                    str(top_out),
                    *ip_rtl,
                ])

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

    def test_soc_top_generator_rejects_bad_top_port_roles(self) -> None:
        generator = load_generator()
        manifests = [
            minimal_ip(
                "driver_a",
                10,
                [{"name": "debug_o", "direction": "output", "width": 1, "connect": "top.debug_o"}],
            ),
            minimal_ip(
                "driver_b",
                20,
                [{"name": "debug_o", "direction": "output", "width": 1, "connect": "top.debug_o"}],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ip_dir = Path(tmp)
            for manifest in manifests:
                path = ip_dir / f"{manifest['name']}.json"
                path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "top port debug_o"):
                generator.generate(ARCH, ip_dir)

    def test_soc_top_generator_rejects_rtl_manifest_mismatch(self) -> None:
        generator = load_generator()
        manifest = minimal_ip(
            "bad_cpu",
            10,
            [{"name": "missing_i", "direction": "input", "width": 1, "connect": "top.missing_i"}],
        )
        manifest["module"] = "e1_h1_control_cpu"
        manifest["rtl"] = "e1/e1-h1/rtl/ip/e1_h1_control_cpu.sv"
        with tempfile.TemporaryDirectory() as tmp:
            ip_dir = Path(tmp)
            (ip_dir / "bad_cpu.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing ports"):
                generator.generate(ARCH, ip_dir)

    def test_soc_top_generator_rejects_architecture_parameter_drift(self) -> None:
        generator = load_generator()
        bad_cases = {
            "ingress_sram": ("ingress_sram.json", ("parameters", "SIZE_BYTES"), 1),
            "systolic_array": ("systolic_array.json", ("parameters", "ROWS"), 8),
        }

        for name, (filename, key_path, value) in bad_cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                ip_dir = Path(tmp)
                for manifest in IP_DIR.glob("*.json"):
                    (ip_dir / manifest.name).write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

                data = json.loads((ip_dir / filename).read_text(encoding="utf-8"))
                cursor = data
                for key in key_path[:-1]:
                    cursor = cursor[key]
                cursor[key_path[-1]] = value
                (ip_dir / filename).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

                with self.assertRaisesRegex(ValueError, f"{name}: architecture"):
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
        self.assertEqual(summary["pipeline"]["cpu_to_accelerator_depth"], 1)
        self.assertEqual(summary["pipeline"]["array_input_depth"], 2)
        self.assertEqual(summary["pipeline"]["array_output_depth"], 2)

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

        memory_plan = json.loads((E1_PIPELINE_OUT / "06_memory_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(memory_plan["pipeline"], summary["pipeline"])

        hardware_graph = json.loads((E1_PIPELINE_OUT / "10_hardware_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(hardware_graph["top"], "e1_h1_soc_top")
        self.assertEqual(hardware_graph["generator"], "e1/e1-h1/tools/generate_soc_top.py")
        self.assertEqual(hardware_graph["composition_manifest"], "e1/e1-h1/generated/e1_h1_soc_top_manifest.json")
        self.assertEqual(hardware_graph["interface_contracts"], "e1/e1-h1/generated/e1_h1_interface_contracts.json")
        self.assertEqual(hardware_graph["pipeline"], summary["pipeline"])
        self.assertEqual(
            hardware_graph["subsystems"],
            ["cpu_subsystem", "io_subsystem", "memory_subsystem", "accelerator_subsystem"],
        )
        self.assertIn("systolic_array", {ip["name"] for ip in hardware_graph["ips"]})
        self.assertIn("accelerator_subsystem", {ip["subsystem"] for ip in hardware_graph["ips"]})
        for ip in hardware_graph["ips"]:
            self.assertTrue((REPO_ROOT / ip["rtl"]).exists(), ip)
            self.assertTrue((REPO_ROOT / ip["cpp_model"]).exists(), ip)
            self.assertTrue((REPO_ROOT / ip["module_vip"]).exists(), ip)

        harness_plan = json.loads((E1_PIPELINE_OUT / "09_l1_5_harness_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(harness_plan["module_vips"]),
            {path.stem for path in IP_DIR.glob("*.json")},
        )
        for path in harness_plan["module_vips"].values():
            self.assertTrue((REPO_ROOT / path).exists(), path)

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
        self.assertEqual(
            set(chip_model_plan["c_model_manifests"]),
            {path.stem for path in IP_DIR.glob("*.json")},
        )
        for path in chip_model_plan["c_model_manifests"].values():
            self.assertTrue((REPO_ROOT / path).exists(), path)
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
        self.assertEqual(chip_model_run["counters"]["frames_seen"], 1)
        self.assertGreater(chip_model_run["counters"]["rgmii_rx_cycles"], 0)

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
        self.assertEqual(sv_plan["pipeline_source"], "e1/e1-h1/config/architecture.json")
        self.assertEqual(sv_plan["pipeline"], summary["pipeline"])

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

    def test_verilator_runs_generated_soc_top_smoke(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for E1-H1 SoC top smoke")
        manifest = json.loads((TARGETS / "manifest.json").read_text(encoding="utf-8"))
        rtl_files = manifest["rtl_files"]
        with tempfile.TemporaryDirectory() as tmp:
            obj_dir = Path(tmp) / "obj_dir"
            run([
                verilator,
                "--cc",
                "--exe",
                "--build",
                "--sv",
                "-Wall",
                "-Wno-DECLFILENAME",
                "-Wno-UNUSEDSIGNAL",
                "-Wno-UNUSEDPARAM",
                "-Wno-MULTITOP",
                "--top-module",
                "e1_h1_soc_top",
                "-Mdir",
                str(obj_dir),
                *rtl_files,
                str(SOC_TOP_TB.relative_to(REPO_ROOT)),
            ])
            run([str(obj_dir / "Ve1_h1_soc_top")])

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
                      e1::PerfCounters module_counters;
                      e1::ControlCpuModel cpu;
                      cpu.reset();
                      cpu.tick(false, false, false, module_counters);
                      assert(cpu.command_valid());
                      cpu.tick(true, false, false, module_counters);
                      cpu.tick(false, true, false, module_counters);
                      assert(cpu.halted());

                      e1::StreamSramModel ingress_sram;
                      e1::RgmiiEthernetIngressModel rgmii;
                      ingress_sram.reset();
                      rgmii.reset();
                      rgmii.load_payload({0xaa, 0xbb}, module_counters);
                      rgmii.tick(ingress_sram, module_counters);
                      ingress_sram.tick(module_counters);

                      e1::ConfigSramModel activation(524288, 128, 8);
                      activation.reset();
                      activation.tick(module_counters);
                      assert(activation.initialized());
                      assert(activation.data_width() == 128);

                      e1::SystolicArrayModel array;
                      array.submit({0x10000u, 0x40000u, 0x80000u, 16, 16, 16});
                      array.tick(module_counters);
                      assert(array.busy());

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
