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
E1_CHECKPOINT = REPO_ROOT / "e1" / "tools" / "run_tinyllama_checkpoint.py"
MODULE_DPI_VERILATOR_RUNNER = REPO_ROOT / "e1" / "tools" / "run_module_dpi_verilator.py"
E1_PIPELINE_OUT = REPO_ROOT / "e1" / "generated" / "pipeline"
TARGETS = E1_H1 / "generated" / "targets"
IMPLEMENTATION_MATRIX = E1_H1 / "generated" / "implementation_matrix.json"
IMPLEMENTATION_FLISTS = E1_H1 / "generated" / "flists"
SOC_TOP_TB = E1_H1 / "tests" / "e1_h1_soc_top_tb.cpp"
DPI_PROBE = E1_H1 / "dpi" / "e1_h1_imp_equiv_probe.sv"
DPI_REF = E1_H1 / "dpi" / "e1_h1_imp1_reference.sv"
DPI_SCOREBOARD = E1_H1 / "dpi" / "e1_h1_imp_equiv_dpi.cpp"
DPI_MAIN = E1_H1 / "dpi" / "e1_h1_imp_equiv_main.cpp"
MODULE_DPI_GENERATOR = E1_H1 / "tools" / "generate_module_dpi.cpp"
MODULE_DPI_DIR = E1_H1 / "generated" / "module_dpi"
MODULE_DPI_MANIFEST = MODULE_DPI_DIR / "manifest.json"
MODULE_DPI_ISOLATION = MODULE_DPI_DIR / "module_isolation.json"
MODULE_DPI_CYCLE_CONTRACT = MODULE_DPI_DIR / "cycle_contract.json"
MODULE_DPI_TEST_PLAN = MODULE_DPI_DIR / "module_test_plan.json"
MODULE_DPI_VERILATOR_RECIPE = MODULE_DPI_DIR / "verilator_execution_recipe.json"
MODULE_DPI_VERILATOR_EXECUTION = MODULE_DPI_DIR / "verilator_execution_report.json"
MODULE_DPI_README_CYCLE_COVERAGE = MODULE_DPI_DIR / "readme_cycle_coverage.json"
FULL_CHECKPOINT_GENERATED = E1_H1 / "generated" / "full_checkpoint"
FULL_CHECKPOINT_MODULE_DPI_GENERATOR = E1_H1 / "tools" / "generate_full_checkpoint_module_dpi.cpp"
FULL_CHECKPOINT_MODULE_DPI_DIR = E1_H1 / "generated" / "full_checkpoint_dpi"
FULL_CHECKPOINT_MODULE_DPI_MANIFEST = FULL_CHECKPOINT_MODULE_DPI_DIR / "manifest.json"
FULL_CHECKPOINT_MODULE_INTERFACES = FULL_CHECKPOINT_MODULE_DPI_DIR / "module_interfaces.md"
FULL_CHECKPOINT_MODULE_ISOLATION = FULL_CHECKPOINT_MODULE_DPI_DIR / "module_isolation.json"
FULL_CHECKPOINT_CYCLE_CONTRACT = FULL_CHECKPOINT_MODULE_DPI_DIR / "cycle_contract.json"
FULL_CHECKPOINT_MODULE_TEST_PLAN = FULL_CHECKPOINT_MODULE_DPI_DIR / "module_test_plan.json"
FULL_CHECKPOINT_MODULE_VERILATOR_RECIPE = FULL_CHECKPOINT_MODULE_DPI_DIR / "verilator_execution_recipe.json"
FULL_CHECKPOINT_MODULE_VERILATOR_EXECUTION = FULL_CHECKPOINT_MODULE_DPI_DIR / "verilator_execution_report.json"
FULL_CHECKPOINT_README_CYCLE_COVERAGE = FULL_CHECKPOINT_MODULE_DPI_DIR / "readme_cycle_coverage.json"


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


def regenerate_module_dpi(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    exe = tmp_path / "e1_h1_generate_module_dpi"
    run([
        "c++",
        "-std=c++17",
        str(MODULE_DPI_GENERATOR.relative_to(REPO_ROOT)),
        "-o",
        str(exe),
    ])
    return run([
        str(exe),
        "--repo-root",
        str(REPO_ROOT),
        "--output-dir",
        str(MODULE_DPI_DIR),
    ])


def regenerate_full_checkpoint_module_dpi(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    exe = tmp_path / "e1_h1_generate_full_checkpoint_module_dpi"
    run([
        "c++",
        "-std=c++17",
        str(FULL_CHECKPOINT_MODULE_DPI_GENERATOR.relative_to(REPO_ROOT)),
        "-o",
        str(exe),
    ])
    return run([
        str(exe),
        "--repo-root",
        str(REPO_ROOT),
        "--output-dir",
        str(FULL_CHECKPOINT_MODULE_DPI_DIR),
    ])


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
            self.assertIn("implementation_scheme", data, path)
            self.assertTrue((REPO_ROOT / data["rtl"]).exists(), data["rtl"])
            self.assertTrue((REPO_ROOT / data["cpp_model"]).exists(), data["cpp_model"])
            self.assertTrue((REPO_ROOT / data["module_vip"]).exists(), data["module_vip"])
            scheme = data["implementation_scheme"]
            self.assertEqual(scheme["active"], "imp2", path)
            self.assertEqual(scheme["reference"], "imp1", path)
            self.assertEqual(set(scheme["implementations"]), {"imp1", "imp2"}, path)
            imp1 = scheme["implementations"]["imp1"]
            imp2 = scheme["implementations"]["imp2"]
            self.assertEqual(imp1["kind"], "mock", path)
            self.assertEqual(imp1["status"], "accepted", path)
            self.assertEqual(imp1["module"], data["module"], path)
            self.assertIn("/rtl/ip/", imp1["rtl"], path)
            self.assertEqual(imp1["rtl_files"], [imp1["rtl"]], path)
            self.assertTrue((REPO_ROOT / imp1["rtl"]).exists(), path)
            self.assertEqual(imp2["kind"], "candidate_rtl", path)
            self.assertEqual(imp2["status"], "accepted", path)
            self.assertEqual(imp2["module"], data["module"], path)
            self.assertEqual(imp2["rtl"], data["rtl"], path)
            self.assertEqual(imp2["rtl_files"], [data["rtl"]], path)
            self.assertIn("/rtl/imp2/", data["rtl"], path)
            self.assertEqual(imp2["acceptance"], "verilator_dpi_vip_equivalent_to_imp1", path)
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
            "## Implementation Versions",
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
            self.assertIn("`imp1`", text, spec_path)
            self.assertIn("`imp2`", text, spec_path)
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
            self.assertIn("dpi_equivalence", vip, vip_path)
            dpi = vip["dpi_equivalence"]
            self.assertEqual(dpi["schema"], "e1-h1-dpi-equivalence-v0", vip_path)
            self.assertEqual(dpi["reference_implementation"], "imp1", vip_path)
            self.assertEqual(dpi["candidate_implementation"], "imp2", vip_path)
            self.assertEqual(dpi["probe"], "e1/e1-h1/dpi/e1_h1_imp_equiv_probe.sv", vip_path)
            self.assertEqual(dpi["scoreboard"], "e1/e1-h1/dpi/e1_h1_imp_equiv_dpi.cpp", vip_path)
            self.assertTrue((REPO_ROOT / dpi["probe"]).exists(), vip_path)
            self.assertTrue((REPO_ROOT / dpi["scoreboard"]).exists(), vip_path)
            self.assertEqual(dpi["module_generator"], "e1/e1-h1/tools/generate_module_dpi.cpp", vip_path)
            self.assertEqual(dpi["module_manifest"], "e1/e1-h1/generated/module_dpi/manifest.json", vip_path)
            self.assertEqual(
                dpi["module_probe"],
                f"e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_{ip['name']}.sv",
                vip_path,
            )
            self.assertEqual(
                dpi["module_main"],
                f"e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_{ip['name']}_main.cpp",
                vip_path,
            )
            self.assertEqual(dpi["module_flist"], f"e1/e1-h1/generated/module_dpi/flists/{ip['name']}.f", vip_path)
            self.assertEqual(
                dpi["module_scoreboard"],
                "e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_scoreboard.cpp",
                vip_path,
            )
            for key in [
                "module_generator",
                "module_manifest",
                "module_probe",
                "module_main",
                "module_flist",
                "module_scoreboard",
            ]:
                self.assertTrue((REPO_ROOT / dpi[key]).exists(), (vip_path, key))
            self.assertEqual(dpi["stream_space"]["kind"], "sensible_bounded", vip_path)
            self.assertGreaterEqual(len(dpi["stream_space"]["cases"]), 3, vip_path)

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

    def test_dpi_equivalence_probe_runs_under_verilator(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for E1-H1 DPI smoke")
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
                "-Wno-WIDTHEXPAND",
                "--timing",
                "--top-module",
                "e1_h1_imp_equiv_probe",
                "-Mdir",
                str(obj_dir),
                str(DPI_REF.relative_to(REPO_ROOT)),
                "e1/e1-h1/rtl/imp2/e1_h1_control_cpu.sv",
                "e1/e1-h1/rtl/imp2/e1_h1_rgmii_ethernet_ingress.sv",
                "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv",
                "e1/e1-h1/rtl/imp2/e1_h1_config_sram.sv",
                "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv",
                str(DPI_PROBE.relative_to(REPO_ROOT)),
                str(DPI_SCOREBOARD.relative_to(REPO_ROOT)),
                str(DPI_MAIN.relative_to(REPO_ROOT)),
            ])
            run([str(obj_dir / "Ve1_h1_imp_equiv_probe")])

    def test_module_dpi_generator_outputs_separated_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = regenerate_module_dpi(Path(tmp))
        self.assertIn("PASS e1_h1_generate_module_dpi 6 modules", result.stdout)

        manifest = json.loads(MODULE_DPI_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "e1-h1-generated-module-dpi-v0")
        self.assertEqual(manifest["generator"], "e1/e1-h1/tools/generate_module_dpi.cpp")
        self.assertEqual(manifest["reference_implementation"], "imp1")
        self.assertEqual(manifest["candidate_implementation"], "imp2")
        self.assertEqual(manifest["scoreboard"], "e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_scoreboard.cpp")
        self.assertEqual(manifest["module_isolation_proof"], "e1/e1-h1/generated/module_dpi/module_isolation.json")
        self.assertEqual(manifest["cycle_contract"], "e1/e1-h1/generated/module_dpi/cycle_contract.json")
        self.assertEqual(manifest["module_test_plan"], "e1/e1-h1/generated/module_dpi/module_test_plan.json")
        self.assertEqual(
            manifest["verilator_execution_recipe"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            manifest["verilator_execution_report"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            manifest["readme_cycle_coverage"],
            "e1/e1-h1/generated/module_dpi/readme_cycle_coverage.json",
        )
        self.assertIn("one_generated_probe_per_ip", manifest["construction_rule"])
        self.assertIn("CPU command issue is tested without the systolic array RTL", manifest["separation_of_concerns"]["control_cpu"])
        self.assertIn("The array is tested without CPU RTL", manifest["separation_of_concerns"]["systolic_array"])
        self.assertIn("latched boundary", manifest["separation_of_concerns"]["ingress_sram"])
        self.assertTrue(MODULE_DPI_ISOLATION.exists())
        self.assertTrue(MODULE_DPI_CYCLE_CONTRACT.exists())
        self.assertTrue(MODULE_DPI_TEST_PLAN.exists())
        self.assertTrue(MODULE_DPI_VERILATOR_RECIPE.exists())
        self.assertTrue(MODULE_DPI_VERILATOR_EXECUTION.exists())
        self.assertTrue(MODULE_DPI_README_CYCLE_COVERAGE.exists())
        isolation = json.loads(MODULE_DPI_ISOLATION.read_text(encoding="utf-8"))
        cycle_contract = json.loads(MODULE_DPI_CYCLE_CONTRACT.read_text(encoding="utf-8"))
        test_plan = json.loads(MODULE_DPI_TEST_PLAN.read_text(encoding="utf-8"))
        verilator_recipe = json.loads(MODULE_DPI_VERILATOR_RECIPE.read_text(encoding="utf-8"))
        verilator_execution = json.loads(MODULE_DPI_VERILATOR_EXECUTION.read_text(encoding="utf-8"))
        readme_cycle_coverage = json.loads(MODULE_DPI_README_CYCLE_COVERAGE.read_text(encoding="utf-8"))
        readme = (E1_H1 / "docs" / "modules" / "README.md").read_text(encoding="utf-8")
        self.assertEqual(isolation["schema"], "e1-h1-module-dpi-isolation-v0")
        self.assertEqual(cycle_contract["schema"], "e1-h1-module-dpi-cycle-contract-v0")
        self.assertEqual(test_plan["schema"], "e1-h1-module-dpi-test-plan-v0")
        self.assertEqual(verilator_recipe["schema"], "e1-h1-module-dpi-verilator-execution-recipe-v0")
        self.assertEqual(verilator_recipe["runner"], "e1/tools/run_module_dpi_verilator.py")
        self.assertEqual(verilator_recipe["suite"], "module_dpi")
        self.assertEqual(verilator_recipe["test_plan"], manifest["module_test_plan"])
        self.assertEqual(verilator_recipe["report"], manifest["verilator_execution_report"])
        self.assertEqual(verilator_execution["schema"], "e1-module-dpi-verilator-execution-report-v0")
        self.assertEqual(verilator_execution["status"], "pass")
        self.assertEqual(verilator_execution["execution_recipe"], manifest["verilator_execution_recipe"])
        self.assertEqual(readme_cycle_coverage["schema"], "e1-h1-module-dpi-readme-cycle-coverage-v0")
        self.assertEqual(
            readme_cycle_coverage["readme_index"],
            "e1/e1-h1/docs/modules/README.md#generated-cycle-contract-index",
        )
        isolation_by_name = {module["name"]: module for module in isolation["modules"]}
        cycle_contract_by_name = {module["name"]: module for module in cycle_contract["modules"]}
        test_plan_by_name = {module["name"]: module for module in test_plan["modules"]}
        recipe_by_name = {module["name"]: module for module in verilator_recipe["modules"]}
        verilator_execution_by_name = {module["name"]: module for module in verilator_execution["modules"]}
        readme_coverage_by_name = {module["name"]: module for module in readme_cycle_coverage["modules"]}

        modules = {module["name"]: module for module in manifest["modules"]}
        self.assertEqual(set(modules), {path.stem for path in IP_DIR.glob("*.json")})
        self.assertEqual(set(isolation_by_name), set(modules))
        self.assertEqual(set(cycle_contract_by_name), set(modules))
        self.assertEqual(set(test_plan_by_name), set(modules))
        self.assertEqual(set(recipe_by_name), set(modules))
        self.assertEqual(set(verilator_execution_by_name), set(modules))
        self.assertEqual(set(readme_coverage_by_name), set(modules))
        self.assertEqual([name for name, module in modules.items() if module["latch_buffer"]], ["ingress_sram"])
        for name, module in modules.items():
            vip = json.loads((REPO_ROOT / f"e1/e1-h1/vip/{name}.json").read_text(encoding="utf-8"))
            dpi = vip["dpi_equivalence"]
            self.assertEqual(module["scope"], "module_only")
            self.assertEqual(module["neighbors"], "cpp_dpi_environment")
            self.assertEqual(module["reference_module"], isolation_by_name[name]["reference_module"])
            self.assertEqual(module["cycle_contract"]["template"], cycle_contract_by_name[name]["template"])
            self.assertEqual(module["cycle_contract"]["phase_source"], "e1_h1_module_dpi_cycle")
            self.assertEqual(module["verilator_execution_recipe"], manifest["verilator_execution_recipe"])
            self.assertEqual(module["verilator_execution_report"], manifest["verilator_execution_report"])
            self.assertEqual(module["readme_cycle_coverage"], manifest["readme_cycle_coverage"])
            self.assertEqual(
                [step["cycle"] for step in module["cycle_contract"]["cycles"]],
                list(range(module["cycle_contract"]["cycle_period"])),
            )
            self.assertEqual({check["status"] for check in isolation_by_name[name]["checks"]}, {"pass"})
            self.assertEqual({check["status"] for check in cycle_contract_by_name[name]["checks"]}, {"pass"})
            self.assertEqual({check["status"] for check in test_plan_by_name[name]["checks"]}, {"pass"})
            self.assertEqual(verilator_execution_by_name[name]["status"], "pass")
            self.assertEqual(
                verilator_execution_by_name[name]["observed_stdout_markers"],
                test_plan_by_name[name]["verilator"]["expected_stdout_markers"],
            )
            self.assertEqual({check["status"] for check in readme_coverage_by_name[name]["checks"]}, {"pass"})
            self.assertEqual(readme_coverage_by_name[name]["template"], cycle_contract_by_name[name]["template"])
            self.assertEqual(
                readme_coverage_by_name[name]["phase_names"],
                [step["phase"] for step in cycle_contract_by_name[name]["cycles"]],
            )
            self.assertIn(name, readme)
            self.assertIn(readme_coverage_by_name[name]["template"], readme)
            for phase in readme_coverage_by_name[name]["phase_names"]:
                self.assertIn(phase, readme)
            self.assertEqual(test_plan_by_name[name]["verilator"]["top_module"], module["probe_module"])
            self.assertEqual(test_plan_by_name[name]["verilator"]["flist"], module["flist"])
            self.assertEqual(test_plan_by_name[name]["verilator"]["main"], module["main"])
            self.assertEqual(test_plan_by_name[name]["verilator"]["scoreboard"], manifest["scoreboard"])
            self.assertEqual(test_plan_by_name[name]["verilator"]["run_executable"], f"V{module['probe_module']}")
            self.assertEqual(recipe_by_name[name]["scope"], module["scope"])
            self.assertEqual(recipe_by_name[name]["top_module"], module["probe_module"])
            self.assertEqual(recipe_by_name[name]["dut_module"], module["top_module"])
            self.assertEqual(recipe_by_name[name]["flist"], module["flist"])
            self.assertEqual(recipe_by_name[name]["scoreboard"], manifest["scoreboard"])
            self.assertEqual(recipe_by_name[name]["main"], module["main"])
            self.assertEqual(
                recipe_by_name[name]["run_executable"],
                f"<build-root>/obj_module_dpi_{name}/V{module['probe_module']}",
            )
            self.assertEqual(
                recipe_by_name[name]["expected_stdout_markers"],
                test_plan_by_name[name]["verilator"]["expected_stdout_markers"],
            )
            self.assertEqual(
                verilator_execution_by_name[name]["build_command"],
                recipe_by_name[name]["build_command"],
            )
            self.assertEqual(
                verilator_execution_by_name[name]["run_executable"],
                recipe_by_name[name]["run_executable"],
            )
            self.assertEqual(module["probe"], dpi["module_probe"])
            self.assertEqual(module["main"], dpi["module_main"])
            self.assertEqual(module["flist"], dpi["module_flist"])
            self.assertEqual(manifest["scoreboard"], dpi["module_scoreboard"])
            self.assertEqual(module["vip_cases"], dpi["stream_space"]["cases"])
            self.assertGreater(len(module["cycle_notes"]), 0, module)
            self.assertTrue((REPO_ROOT / module["probe"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["main"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["flist"]).exists(), module)
            flist = (REPO_ROOT / module["flist"]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                flist,
                [
                    "e1/e1-h1/dpi/e1_h1_imp1_reference.sv",
                    module["imp2_rtl"],
                    module["probe"],
                ],
                module,
            )
            probe_text = (REPO_ROOT / module["probe"]).read_text(encoding="utf-8")
            self.assertIn(f"module {module['probe_module']};", probe_text)
            self.assertIn("e1_h1_module_dpi_begin", probe_text)
            self.assertIn("e1_h1_module_dpi_cycle", probe_text)
            self.assertIn(module["reference_module"], probe_text)
            self.assertIn(module["top_module"], probe_text)
            for forbidden in isolation_by_name[name]["forbidden_design_neighbors"]:
                self.assertNotIn(forbidden, probe_text)

        control_probe = (REPO_ROOT / modules["control_cpu"]["probe"]).read_text(encoding="utf-8")
        array_probe = (REPO_ROOT / modules["systolic_array"]["probe"]).read_text(encoding="utf-8")
        buffer_probe = (REPO_ROOT / modules["ingress_sram"]["probe"]).read_text(encoding="utf-8")
        self.assertNotIn("e1_h1_systolic_array", control_probe)
        self.assertNotIn("e1_h1_control_cpu", array_probe)
        self.assertNotIn("e1_h1_control_cpu", buffer_probe)
        self.assertNotIn("e1_h1_systolic_array", buffer_probe)
        self.assertIn("drive_latch_boundary", buffer_probe)
        self.assertIn("sample_latched_output", buffer_probe)
        self.assertIn("array_ready_i = (cycle >= 2);", buffer_probe)

    def test_generated_module_dpi_probes_run_under_verilator(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for generated module DPI probes")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            regenerate_module_dpi(tmp_path)
            run([
                "python3",
                str(MODULE_DPI_VERILATOR_RUNNER.relative_to(REPO_ROOT)),
                "--test-plan",
                str(MODULE_DPI_TEST_PLAN.relative_to(REPO_ROOT)),
                "--recipe",
                str(MODULE_DPI_VERILATOR_RECIPE.relative_to(REPO_ROOT)),
                "--report",
                str(MODULE_DPI_VERILATOR_EXECUTION.relative_to(REPO_ROOT)),
                "--suite",
                "module_dpi",
            ])
            report = json.loads(MODULE_DPI_VERILATOR_EXECUTION.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "e1-module-dpi-verilator-execution-report-v0")
            self.assertEqual(report["suite"], "module_dpi")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["execution_recipe"], "e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json")
            self.assertIn("cpp_execution_recipe_commands_match_runner", {check["name"] for check in report["checks"]})
            self.assertEqual({check["status"] for check in report["checks"]}, {"pass"})
            self.assertEqual({module["status"] for module in report["modules"]}, {"pass"})
            self.assertEqual(report["module_count"], len(report["modules"]))

    def test_full_checkpoint_module_dpi_generator_outputs_generated_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = regenerate_full_checkpoint_module_dpi(Path(tmp))
        self.assertIn("PASS e1_h1_generate_full_checkpoint_module_dpi 7 modules", result.stdout)

        manifest = json.loads(FULL_CHECKPOINT_MODULE_DPI_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "e1-h1-full-checkpoint-generated-module-dpi-v0")
        self.assertEqual(manifest["generator"], "e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp")
        self.assertEqual(
            manifest["scoreboard"],
            "e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_scoreboard.cpp",
        )
        self.assertEqual(
            manifest["module_interfaces_doc"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md",
        )
        self.assertEqual(
            manifest["module_isolation_proof"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json",
        )
        self.assertEqual(
            manifest["cycle_contract"],
            "e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json",
        )
        self.assertEqual(
            manifest["module_test_plan"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json",
        )
        self.assertEqual(
            manifest["verilator_execution_recipe"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            manifest["verilator_execution_report"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            manifest["readme_cycle_coverage"],
            "e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json",
        )
        self.assertTrue(FULL_CHECKPOINT_MODULE_INTERFACES.exists())
        self.assertTrue(FULL_CHECKPOINT_MODULE_ISOLATION.exists())
        self.assertTrue(FULL_CHECKPOINT_CYCLE_CONTRACT.exists())
        self.assertTrue(FULL_CHECKPOINT_MODULE_TEST_PLAN.exists())
        self.assertTrue(FULL_CHECKPOINT_MODULE_VERILATOR_RECIPE.exists())
        self.assertTrue(FULL_CHECKPOINT_MODULE_VERILATOR_EXECUTION.exists())
        self.assertTrue(FULL_CHECKPOINT_README_CYCLE_COVERAGE.exists())
        interface_doc = FULL_CHECKPOINT_MODULE_INTERFACES.read_text(encoding="utf-8")
        isolation = json.loads(FULL_CHECKPOINT_MODULE_ISOLATION.read_text(encoding="utf-8"))
        cycle_contract = json.loads(FULL_CHECKPOINT_CYCLE_CONTRACT.read_text(encoding="utf-8"))
        test_plan = json.loads(FULL_CHECKPOINT_MODULE_TEST_PLAN.read_text(encoding="utf-8"))
        verilator_recipe = json.loads(FULL_CHECKPOINT_MODULE_VERILATOR_RECIPE.read_text(encoding="utf-8"))
        verilator_execution = json.loads(FULL_CHECKPOINT_MODULE_VERILATOR_EXECUTION.read_text(encoding="utf-8"))
        readme_cycle_coverage = json.loads(FULL_CHECKPOINT_README_CYCLE_COVERAGE.read_text(encoding="utf-8"))
        readme = (E1_H1 / "docs" / "modules" / "README.md").read_text(encoding="utf-8")
        self.assertEqual(isolation["schema"], "e1-h1-full-checkpoint-module-isolation-v0")
        self.assertEqual(cycle_contract["schema"], "e1-h1-full-checkpoint-cycle-contract-v0")
        self.assertEqual(test_plan["schema"], "e1-h1-full-checkpoint-module-dpi-test-plan-v0")
        self.assertEqual(
            verilator_recipe["schema"],
            "e1-h1-full-checkpoint-module-dpi-verilator-execution-recipe-v0",
        )
        self.assertEqual(verilator_recipe["runner"], "e1/tools/run_module_dpi_verilator.py")
        self.assertEqual(verilator_recipe["suite"], "full_checkpoint_module_dpi")
        self.assertEqual(verilator_recipe["test_plan"], manifest["module_test_plan"])
        self.assertEqual(verilator_recipe["report"], manifest["verilator_execution_report"])
        self.assertEqual(verilator_execution["schema"], "e1-module-dpi-verilator-execution-report-v0")
        self.assertEqual(verilator_execution["status"], "pass")
        self.assertEqual(verilator_execution["execution_recipe"], manifest["verilator_execution_recipe"])
        self.assertEqual(readme_cycle_coverage["schema"], "e1-h1-full-checkpoint-readme-cycle-coverage-v0")
        self.assertEqual(cycle_contract["readme_diagram"], "e1/e1-h1/docs/modules/README.md#cycle-diagram")
        self.assertEqual(
            readme_cycle_coverage["readme_index"],
            "e1/e1-h1/docs/modules/README.md#generated-cycle-contract-index",
        )
        isolation_by_name = {module["name"]: module for module in isolation["modules"]}
        cycle_contract_by_name = {module["name"]: module for module in cycle_contract["modules"]}
        test_plan_by_name = {module["name"]: module for module in test_plan["modules"]}
        recipe_by_name = {module["name"]: module for module in verilator_recipe["modules"]}
        verilator_execution_by_name = {module["name"]: module for module in verilator_execution["modules"]}
        readme_coverage_by_name = {module["name"]: module for module in readme_cycle_coverage["modules"]}
        self.assertIn("# Generated Full-Checkpoint RTL Module Interfaces", interface_doc)
        self.assertIn("one_generated_probe_per_full_checkpoint_rtl_module", manifest["construction_rule"])
        modules = {module["name"]: module for module in manifest["modules"]}
        self.assertEqual(
            set(modules),
            {
                "linear_scheduler",
                "linear_tile_engine",
                "control_scheduler",
                "graph_sequencer",
                "linear_slot_engine",
                "control_slot_engine",
                "full_checkpoint_top",
            },
        )
        for module in modules.values():
            self.assertEqual(module["scope"], "generated_full_checkpoint_module_only")
            self.assertIn("cpp_dpi", module["neighbors"])
            self.assertIn(module["name"], isolation_by_name)
            self.assertIn(module["name"], cycle_contract_by_name)
            self.assertIn(module["name"], test_plan_by_name)
            self.assertIn(module["name"], recipe_by_name)
            self.assertIn(module["name"], verilator_execution_by_name)
            self.assertIn(module["name"], readme_coverage_by_name)
            self.assertEqual(isolation_by_name[module["name"]]["dut_module"], module["top_module"])
            self.assertEqual(isolation_by_name[module["name"]]["rtl_files"], module["rtl"])
            self.assertEqual({check["status"] for check in isolation_by_name[module["name"]]["checks"]}, {"pass"})
            self.assertEqual(module["cycle_contract"]["template"], cycle_contract_by_name[module["name"]]["template"])
            self.assertEqual(module["verilator_execution_recipe"], manifest["verilator_execution_recipe"])
            self.assertEqual(module["verilator_execution_report"], manifest["verilator_execution_report"])
            self.assertEqual(module["readme_cycle_coverage"], manifest["readme_cycle_coverage"])
            self.assertEqual(
                module["cycle_contract"]["phase_signals"],
                cycle_contract_by_name[module["name"]]["phase_signals"],
            )
            self.assertEqual(
                module["cycle_contract"]["cycles"],
                cycle_contract_by_name[module["name"]]["cycles"],
            )
            self.assertEqual({check["status"] for check in cycle_contract_by_name[module["name"]]["checks"]}, {"pass"})
            self.assertEqual({check["status"] for check in readme_coverage_by_name[module["name"]]["checks"]}, {"pass"})
            self.assertEqual(
                readme_coverage_by_name[module["name"]]["template"],
                cycle_contract_by_name[module["name"]]["template"],
            )
            self.assertEqual(
                readme_coverage_by_name[module["name"]]["phase_names"],
                [step["phase"] for step in cycle_contract_by_name[module["name"]]["cycles"]],
            )
            self.assertIn(module["name"], readme)
            self.assertIn(readme_coverage_by_name[module["name"]]["template"], readme)
            for phase in readme_coverage_by_name[module["name"]]["phase_names"]:
                self.assertIn(phase, readme)
            self.assertEqual(
                [step["cycle"] for step in module["cycle_contract"]["cycles"]],
                list(range(module["cycle_contract"]["cycle_period"])),
            )
            self.assertGreater(module["cycle_contract"]["cycle_period"], 0, module)
            self.assertEqual({check["status"] for check in test_plan_by_name[module["name"]]["checks"]}, {"pass"})
            self.assertEqual(test_plan_by_name[module["name"]]["verilator"]["top_module"], module["probe_module"])
            self.assertEqual(test_plan_by_name[module["name"]]["verilator"]["flist"], module["flist"])
            self.assertEqual(test_plan_by_name[module["name"]]["verilator"]["main"], module["main"])
            self.assertEqual(test_plan_by_name[module["name"]]["verilator"]["scoreboard"], manifest["scoreboard"])
            self.assertEqual(
                test_plan_by_name[module["name"]]["verilator"]["run_executable"],
                f"V{module['probe_module']}",
            )
            self.assertEqual(recipe_by_name[module["name"]]["scope"], module["scope"])
            self.assertEqual(recipe_by_name[module["name"]]["top_module"], module["probe_module"])
            self.assertEqual(recipe_by_name[module["name"]]["dut_module"], module["top_module"])
            self.assertEqual(recipe_by_name[module["name"]]["flist"], module["flist"])
            self.assertEqual(recipe_by_name[module["name"]]["scoreboard"], manifest["scoreboard"])
            self.assertEqual(recipe_by_name[module["name"]]["main"], module["main"])
            self.assertEqual(
                recipe_by_name[module["name"]]["run_executable"],
                f"<build-root>/obj_full_checkpoint_module_dpi_{module['name']}/V{module['probe_module']}",
            )
            self.assertEqual(
                recipe_by_name[module["name"]]["expected_stdout_markers"],
                test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"],
            )
            self.assertEqual(verilator_execution_by_name[module["name"]]["status"], "pass")
            self.assertEqual(
                verilator_execution_by_name[module["name"]]["build_command"],
                recipe_by_name[module["name"]]["build_command"],
            )
            self.assertEqual(
                verilator_execution_by_name[module["name"]]["run_executable"],
                recipe_by_name[module["name"]]["run_executable"],
            )
            self.assertEqual(
                verilator_execution_by_name[module["name"]]["observed_stdout_markers"],
                test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"],
            )
            self.assertGreater(len(module["cycle_notes"]), 0, module)
            self.assertGreater(len(module["input_signals"]), 0, module)
            self.assertGreater(len(module["output_signals"]), 0, module)
            self.assertIn(f"## {module['name']}", interface_doc)
            self.assertIn(f"- Top module: `{module['top_module']}`", interface_doc)
            self.assertIn(f"- DPI probe: `{module['probe_module']}`", interface_doc)
            self.assertTrue((REPO_ROOT / module["probe"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["main"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["flist"]).exists(), module)
            output_signal_names = {signal["name"] for signal in module["output_signals"]}
            for phase_signal in module["cycle_contract"]["phase_signals"]:
                self.assertIn(phase_signal, output_signal_names, module)
            for step in module["cycle_contract"]["cycles"]:
                self.assertEqual(
                    set(step),
                    {"cycle", "phase", "responsibility", "observed_signals", "dpi_check"},
                )
                self.assertIn(f"| {step['cycle']} | `{step['phase']}` |", interface_doc)
            flist = (REPO_ROOT / module["flist"]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(flist, [*module["rtl"], module["probe"]], module)
            probe_text = (REPO_ROOT / module["probe"]).read_text(encoding="utf-8")
            self.assertIn(f"module {module['probe_module']};", probe_text)
            self.assertIn("e1_h1_full_dpi_begin", probe_text)
            self.assertIn("e1_h1_full_dpi_cycle", probe_text)
            for signal in [*module["input_signals"], *module["output_signals"]]:
                self.assertEqual(set(signal), {"name", "width", "description"})
                self.assertTrue(signal["name"], signal)
                self.assertTrue(signal["width"], signal)
                self.assertTrue(signal["description"], signal)
                self.assertIn(
                    f"| `{signal['name']}` | {signal['width']} | {signal['description']} |",
                    interface_doc,
                )

        full_top = modules["full_checkpoint_top"]
        self.assertEqual(
            isolation_by_name["full_checkpoint_top"]["allowed_child_modules"],
            [
                "e1_h1_tinyllama_graph_sequencer",
                "e1_h1_tinyllama_linear_slot_engine",
                "e1_h1_tinyllama_control_slot_engine",
            ],
        )
        self.assertIn("e1_h1_systolic_array", isolation_by_name["full_checkpoint_top"]["forbidden_child_modules"])
        self.assertIn("e1_h1_stream_sram", isolation_by_name["full_checkpoint_top"]["forbidden_child_modules"])
        self.assertEqual(
            isolation_by_name["control_slot_engine"]["allowed_child_modules"],
            [],
        )
        self.assertIn("e1_h1_systolic_array", isolation_by_name["control_slot_engine"]["forbidden_child_modules"])
        self.assertIn(
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_slot_engine.sv",
            full_top["rtl"],
        )
        self.assertIn(
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_slot_engine.sv",
            full_top["rtl"],
        )
        control_slot_probe = (REPO_ROOT / modules["control_slot_engine"]["probe"]).read_text(encoding="utf-8")
        self.assertNotIn("e1_h1_systolic_array", control_slot_probe)
        linear_slot_probe = (REPO_ROOT / modules["linear_slot_engine"]["probe"]).read_text(encoding="utf-8")
        self.assertIn("SmokeMaxTilesPerLinearSlot(2)", linear_slot_probe)

    def test_full_checkpoint_module_dpi_probes_run_under_verilator(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for full checkpoint module DPI probes")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            regenerate_full_checkpoint_module_dpi(tmp_path)
            run([
                "python3",
                str(MODULE_DPI_VERILATOR_RUNNER.relative_to(REPO_ROOT)),
                "--test-plan",
                str(FULL_CHECKPOINT_MODULE_TEST_PLAN.relative_to(REPO_ROOT)),
                "--recipe",
                str(FULL_CHECKPOINT_MODULE_VERILATOR_RECIPE.relative_to(REPO_ROOT)),
                "--report",
                str(FULL_CHECKPOINT_MODULE_VERILATOR_EXECUTION.relative_to(REPO_ROOT)),
                "--suite",
                "full_checkpoint_module_dpi",
            ])
            report = json.loads(FULL_CHECKPOINT_MODULE_VERILATOR_EXECUTION.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "e1-module-dpi-verilator-execution-report-v0")
            self.assertEqual(report["suite"], "full_checkpoint_module_dpi")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                report["execution_recipe"],
                "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
            )
            self.assertIn("cpp_execution_recipe_commands_match_runner", {check["name"] for check in report["checks"]})
            self.assertEqual({check["status"] for check in report["checks"]}, {"pass"})
            self.assertEqual({module["status"] for module in report["modules"]}, {"pass"})
            self.assertEqual(report["module_count"], len(report["modules"]))

    def test_implementation_matrix_and_flists_define_imp1_imp2(self) -> None:
        run(["python3", str(E1_PIPELINE.relative_to(REPO_ROOT)), "--clean"])
        matrix = json.loads(IMPLEMENTATION_MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(matrix["schema"], "e1-h1-implementation-matrix-v0")
        self.assertEqual(matrix["reference_implementation"], "imp1")
        self.assertEqual(matrix["active_implementation"], "imp2")
        self.assertEqual(matrix["imp2_acceptance"], "verilator_dpi_vip_equivalent_to_imp1")
        self.assertEqual(matrix["dpi"]["probe"], "e1/e1-h1/dpi/e1_h1_imp_equiv_probe.sv")
        self.assertEqual(matrix["dpi"]["scoreboard"], "e1/e1-h1/dpi/e1_h1_imp_equiv_dpi.cpp")
        self.assertEqual(matrix["dpi"]["main"], "e1/e1-h1/dpi/e1_h1_imp_equiv_main.cpp")
        self.assertEqual(matrix["dpi"]["module_generator"], "e1/e1-h1/tools/generate_module_dpi.cpp")
        self.assertEqual(matrix["dpi"]["module_manifest"], "e1/e1-h1/generated/module_dpi/manifest.json")
        self.assertEqual(
            matrix["dpi"]["module_scoreboard"],
            "e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_scoreboard.cpp",
        )

        target = json.loads((TARGETS / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(target["implementation_matrix"], "e1/e1-h1/generated/implementation_matrix.json")
        self.assertEqual(target["implementation_flists"], matrix["flists"])

        active_flist = REPO_ROOT / matrix["flists"]["active"]
        self.assertTrue(active_flist.exists(), active_flist)
        self.assertEqual(active_flist.read_text(encoding="utf-8").splitlines(), target["rtl_files"])
        self.assertEqual(matrix["active_rtl_files"], target["rtl_files"])

        ip_names = {path.stem for path in IP_DIR.glob("*.json")}
        self.assertEqual({entry["name"] for entry in matrix["ips"]}, ip_names)
        self.assertEqual(set(matrix["flists"]["imp1"]), ip_names)
        self.assertEqual(set(matrix["flists"]["imp2"]), ip_names)
        for entry in matrix["ips"]:
            ip = json.loads((REPO_ROOT / entry["interface_source"]).read_text(encoding="utf-8"))
            self.assertEqual(entry["active"], "imp2")
            self.assertEqual(entry["reference"], "imp1")
            self.assertEqual(entry["vip"], ip["module_vip"])
            self.assertEqual(entry["l1_5_harness"], ip["l1_5_harness"])
            self.assertEqual(entry["imp1"]["kind"], "mock")
            self.assertEqual(entry["imp1"]["status"], "accepted")
            self.assertEqual(entry["imp1"]["module"], ip["module"])
            self.assertIn("/rtl/ip/", entry["imp1"]["rtl"])
            self.assertEqual(entry["imp2"]["status"], "accepted")
            self.assertEqual(entry["imp2"]["module"], ip["module"])
            self.assertEqual(entry["imp2"]["rtl_files"], [ip["rtl"]])
            self.assertIsNotNone(entry["imp2"]["flist"])
            self.assertEqual(entry["imp2"]["acceptance"], "verilator_dpi_vip_equivalent_to_imp1")
            self.assertEqual(entry["dpi_equivalence"]["reference"], "imp1")
            self.assertEqual(entry["dpi_equivalence"]["candidate"], "imp2")
            self.assertEqual(entry["dpi_equivalence"]["status"], "accepted")
            self.assertEqual(entry["dpi_equivalence"]["module_generator"], matrix["dpi"]["module_generator"])
            self.assertEqual(entry["dpi_equivalence"]["module_manifest"], matrix["dpi"]["module_manifest"])
            self.assertEqual(entry["dpi_equivalence"]["module_scoreboard"], matrix["dpi"]["module_scoreboard"])
            self.assertEqual(
                entry["dpi_equivalence"]["module_probe"],
                f"e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_{entry['name']}.sv",
            )
            self.assertEqual(
                entry["dpi_equivalence"]["module_main"],
                f"e1/e1-h1/generated/module_dpi/e1_h1_module_dpi_{entry['name']}_main.cpp",
            )
            self.assertEqual(
                entry["dpi_equivalence"]["module_flist"],
                f"e1/e1-h1/generated/module_dpi/flists/{entry['name']}.f",
            )
            for key in ["module_probe", "module_main", "module_flist", "module_scoreboard"]:
                self.assertTrue((REPO_ROOT / entry["dpi_equivalence"][key]).exists(), (entry["name"], key))
            imp1_flist = REPO_ROOT / entry["imp1"]["flist"]
            self.assertTrue(imp1_flist.exists(), imp1_flist)
            self.assertEqual(imp1_flist.read_text(encoding="utf-8").splitlines(), [entry["imp1"]["rtl"]])
            imp2_flist = REPO_ROOT / entry["imp2"]["flist"]
            self.assertTrue(imp2_flist.exists(), imp2_flist)
            self.assertEqual(imp2_flist.read_text(encoding="utf-8").splitlines(), [ip["rtl"]])

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
        self.assertIn("PASS e1_pipeline 27 passes", result.stdout)

        summary = json.loads((E1_PIPELINE_OUT / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["schema"], "e1-pipeline-summary-v0")
        self.assertEqual(summary["model_id"], "tinyllama-1.1b-chat-v1.0")
        self.assertEqual(summary["architecture_id"], "e1-h1")
        self.assertEqual(summary["pass_count"], 27)
        self.assertEqual(summary["operation_counts"]["dot_general"], 6)
        self.assertTrue(summary["all_current_modules_have_l1_5_harnesses"])
        self.assertEqual(summary["generated_top"], "e1/e1-h1/generated/e1_h1_soc_top.sv")
        self.assertEqual(summary["end_to_end_smoke"], "e1/generated/pipeline/27_end_to_end_smoke.json")
        self.assertEqual(summary["end_to_end_status"], "pass")
        self.assertEqual(summary["module_dpi_generation"], "e1/generated/pipeline/12_module_dpi_generation.json")
        self.assertEqual(summary["module_dpi_manifest"], "e1/e1-h1/generated/module_dpi/manifest.json")
        self.assertEqual(summary["module_dpi_isolation_proof"], "e1/e1-h1/generated/module_dpi/module_isolation.json")
        self.assertEqual(summary["module_dpi_cycle_contract"], "e1/e1-h1/generated/module_dpi/cycle_contract.json")
        self.assertEqual(summary["module_dpi_test_plan"], "e1/e1-h1/generated/module_dpi/module_test_plan.json")
        self.assertEqual(
            summary["module_dpi_verilator_execution_recipe"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            summary["module_dpi_verilator_execution_report"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            summary["module_dpi_readme_cycle_coverage"],
            "e1/e1-h1/generated/module_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(summary["rtl_lowering"], "e1/generated/pipeline/15_rtl_lowering.json")
        self.assertEqual(summary["rtl_lowering_status"], "pass")
        self.assertEqual(
            summary["full_checkpoint_rtl_lowering_plan"],
            "e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json",
        )
        self.assertEqual(summary["full_checkpoint_rtl_lowering_status"], "planned")
        self.assertTrue(summary["full_checkpoint_graph_lowered_to_rtl"])
        self.assertEqual(
            summary["full_checkpoint_graph_rtl_lowering_proof"],
            "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
        )
        self.assertEqual(summary["full_checkpoint_graph_rtl_lowering_status"], "pass")
        self.assertTrue(summary["full_checkpoint_command_stream_rtl_execution"])
        self.assertFalse(summary["full_checkpoint_numeric_output_equivalence"])
        self.assertEqual(summary["full_checkpoint_command_stream"], "e1/generated/pipeline/19_full_checkpoint_command_stream.json")
        self.assertEqual(summary["full_checkpoint_command_stream_status"], "pass")
        self.assertEqual(summary["full_checkpoint_total_tile_commands"], 3784704)
        self.assertEqual(
            summary["full_checkpoint_rtl_cycle_lowering"],
            "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json",
        )
        self.assertEqual(summary["full_checkpoint_rtl_cycle_lowering_status"], "pass")
        self.assertEqual(summary["full_checkpoint_total_rtl_cycles"], 30277632)
        self.assertEqual(summary["full_checkpoint_tile_engine"], "e1/generated/pipeline/21_full_checkpoint_tile_engine.json")
        self.assertEqual(summary["full_checkpoint_tile_engine_status"], "pass")
        self.assertEqual(
            summary["full_checkpoint_control_scheduler"],
            "e1/generated/pipeline/22_full_checkpoint_control_scheduler.json",
        )
        self.assertEqual(summary["full_checkpoint_control_scheduler_status"], "pass")
        self.assertEqual(summary["full_checkpoint_total_control_ops"], 154)
        self.assertEqual(summary["full_checkpoint_graph_sequencer"], "e1/generated/pipeline/23_full_checkpoint_graph_sequencer.json")
        self.assertEqual(summary["full_checkpoint_graph_sequencer_status"], "pass")
        self.assertEqual(summary["full_checkpoint_total_graph_slots"], 308)
        self.assertEqual(summary["full_checkpoint_rtl_top"], "e1/generated/pipeline/24_full_checkpoint_rtl_top.json")
        self.assertEqual(summary["full_checkpoint_rtl_top_status"], "pass")
        self.assertEqual(summary["full_checkpoint_rtl_top_smoke_max_tiles_per_linear_slot"], 2)
        self.assertEqual(
            summary["full_checkpoint_rtl_top_full_verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp",
        )
        self.assertEqual(summary["full_checkpoint_rtl_top_full_expected_linear_commands"], 3784704)
        self.assertTrue(summary["full_checkpoint_rtl_top_full_command_count_rtl_execution"])
        self.assertTrue(summary["full_checkpoint_rtl_top_full_command_payload_schedule_check"])
        self.assertTrue(summary["full_checkpoint_rtl_top_full_command_cycle_phase_check"])
        self.assertEqual(
            summary["full_checkpoint_module_dpi_generation"],
            "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json",
        )
        self.assertEqual(summary["full_checkpoint_module_dpi_manifest"], "e1/e1-h1/generated/full_checkpoint_dpi/manifest.json")
        self.assertEqual(
            summary["full_checkpoint_module_interfaces_doc"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md",
        )
        self.assertEqual(
            summary["full_checkpoint_module_isolation_proof"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json",
        )
        self.assertEqual(
            summary["full_checkpoint_module_cycle_contract"],
            "e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json",
        )
        self.assertEqual(
            summary["full_checkpoint_module_test_plan"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json",
        )
        self.assertEqual(
            summary["full_checkpoint_module_verilator_execution_recipe"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            summary["full_checkpoint_module_verilator_execution_report"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            summary["full_checkpoint_module_readme_cycle_coverage"],
            "e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(summary["full_checkpoint_module_dpi_status"], "pass")
        self.assertEqual(summary["full_checkpoint_module_dpi_count"], 7)
        self.assertEqual(
            summary["full_tinyllama_checkpoint_execution"],
            "e1/generated/pipeline/17_full_tinyllama_checkpoint_execution.json",
        )
        self.assertFalse(summary["full_tinyllama_checkpoint_implemented"])
        self.assertIn(
            summary["full_tinyllama_checkpoint_execution_status"],
            {"missing_python_dependencies", "missing_checkpoint_cache", "missing_checkpoint_files", "ready"},
        )
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
            "e1_select_implementations",
            "e1_generate_module_dpi",
            "e1_emit_systemverilog",
            "e1_package_targets",
            "e1_lower_to_rtl",
            "e1_check_tinyllama_imp2_coverage",
            "e1_run_full_tinyllama_checkpoint",
            "e1_plan_full_checkpoint_rtl_lowering",
            "e1_emit_full_checkpoint_command_stream",
            "e1_lower_full_checkpoint_command_stream_to_rtl_cycles",
            "e1_wire_full_checkpoint_tile_engine",
            "e1_lower_full_checkpoint_control_ops_to_rtl",
            "e1_sequence_full_checkpoint_graph_slots",
            "e1_integrate_full_checkpoint_rtl_top",
            "e1_prove_full_checkpoint_graph_rtl_lowering",
            "e1_generate_full_checkpoint_module_dpi",
            "e1_end_to_end_smoke",
        ]
        self.assertEqual([entry["pass"] for entry in summary["passes"]], expected_passes)

        inspection = json.loads((E1_PIPELINE_OUT / "03_stablehlo_inspection.json").read_text(encoding="utf-8"))
        self.assertEqual(inspection["unsupported_ops"], [])
        self.assertIn("dot_general", inspection["systolic_array_ops"])
        self.assertIn("Ethernet/RGMII", inspection["answers"]["external_data_source"])

        binding = json.loads((E1_PIPELINE_OUT / "05_e1_h1_binding.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["bindings"]["stablehlo.constant"], "control_cpu")
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

        model_manifest = json.loads((E1_PIPELINE_OUT / "00_model_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(model_manifest["checkpoint_shape"]["model_type"], "llama")
        self.assertEqual(model_manifest["checkpoint_shape"]["num_hidden_layers"], 22)
        self.assertEqual(model_manifest["checkpoint_shape"]["hidden_size"], 2048)
        self.assertEqual(model_manifest["checkpoint_shape"]["intermediate_size"], 5632)
        self.assertEqual(model_manifest["checkpoint_shape"]["num_attention_heads"], 32)
        self.assertEqual(model_manifest["checkpoint_shape"]["num_key_value_heads"], 4)

        export = json.loads((E1_PIPELINE_OUT / "02_stablehlo_export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["schema"], "e1-stablehlo-export-report-v0")
        self.assertEqual(export["status"], "offline_fixture")
        self.assertEqual(export["stablehlo_out"], "e1/generated/pipeline/02_stablehlo.mlir")
        self.assertEqual(export["fixture"], "e1/fixtures/stablehlo/tinyllama_block.mlir")

        device_plan = json.loads((E1_PIPELINE_OUT / "07_device_program_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(device_plan["program"], "e1/code/program/e1_tinyllama_program.cpp")
        self.assertEqual(device_plan["host_smoke"], "e1/code/program/e1_tinyllama_program_host_smoke.cpp")
        self.assertEqual(device_plan["run_report"], "e1/generated/pipeline/07_device_program_run.json")
        self.assertEqual(device_plan["run_status"], "pass")

        device_run = json.loads((E1_PIPELINE_OUT / "07_device_program_run.json").read_text(encoding="utf-8"))
        self.assertEqual(device_run["schema"], "e1-device-program-smoke-v0")
        self.assertEqual(device_run["source"], "e1/code/program/e1_tinyllama_program.cpp")
        self.assertEqual(device_run["host_smoke"], "e1/code/program/e1_tinyllama_program_host_smoke.cpp")
        self.assertEqual(device_run["status"], "pass")
        self.assertEqual(device_run["program"], "first_attention_tile")
        self.assertEqual(device_run["writes"], 7)
        self.assertEqual(device_run["status_reads"], 2)

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

        module_dpi_report = json.loads((E1_PIPELINE_OUT / "12_module_dpi_generation.json").read_text(encoding="utf-8"))
        self.assertEqual(module_dpi_report["schema"], "e1-module-dpi-generation-report-v0")
        self.assertEqual(module_dpi_report["status"], "pass")
        self.assertEqual(module_dpi_report["generator"], "e1/e1-h1/tools/generate_module_dpi.cpp")
        self.assertEqual(module_dpi_report["manifest"], "e1/e1-h1/generated/module_dpi/manifest.json")
        self.assertEqual(module_dpi_report["module_isolation_proof"], "e1/e1-h1/generated/module_dpi/module_isolation.json")
        self.assertEqual(module_dpi_report["cycle_contract"], "e1/e1-h1/generated/module_dpi/cycle_contract.json")
        self.assertEqual(module_dpi_report["module_test_plan"], "e1/e1-h1/generated/module_dpi/module_test_plan.json")
        self.assertEqual(
            module_dpi_report["verilator_execution_recipe"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            module_dpi_report["verilator_execution_report"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            module_dpi_report["readme_cycle_coverage"],
            "e1/e1-h1/generated/module_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(module_dpi_report["module_count"], len(list(IP_DIR.glob("*.json"))))
        self.assertIn("without the systolic array RTL", module_dpi_report["separation_of_concerns"]["control_cpu"])
        self.assertIn("without CPU RTL", module_dpi_report["separation_of_concerns"]["systolic_array"])
        self.assertEqual({check["status"] for check in module_dpi_report["checks"]}, {"pass"})
        self.assertEqual([module["name"] for module in module_dpi_report["modules"] if module["latch_buffer"]], ["ingress_sram"])
        for module in module_dpi_report["modules"]:
            self.assertTrue((REPO_ROOT / module["probe"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["main"]).exists(), module)
            self.assertTrue((REPO_ROOT / module["flist"]).exists(), module)
            self.assertGreater(len(module["cycle_notes"]), 0, module)
            self.assertEqual(module["isolation"]["dut_module"], module["top_module"])
            self.assertEqual(module["isolation"]["reference_module"], module["reference_module"])
            self.assertEqual({check["status"] for check in module["isolation"]["checks"]}, {"pass"})
            self.assertEqual({check["status"] for check in module["cycle_contract"]["checks"]}, {"pass"})
            self.assertEqual(
                [step["cycle"] for step in module["cycle_contract"]["cycles"]],
                list(range(module["cycle_contract"]["cycle_period"])),
            )
            self.assertEqual({check["status"] for check in module["test_plan"]["checks"]}, {"pass"})
            self.assertEqual(
                module["verilator_execution"]["build_command"],
                module["verilator_execution_recipe"]["build_command"],
            )
            self.assertEqual(
                module["verilator_execution"]["run_executable"],
                module["verilator_execution_recipe"]["run_executable"],
            )
            self.assertEqual(module["verilator_execution"]["status"], "pass")
            self.assertEqual(
                module["verilator_execution"]["observed_stdout_markers"],
                module["test_plan"]["verilator"]["expected_stdout_markers"],
            )
            self.assertEqual({check["status"] for check in module["readme_cycle_coverage"]["checks"]}, {"pass"})
            self.assertEqual(
                module["readme_cycle_coverage"]["phase_names"],
                [step["phase"] for step in module["cycle_contract"]["cycles"]],
            )
            self.assertEqual(module["test_plan"]["verilator"]["top_module"], module["probe"].split("/")[-1][:-3])
            self.assertEqual(module["test_plan"]["verilator"]["flist"], module["flist"])
            self.assertEqual(module["test_plan"]["verilator"]["main"], module["main"])

        target_plan = json.loads((E1_PIPELINE_OUT / "14_target_package_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(target_plan["manifest"], "e1/e1-h1/generated/targets/manifest.json")
        self.assertTrue(target_plan["digital_only"])
        self.assertEqual(target_plan["fpga"]["top"], "e1_h1_soc_top")
        self.assertEqual(target_plan["asic_openroad"]["top"], "e1_h1_soc_top")
        self.assertEqual(
            target_plan["fpga"]["package"]["filelist"],
            "e1/e1-h1/generated/targets/fpga/rtl.filelist",
        )

        implementation_matrix = json.loads(IMPLEMENTATION_MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(implementation_matrix["schema"], "e1-h1-implementation-matrix-v0")
        self.assertEqual(implementation_matrix["reference_implementation"], "imp1")
        self.assertEqual(implementation_matrix["active_implementation"], "imp2")
        self.assertEqual(implementation_matrix["imp2_acceptance"], "verilator_dpi_vip_equivalent_to_imp1")

        rtl_lowering = json.loads((E1_PIPELINE_OUT / "15_rtl_lowering.json").read_text(encoding="utf-8"))
        self.assertEqual(rtl_lowering["schema"], "e1-rtl-lowering-v0")
        self.assertEqual(rtl_lowering["status"], "pass")
        self.assertEqual(rtl_lowering["model_id"], summary["model_id"])
        self.assertEqual(rtl_lowering["scope"]["kind"], "reduced_stablehlo_fixture")
        self.assertFalse(rtl_lowering["scope"]["full_checkpoint_graph_lowering"])
        self.assertEqual(rtl_lowering["architecture_id"], summary["architecture_id"])
        self.assertEqual(rtl_lowering["pipeline"], summary["pipeline"])
        self.assertEqual(rtl_lowering["hardware_graph"], "e1/generated/pipeline/10_hardware_graph.json")
        self.assertEqual(rtl_lowering["implementation_matrix"], "e1/e1-h1/generated/implementation_matrix.json")
        self.assertEqual(rtl_lowering["module_dpi_generation"]["manifest"], module_dpi_report["manifest"])
        self.assertEqual(rtl_lowering["cycle_diagram"], "e1/e1-h1/docs/modules/README.md")
        self.assertEqual({check["status"] for check in rtl_lowering["checks"]}, {"pass"})
        self.assertEqual(
            {entry["operation"] for entry in rtl_lowering["operation_lowering"]},
            {f"stablehlo.{name}" for name in summary["operation_counts"]},
        )
        self.assertEqual({entry["status"] for entry in rtl_lowering["operation_lowering"]}, {"pass"})
        self.assertEqual(
            {entry["operation"]: entry["ip"] for entry in rtl_lowering["operation_lowering"]},
            {
                "stablehlo.add": "control_cpu",
                "stablehlo.constant": "control_cpu",
                "stablehlo.dot_general": "systolic_array",
                "stablehlo.gather": "control_cpu",
                "stablehlo.multiply": "control_cpu",
                "stablehlo.tanh": "control_cpu",
            },
        )
        self.assertIn("control_cpu", {entry["module"] for entry in rtl_lowering["cycle_schedule"]})
        self.assertIn("ingress_sram", {entry["module"] for entry in rtl_lowering["cycle_schedule"]})
        self.assertIn("systolic_array", {entry["module"] for entry in rtl_lowering["cycle_schedule"]})
        for entry in rtl_lowering["operation_lowering"]:
            self.assertEqual(entry["active_implementation"], "imp2", entry)
            self.assertIsNotNone(entry["module_dpi_probe"], entry)
            self.assertIsNotNone(entry["module_dpi_flist"], entry)
            self.assertTrue((REPO_ROOT / entry["module_dpi_probe"]).exists(), entry)
            self.assertTrue((REPO_ROOT / entry["module_dpi_flist"]).exists(), entry)
            self.assertTrue(all("/rtl/imp2/" in path for path in entry["rtl_files"]), entry)

        tinyllama_coverage = json.loads((E1_PIPELINE_OUT / "16_tinyllama_imp2_coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(tinyllama_coverage["schema"], "e1-tinyllama-imp2-coverage-v0")
        self.assertEqual(tinyllama_coverage["status"], "pass")
        self.assertEqual(tinyllama_coverage["model_id"], summary["model_id"])
        self.assertEqual(tinyllama_coverage["scope"]["kind"], "reduced_stablehlo_fixture")
        self.assertEqual(tinyllama_coverage["scope"]["fixture"], "e1/fixtures/stablehlo/tinyllama_block.mlir")
        self.assertEqual(tinyllama_coverage["scope"]["function"], "tinyllama_block")
        self.assertTrue(tinyllama_coverage["tinyllama_fixture_implemented"])
        self.assertFalse(tinyllama_coverage["full_tinyllama_checkpoint_implemented"])
        self.assertFalse(tinyllama_coverage["scope"]["full_checkpoint_execution"])
        self.assertEqual(tinyllama_coverage["operation_counts"], summary["operation_counts"])
        self.assertEqual(
            {entry["operation"] for entry in tinyllama_coverage["operation_coverage"]},
            {f"stablehlo.{name}" for name in summary["operation_counts"]},
        )
        self.assertEqual(
            {entry["operation"]: entry["ip"] for entry in tinyllama_coverage["operation_coverage"]},
            {
                "stablehlo.add": "control_cpu",
                "stablehlo.constant": "control_cpu",
                "stablehlo.dot_general": "systolic_array",
                "stablehlo.gather": "control_cpu",
                "stablehlo.multiply": "control_cpu",
                "stablehlo.tanh": "control_cpu",
            },
        )
        self.assertEqual({entry["status"] for entry in tinyllama_coverage["operation_coverage"]}, {"pass"})
        self.assertEqual({entry["active_implementation"] for entry in tinyllama_coverage["operation_coverage"]}, {"imp2"})
        self.assertEqual(set(tinyllama_coverage["required_imp2_ips"]), {"control_cpu", "systolic_array"})
        self.assertEqual(tinyllama_coverage["implementation_matrix"], "e1/e1-h1/generated/implementation_matrix.json")
        self.assertEqual(tinyllama_coverage["active_flist"], implementation_matrix["flists"]["active"])
        self.assertEqual(tinyllama_coverage["target_rtl_files"], implementation_matrix["active_rtl_files"])
        self.assertEqual(tinyllama_coverage["device_program_run_status"], "pass")
        self.assertEqual(tinyllama_coverage["chip_model_run_status"], "pass")
        self.assertEqual({check["status"] for check in tinyllama_coverage["checks"]}, {"pass"})
        for entry in tinyllama_coverage["operation_coverage"]:
            self.assertIsNotNone(entry["flist"], entry)
            self.assertTrue((REPO_ROOT / entry["flist"]).exists(), entry)
            self.assertGreater(len(entry["rtl_files"]), 0, entry)
            self.assertTrue(all("/rtl/imp2/" in path for path in entry["rtl_files"]), entry)
            for rtl in entry["rtl_files"]:
                self.assertTrue((REPO_ROOT / rtl).exists(), rtl)

        sv_plan = json.loads((E1_PIPELINE_OUT / "13_systemverilog_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(sv_plan["generated_top"], "e1/e1-h1/generated/e1_h1_soc_top.sv")
        self.assertEqual(sv_plan["generated_composition_manifest"], "e1/e1-h1/generated/e1_h1_soc_top_manifest.json")
        self.assertEqual(sv_plan["generated_interface_contracts"], "e1/e1-h1/generated/e1_h1_interface_contracts.json")
        self.assertEqual(sv_plan["pipeline_source"], "e1/e1-h1/config/architecture.json")
        self.assertEqual(sv_plan["pipeline"], summary["pipeline"])

        full_checkpoint = json.loads(
            (E1_PIPELINE_OUT / "17_full_tinyllama_checkpoint_execution.json").read_text(encoding="utf-8")
        )
        self.assertEqual(full_checkpoint["schema"], "e1-full-tinyllama-checkpoint-execution-v0")
        self.assertEqual(full_checkpoint["model_id"], summary["model_id"])
        self.assertEqual(full_checkpoint["mode"], "preflight")
        self.assertEqual(full_checkpoint["full_checkpoint_execution"], False)
        self.assertEqual(full_checkpoint["status"], summary["full_tinyllama_checkpoint_execution_status"])
        self.assertIn("torch", full_checkpoint["dependencies"])
        self.assertIn("transformers", full_checkpoint["dependencies"])
        if full_checkpoint["status"] != "ready":
            self.assertTrue(
                full_checkpoint["missing_dependencies"] or full_checkpoint["missing_checkpoint_files"],
                full_checkpoint,
            )

        full_checkpoint_rtl = json.loads(
            (E1_PIPELINE_OUT / "18_full_checkpoint_rtl_lowering_plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(full_checkpoint_rtl["schema"], "e1-full-checkpoint-rtl-lowering-plan-v0")
        self.assertEqual(full_checkpoint_rtl["status"], "planned")
        self.assertEqual(full_checkpoint_rtl["model_id"], summary["model_id"])
        self.assertFalse(full_checkpoint_rtl["full_checkpoint_graph_lowering"])
        self.assertFalse(full_checkpoint_rtl["full_checkpoint_rtl_execution"])
        self.assertEqual(full_checkpoint_rtl["truth_boundary"], "shape_complete_layer_plan_only")
        self.assertEqual(full_checkpoint_rtl["checkpoint_shape"]["num_hidden_layers"], 22)
        self.assertEqual(full_checkpoint_rtl["head_dim"], 64)
        self.assertEqual(full_checkpoint_rtl["kv_projection_width"], 256)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["layers"], 22)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["linear_ops_per_layer"], 7)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["control_ops_per_layer"], 7)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["total_linear_ops"], 154)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["total_control_ops"], 154)
        self.assertEqual(full_checkpoint_rtl["aggregate"]["module_dpi_manifest"], module_dpi_report["manifest"])
        self.assertEqual(full_checkpoint_rtl["aggregate"]["reduced_fixture_rtl_lowering"], "e1/generated/pipeline/15_rtl_lowering.json")
        self.assertEqual({check["status"] for check in full_checkpoint_rtl["construction_checks"]}, {"pass"})
        self.assertEqual(len(full_checkpoint_rtl["layers"]), 22)
        first_layer = full_checkpoint_rtl["layers"][0]
        self.assertEqual(first_layer["layer"], 0)
        self.assertEqual(len(first_layer["ops"]), 14)
        self.assertEqual(
            [op["name"] for op in first_layer["ops"] if op["kind"] == "linear"],
            ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        )
        self.assertEqual({op["ip"] for op in first_layer["ops"] if op["kind"] == "linear"}, {"systolic_array"})
        self.assertEqual({op["ip"] for op in first_layer["ops"] if op["kind"] != "linear"}, {"control_cpu"})
        self.assertGreater(len(full_checkpoint_rtl["remaining_to_execute_full_rtl"]), 0)

        command_stream = json.loads((E1_PIPELINE_OUT / "19_full_checkpoint_command_stream.json").read_text(encoding="utf-8"))
        self.assertEqual(command_stream["schema"], "e1-full-checkpoint-command-stream-v0")
        self.assertEqual(command_stream["status"], "pass")
        self.assertEqual(command_stream["truth_boundary"], "compressed_tile_command_stream")
        self.assertFalse(command_stream["full_checkpoint_graph_lowering"])
        self.assertFalse(command_stream["full_checkpoint_rtl_execution"])
        self.assertEqual(command_stream["header"], "e1/code/program/e1_tinyllama_full_schedule.hpp")
        self.assertEqual(command_stream["host_smoke"], "e1/code/program/e1_tinyllama_full_schedule_smoke.cpp")
        self.assertTrue((REPO_ROOT / command_stream["header"]).exists())
        self.assertTrue((REPO_ROOT / command_stream["host_smoke"]).exists())
        self.assertEqual(command_stream["layers"], 22)
        self.assertEqual(command_stream["commands_per_layer"], 172032)
        self.assertEqual(command_stream["total_tile_commands"], 3784704)
        self.assertEqual(command_stream["smoke"]["status"], "pass")
        self.assertEqual(command_stream["smoke"]["total_tile_commands"], 3784704)
        self.assertEqual({check["status"] for check in command_stream["checks"]}, {"pass", "planned"})
        self.assertEqual(
            [op["name"] for op in command_stream["linear_ops"]],
            ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        )

        rtl_cycle = json.loads((E1_PIPELINE_OUT / "20_full_checkpoint_rtl_cycle_lowering.json").read_text(encoding="utf-8"))
        self.assertEqual(rtl_cycle["schema"], "e1-full-checkpoint-rtl-cycle-lowering-v0")
        self.assertEqual(rtl_cycle["status"], "pass")
        self.assertEqual(rtl_cycle["truth_boundary"], "linear_tile_command_scheduler_rtl")
        self.assertTrue(rtl_cycle["full_checkpoint_linear_command_rtl_lowering"])
        self.assertFalse(rtl_cycle["full_checkpoint_graph_lowering"])
        self.assertFalse(rtl_cycle["full_checkpoint_rtl_execution"])
        self.assertEqual(
            rtl_cycle["scheduler_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler.sv",
        )
        self.assertEqual(
            rtl_cycle["verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler_tb.cpp",
        )
        self.assertEqual(
            rtl_cycle["flist"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_scheduler.f",
        )
        self.assertEqual(rtl_cycle["cycle_smoke"], "e1/code/program/e1_tinyllama_full_rtl_cycle_smoke.cpp")
        self.assertEqual(rtl_cycle["module_dpi_manifest"], module_dpi_report["manifest"])
        self.assertEqual(rtl_cycle["separated_modules"], ["control_cpu", "ingress_sram", "systolic_array"])
        self.assertEqual(rtl_cycle["latch_buffer_module"], "ingress_sram")
        self.assertEqual(rtl_cycle["cycles_per_tile_command"], 8)
        self.assertEqual(rtl_cycle["total_tile_commands"], 3784704)
        self.assertEqual(rtl_cycle["total_rtl_cycles"], 30277632)
        self.assertEqual(rtl_cycle["cycle_smoke_report"]["status"], "pass")
        self.assertEqual(rtl_cycle["cycle_smoke_report"]["total_rtl_cycles"], 30277632)
        self.assertEqual({check["status"] for check in rtl_cycle["checks"]}, {"pass"})
        self.assertEqual([entry["cycle"] for entry in rtl_cycle["phase_template"]], list(range(8)))
        self.assertIn("control_cpu", {entry["module"] for entry in rtl_cycle["phase_template"]})
        self.assertIn("ingress_sram", {entry["module"] for entry in rtl_cycle["phase_template"]})
        self.assertIn("systolic_array", {entry["module"] for entry in rtl_cycle["phase_template"]})
        for path in [rtl_cycle["scheduler_rtl"], rtl_cycle["verilator_tb"], rtl_cycle["flist"], rtl_cycle["cycle_smoke"]]:
            self.assertTrue((REPO_ROOT / path).exists(), path)
        scheduler_text = (REPO_ROOT / rtl_cycle["scheduler_rtl"]).read_text(encoding="utf-8")
        self.assertIn("module e1_h1_tinyllama_linear_scheduler", scheduler_text)
        self.assertIn("cycle_phase_o", scheduler_text)
        self.assertIn("TotalTileCommands = 32'd3784704", scheduler_text)

        tile_engine = json.loads((E1_PIPELINE_OUT / "21_full_checkpoint_tile_engine.json").read_text(encoding="utf-8"))
        self.assertEqual(tile_engine["schema"], "e1-full-checkpoint-tile-engine-v0")
        self.assertEqual(tile_engine["status"], "pass")
        self.assertEqual(tile_engine["truth_boundary"], "scheduler_latch_buffer_systolic_array_rtl_composition")
        self.assertTrue(tile_engine["full_checkpoint_linear_tile_engine_rtl"])
        self.assertFalse(tile_engine["full_checkpoint_graph_lowering"])
        self.assertFalse(tile_engine["full_checkpoint_rtl_execution"])
        self.assertEqual(
            tile_engine["engine_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine.sv",
        )
        self.assertEqual(
            tile_engine["verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine_tb.cpp",
        )
        self.assertEqual(
            tile_engine["flist"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_tile_engine.f",
        )
        self.assertEqual(tile_engine["scheduler_rtl"], rtl_cycle["scheduler_rtl"])
        self.assertEqual(tile_engine["latch_buffer_rtl"], "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv")
        self.assertEqual(tile_engine["systolic_array_rtl"], "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv")
        self.assertEqual(tile_engine["total_tile_commands"], 3784704)
        self.assertEqual(tile_engine["cycles_per_tile_command"], 8)
        self.assertEqual(tile_engine["total_rtl_cycles"], 30277632)
        self.assertEqual({check["status"] for check in tile_engine["checks"]}, {"pass"})
        self.assertEqual(
            set(tile_engine["separation"]),
            {"scheduler", "latch_buffer", "systolic_array"},
        )
        for path in [tile_engine["engine_rtl"], tile_engine["verilator_tb"], tile_engine["flist"]]:
            self.assertTrue((REPO_ROOT / path).exists(), path)
        tile_engine_text = (REPO_ROOT / tile_engine["engine_rtl"]).read_text(encoding="utf-8")
        self.assertIn("u_scheduler", tile_engine_text)
        self.assertIn("u_latch_buffer", tile_engine_text)
        self.assertIn("u_systolic_array", tile_engine_text)
        self.assertIn("assign array_cmd_valid_o = scheduler_cmd_valid_o && (cycle_phase_o == 3'd2);", tile_engine_text)

        control_scheduler = json.loads((E1_PIPELINE_OUT / "22_full_checkpoint_control_scheduler.json").read_text(encoding="utf-8"))
        self.assertEqual(control_scheduler["schema"], "e1-full-checkpoint-control-scheduler-v0")
        self.assertEqual(control_scheduler["status"], "pass")
        self.assertEqual(control_scheduler["truth_boundary"], "cpu_control_op_scheduler_rtl")
        self.assertTrue(control_scheduler["full_checkpoint_control_op_rtl_lowering"])
        self.assertFalse(control_scheduler["full_checkpoint_graph_lowering"])
        self.assertFalse(control_scheduler["full_checkpoint_rtl_execution"])
        self.assertEqual(
            control_scheduler["scheduler_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler.sv",
        )
        self.assertEqual(
            control_scheduler["verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler_tb.cpp",
        )
        self.assertEqual(
            control_scheduler["flist"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_scheduler.f",
        )
        control_module_dpi = next(module for module in module_dpi_report["modules"] if module["name"] == "control_cpu")
        self.assertEqual(control_scheduler["module_dpi_probe"], control_module_dpi["probe"])
        self.assertEqual(control_scheduler["layers"], 22)
        self.assertEqual(control_scheduler["control_ops_per_layer"], 7)
        self.assertEqual(control_scheduler["total_control_ops"], 154)
        self.assertEqual(control_scheduler["cycles_per_control_op"], 4)
        self.assertEqual(control_scheduler["total_control_cycles"], 616)
        self.assertEqual([entry["cycle"] for entry in control_scheduler["phase_template"]], list(range(4)))
        self.assertEqual({entry["module"] for entry in control_scheduler["phase_template"]}, {"control_cpu"})
        self.assertEqual({check["status"] for check in control_scheduler["checks"]}, {"pass"})
        self.assertEqual(
            [op["name"] for op in control_scheduler["control_ops"]],
            [
                "input_rms_norm",
                "rope_qk",
                "attention_scores_softmax",
                "post_attention_residual",
                "post_attention_rms_norm",
                "silu_gate_multiply",
                "post_mlp_residual",
            ],
        )
        self.assertEqual(
            [op["layer_op_slot"] for op in control_scheduler["control_ops"]],
            [0, 4, 5, 7, 8, 11, 13],
        )
        for path in [control_scheduler["scheduler_rtl"], control_scheduler["verilator_tb"], control_scheduler["flist"]]:
            self.assertTrue((REPO_ROOT / path).exists(), path)

        graph_sequencer = json.loads((E1_PIPELINE_OUT / "23_full_checkpoint_graph_sequencer.json").read_text(encoding="utf-8"))
        self.assertEqual(graph_sequencer["schema"], "e1-full-checkpoint-graph-sequencer-v0")
        self.assertEqual(graph_sequencer["status"], "pass")
        self.assertEqual(graph_sequencer["truth_boundary"], "ordered_layer_graph_slot_sequencer_rtl")
        self.assertTrue(graph_sequencer["full_checkpoint_ordered_graph_rtl_lowering"])
        self.assertFalse(graph_sequencer["full_checkpoint_graph_lowering"])
        self.assertFalse(graph_sequencer["full_checkpoint_rtl_execution"])
        self.assertEqual(
            graph_sequencer["scheduler_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer.sv",
        )
        self.assertEqual(
            graph_sequencer["verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer_tb.cpp",
        )
        self.assertEqual(
            graph_sequencer["flist"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_graph_sequencer.f",
        )
        self.assertEqual(graph_sequencer["layers"], 22)
        self.assertEqual(graph_sequencer["slots_per_layer"], 14)
        self.assertEqual(graph_sequencer["total_graph_slots"], 308)
        self.assertEqual(graph_sequencer["linear_slots_per_layer"], 7)
        self.assertEqual(graph_sequencer["control_slots_per_layer"], 7)
        self.assertEqual(graph_sequencer["total_linear_slots"], 154)
        self.assertEqual(graph_sequencer["total_control_slots"], 154)
        self.assertEqual({check["status"] for check in graph_sequencer["checks"]}, {"pass"})
        self.assertEqual([entry["cycle"] for entry in graph_sequencer["phase_template"]], list(range(4)))
        self.assertEqual(
            [entry["name"] for entry in graph_sequencer["slot_entries"]],
            [op["name"] for op in first_layer["ops"]],
        )
        self.assertEqual(
            [entry["ip"] for entry in graph_sequencer["slot_entries"] if entry["kind"] == "linear"],
            ["systolic_array"] * 7,
        )
        self.assertEqual(
            [entry["ip"] for entry in graph_sequencer["slot_entries"] if entry["kind"] != "linear"],
            ["control_cpu"] * 7,
        )
        for path in [graph_sequencer["scheduler_rtl"], graph_sequencer["verilator_tb"], graph_sequencer["flist"]]:
            self.assertTrue((REPO_ROOT / path).exists(), path)

        rtl_top = json.loads((E1_PIPELINE_OUT / "24_full_checkpoint_rtl_top.json").read_text(encoding="utf-8"))
        self.assertEqual(rtl_top["schema"], "e1-full-checkpoint-rtl-top-v0")
        self.assertEqual(rtl_top["status"], "pass")
        self.assertEqual(rtl_top["truth_boundary"], "ordered_graph_slot_dispatch_to_slot_scoped_rtl_engines")
        self.assertTrue(rtl_top["full_checkpoint_ordered_graph_integrated_rtl"])
        self.assertTrue(rtl_top["full_checkpoint_graph_lowering"])
        self.assertFalse(rtl_top["full_checkpoint_rtl_execution"])
        self.assertTrue(rtl_top["full_checkpoint_command_stream_rtl_execution"])
        self.assertFalse(rtl_top["full_checkpoint_numeric_output_equivalence"])
        self.assertEqual(rtl_top["top_rtl"], "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top.sv")
        self.assertEqual(
            rtl_top["linear_slot_engine_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_slot_engine.sv",
        )
        self.assertEqual(
            rtl_top["control_slot_engine_rtl"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_slot_engine.sv",
        )
        self.assertEqual(rtl_top["graph_sequencer_rtl"], graph_sequencer["scheduler_rtl"])
        self.assertEqual(rtl_top["latch_buffer_rtl"], "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv")
        self.assertEqual(rtl_top["systolic_array_rtl"], "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv")
        self.assertEqual(rtl_top["flist"], "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top.f")
        self.assertEqual(rtl_top["verilator_tb"], "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_tb.cpp")
        self.assertEqual(
            rtl_top["full_verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp",
        )
        self.assertEqual(rtl_top["layers"], 22)
        self.assertEqual(rtl_top["total_graph_slots"], 308)
        self.assertEqual(rtl_top["total_linear_slots"], 154)
        self.assertEqual(rtl_top["total_control_slots"], 154)
        self.assertEqual(rtl_top["total_tile_commands_full"], 3784704)
        self.assertEqual(rtl_top["smoke_max_tiles_per_linear_slot"], 2)
        self.assertEqual(rtl_top["smoke_expected_linear_commands"], 308)
        self.assertEqual(rtl_top["full_top_verilator_parameter"], "-GSmokeMaxTilesPerLinearSlot=0")
        self.assertEqual(rtl_top["full_expected_linear_commands"], 3784704)
        self.assertGreater(rtl_top["full_execution_cycle_limit"], rtl_top["total_tile_commands_full"])
        self.assertTrue(rtl_top["full_command_count_rtl_execution"])
        self.assertTrue(rtl_top["full_command_payload_schedule_check"])
        self.assertTrue(rtl_top["full_command_cycle_phase_check"])
        self.assertEqual(rtl_top["full_command_payload_schedule"], command_stream["header"])
        self.assertIn("checks every command payload", rtl_top["full_command_count_rtl_execution_note"])
        self.assertIn("phase 1 scheduler-valid", rtl_top["full_command_count_rtl_execution_note"])
        self.assertIn("does not yet prove TinyLlama numeric", rtl_top["full_command_count_rtl_execution_note"])
        self.assertEqual([entry["cycle"] for entry in rtl_top["phase_template"]], list(range(4)))
        self.assertEqual({check["status"] for check in rtl_top["checks"]}, {"pass"})
        for path in [
            rtl_top["top_rtl"],
            rtl_top["linear_slot_engine_rtl"],
            rtl_top["control_slot_engine_rtl"],
            rtl_top["graph_sequencer_rtl"],
            rtl_top["latch_buffer_rtl"],
            rtl_top["systolic_array_rtl"],
            rtl_top["verilator_tb"],
            rtl_top["full_verilator_tb"],
            rtl_top["flist"],
        ]:
            self.assertTrue((REPO_ROOT / path).exists(), path)

        graph_rtl_proof = json.loads(
            (E1_PIPELINE_OUT / "25_full_checkpoint_graph_rtl_lowering_proof.json").read_text(encoding="utf-8")
        )
        self.assertEqual(graph_rtl_proof["schema"], "e1-full-checkpoint-graph-rtl-lowering-proof-v0")
        self.assertEqual(graph_rtl_proof["status"], "pass")
        self.assertEqual(
            graph_rtl_proof["truth_boundary"],
            "full_graph_slot_dispatch_and_linear_command_stream_rtl_lowering",
        )
        self.assertTrue(graph_rtl_proof["full_checkpoint_graph_lowering"])
        self.assertFalse(graph_rtl_proof["full_checkpoint_rtl_execution"])
        self.assertTrue(graph_rtl_proof["full_checkpoint_command_stream_rtl_execution"])
        self.assertFalse(graph_rtl_proof["full_checkpoint_numeric_output_equivalence"])
        self.assertEqual(graph_rtl_proof["graph"]["layers"], 22)
        self.assertEqual(graph_rtl_proof["graph"]["slots_per_layer"], 14)
        self.assertEqual(graph_rtl_proof["graph"]["total_graph_slots"], 308)
        self.assertEqual(graph_rtl_proof["graph"]["total_linear_slots"], 154)
        self.assertEqual(graph_rtl_proof["graph"]["total_control_slots"], 154)
        self.assertEqual(graph_rtl_proof["graph"]["slot_binding_count"], 308)
        self.assertEqual(graph_rtl_proof["command_stream"]["total_tile_commands"], 3784704)
        self.assertEqual(graph_rtl_proof["command_stream"]["total_rtl_cycles"], 30277632)
        self.assertEqual(graph_rtl_proof["rtl_artifacts"]["top"], rtl_top["top_rtl"])
        self.assertEqual(graph_rtl_proof["rtl_artifacts"]["latch_buffer"], rtl_top["latch_buffer_rtl"])
        self.assertEqual(graph_rtl_proof["rtl_artifacts"]["systolic_array"], rtl_top["systolic_array_rtl"])
        self.assertEqual({check["status"] for check in graph_rtl_proof["checks"]}, {"pass"})
        self.assertEqual(len(graph_rtl_proof["slot_bindings"]), 308)
        self.assertEqual(
            [binding["global_slot"] for binding in graph_rtl_proof["slot_bindings"]],
            list(range(308)),
        )
        self.assertEqual(
            {binding["layer"] for binding in graph_rtl_proof["slot_bindings"]},
            set(range(22)),
        )
        self.assertEqual(
            {
                binding["slot_in_layer"]
                for binding in graph_rtl_proof["slot_bindings"]
            },
            set(range(14)),
        )
        self.assertTrue(
            all(
                binding["global_slot"] == binding["layer"] * 14 + binding["slot_in_layer"]
                for binding in graph_rtl_proof["slot_bindings"]
            )
        )
        self.assertEqual(
            sum(1 for binding in graph_rtl_proof["slot_bindings"] if binding["kind"] == "linear"),
            154,
        )
        self.assertEqual(
            sum(1 for binding in graph_rtl_proof["slot_bindings"] if binding["kind"] != "linear"),
            154,
        )
        first_layer_bindings = [
            binding for binding in graph_rtl_proof["slot_bindings"] if binding["layer"] == 0
        ]
        self.assertEqual(
            [binding["name"] for binding in first_layer_bindings],
            [op["name"] for op in first_layer["ops"]],
        )
        self.assertEqual(
            {
                binding["rtl_engine"]
                for binding in graph_rtl_proof["slot_bindings"]
                if binding["kind"] == "linear"
            },
            {"e1_h1_tinyllama_linear_slot_engine"},
        )
        self.assertEqual(
            {
                binding["rtl_engine"]
                for binding in graph_rtl_proof["slot_bindings"]
                if binding["kind"] != "linear"
            },
            {"e1_h1_tinyllama_control_slot_engine"},
        )
        self.assertIn("No TinyLlama numeric output equivalence", graph_rtl_proof["non_claims"][0])

        full_checkpoint_module_dpi = json.loads(
            (E1_PIPELINE_OUT / "26_full_checkpoint_module_dpi_generation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(full_checkpoint_module_dpi["schema"], "e1-full-checkpoint-module-dpi-generation-report-v0")
        self.assertEqual(full_checkpoint_module_dpi["status"], "pass")
        self.assertEqual(full_checkpoint_module_dpi["generator"], "e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp")
        self.assertEqual(full_checkpoint_module_dpi["manifest"], "e1/e1-h1/generated/full_checkpoint_dpi/manifest.json")
        self.assertEqual(
            full_checkpoint_module_dpi["module_interfaces_doc"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["module_isolation_proof"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["cycle_contract"],
            "e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["module_test_plan"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["verilator_execution_recipe"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["verilator_execution_report"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["readme_cycle_coverage"],
            "e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(
            full_checkpoint_module_dpi["scoreboard"],
            "e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_scoreboard.cpp",
        )
        self.assertEqual(full_checkpoint_module_dpi["module_count"], 7)
        self.assertEqual({check["status"] for check in full_checkpoint_module_dpi["checks"]}, {"pass"})
        self.assertEqual(
            {module["name"] for module in full_checkpoint_module_dpi["modules"]},
            {
                "linear_scheduler",
                "linear_tile_engine",
                "control_scheduler",
                "graph_sequencer",
                "linear_slot_engine",
                "control_slot_engine",
                "full_checkpoint_top",
            },
        )
        for module in full_checkpoint_module_dpi["modules"]:
            self.assertGreater(len(module["input_signals"]), 0, module)
            self.assertGreater(len(module["output_signals"]), 0, module)
            self.assertEqual(module["isolation"]["dut_module"], module["top_module"])
            self.assertEqual(module["isolation"]["rtl_files"], module["rtl"])
            self.assertEqual({check["status"] for check in module["isolation"]["checks"]}, {"pass"})
            self.assertEqual(module["cycle_contract"]["top_module"], module["top_module"])
            self.assertEqual({check["status"] for check in module["cycle_contract"]["checks"]}, {"pass"})
            self.assertEqual(
                [step["cycle"] for step in module["cycle_contract"]["cycles"]],
                list(range(module["cycle_contract"]["cycle_period"])),
            )
            self.assertEqual({check["status"] for check in module["test_plan"]["checks"]}, {"pass"})
            self.assertEqual(
                module["verilator_execution"]["build_command"],
                module["verilator_execution_recipe"]["build_command"],
            )
            self.assertEqual(
                module["verilator_execution"]["run_executable"],
                module["verilator_execution_recipe"]["run_executable"],
            )
            self.assertEqual(module["verilator_execution"]["status"], "pass")
            self.assertEqual(
                module["verilator_execution"]["observed_stdout_markers"],
                module["test_plan"]["verilator"]["expected_stdout_markers"],
            )
            self.assertEqual({check["status"] for check in module["readme_cycle_coverage"]["checks"]}, {"pass"})
            self.assertEqual(
                module["readme_cycle_coverage"]["phase_names"],
                [step["phase"] for step in module["cycle_contract"]["cycles"]],
            )
            self.assertEqual(module["test_plan"]["verilator"]["top_module"], module["probe_module"])
            self.assertEqual(module["test_plan"]["verilator"]["flist"], module["flist"])
            self.assertEqual(module["test_plan"]["verilator"]["main"], module["main"])
            for signal in [*module["input_signals"], *module["output_signals"]]:
                self.assertEqual(set(signal), {"name", "width", "description"})
            for path in [module["probe"], module["main"], module["flist"], *module["rtl"]]:
                self.assertTrue((REPO_ROOT / path).exists(), path)

        e2e = json.loads((E1_PIPELINE_OUT / "27_end_to_end_smoke.json").read_text(encoding="utf-8"))
        self.assertEqual(e2e["schema"], "e1-end-to-end-smoke-v0")
        self.assertEqual(e2e["status"], "pass")
        self.assertEqual(e2e["model_id"], summary["model_id"])
        self.assertEqual(e2e["architecture_id"], summary["architecture_id"])
        self.assertEqual(e2e["stablehlo"]["source"], "e1/generated/pipeline/02_stablehlo.mlir")
        self.assertEqual(e2e["stablehlo"]["normalized"], "e1/generated/pipeline/04_normalized_stablehlo.mlir")
        self.assertEqual(e2e["stablehlo"]["unsupported_ops"], [])
        self.assertEqual(e2e["binding"], "e1/generated/pipeline/05_e1_h1_binding.json")
        self.assertEqual(e2e["device_program"]["status"], "pass")
        self.assertEqual(e2e["device_program"]["run"], "e1/generated/pipeline/07_device_program_run.json")
        self.assertEqual(e2e["chip_model"]["status"], "pass")
        self.assertEqual(e2e["chip_model"]["run"], "e1/generated/pipeline/08_chip_model_run.json")
        self.assertEqual(e2e["generated_soc_top"]["top"], "e1/e1-h1/generated/e1_h1_soc_top.sv")
        self.assertEqual(e2e["implementation_matrix"], "e1/e1-h1/generated/implementation_matrix.json")
        self.assertEqual(e2e["implementation_flists"], implementation_matrix["flists"])
        self.assertEqual(e2e["module_dpi_generation"], "e1/generated/pipeline/12_module_dpi_generation.json")
        self.assertEqual(e2e["module_dpi_manifest"], "e1/e1-h1/generated/module_dpi/manifest.json")
        self.assertEqual(e2e["module_dpi_isolation_proof"], "e1/e1-h1/generated/module_dpi/module_isolation.json")
        self.assertEqual(e2e["module_dpi_cycle_contract"], "e1/e1-h1/generated/module_dpi/cycle_contract.json")
        self.assertEqual(e2e["module_dpi_test_plan"], "e1/e1-h1/generated/module_dpi/module_test_plan.json")
        self.assertEqual(
            e2e["module_dpi_verilator_execution_recipe"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            e2e["module_dpi_verilator_execution_report"],
            "e1/e1-h1/generated/module_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            e2e["module_dpi_readme_cycle_coverage"],
            "e1/e1-h1/generated/module_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(e2e["rtl_lowering"], "e1/generated/pipeline/15_rtl_lowering.json")
        self.assertEqual(e2e["rtl_lowering_status"], "pass")
        self.assertEqual(e2e["tinyllama_imp2_coverage"], "e1/generated/pipeline/16_tinyllama_imp2_coverage.json")
        self.assertEqual(
            e2e["full_tinyllama_checkpoint_execution"],
            "e1/generated/pipeline/17_full_tinyllama_checkpoint_execution.json",
        )
        self.assertEqual(
            e2e["full_tinyllama_checkpoint_execution_status"],
            full_checkpoint["status"],
        )
        self.assertFalse(e2e["full_tinyllama_checkpoint_implemented"])
        self.assertEqual(
            e2e["full_checkpoint_rtl_lowering_plan"],
            "e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json",
        )
        self.assertEqual(e2e["full_checkpoint_rtl_lowering_status"], "planned")
        self.assertTrue(e2e["full_checkpoint_graph_lowered_to_rtl"])
        self.assertEqual(
            e2e["full_checkpoint_graph_rtl_lowering_proof"],
            "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
        )
        self.assertEqual(e2e["full_checkpoint_graph_rtl_lowering_status"], "pass")
        self.assertTrue(e2e["full_checkpoint_command_stream_rtl_execution"])
        self.assertFalse(e2e["full_checkpoint_numeric_output_equivalence"])
        self.assertEqual(e2e["full_checkpoint_command_stream"], "e1/generated/pipeline/19_full_checkpoint_command_stream.json")
        self.assertEqual(e2e["full_checkpoint_command_stream_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_total_tile_commands"], 3784704)
        self.assertEqual(e2e["full_checkpoint_rtl_cycle_lowering"], "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json")
        self.assertEqual(e2e["full_checkpoint_rtl_cycle_lowering_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_total_rtl_cycles"], 30277632)
        self.assertEqual(e2e["full_checkpoint_tile_engine"], "e1/generated/pipeline/21_full_checkpoint_tile_engine.json")
        self.assertEqual(e2e["full_checkpoint_tile_engine_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_control_scheduler"], "e1/generated/pipeline/22_full_checkpoint_control_scheduler.json")
        self.assertEqual(e2e["full_checkpoint_control_scheduler_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_total_control_ops"], 154)
        self.assertEqual(e2e["full_checkpoint_graph_sequencer"], "e1/generated/pipeline/23_full_checkpoint_graph_sequencer.json")
        self.assertEqual(e2e["full_checkpoint_graph_sequencer_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_total_graph_slots"], 308)
        self.assertEqual(e2e["full_checkpoint_rtl_top"], "e1/generated/pipeline/24_full_checkpoint_rtl_top.json")
        self.assertEqual(e2e["full_checkpoint_rtl_top_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_rtl_top_smoke_max_tiles_per_linear_slot"], 2)
        self.assertEqual(
            e2e["full_checkpoint_rtl_top_full_verilator_tb"],
            "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp",
        )
        self.assertEqual(e2e["full_checkpoint_rtl_top_full_expected_linear_commands"], 3784704)
        self.assertTrue(e2e["full_checkpoint_rtl_top_full_command_count_rtl_execution"])
        self.assertTrue(e2e["full_checkpoint_rtl_top_full_command_payload_schedule_check"])
        self.assertTrue(e2e["full_checkpoint_rtl_top_full_command_cycle_phase_check"])
        self.assertEqual(
            e2e["full_checkpoint_module_dpi_generation"],
            "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json",
        )
        self.assertEqual(e2e["full_checkpoint_module_dpi_manifest"], "e1/e1-h1/generated/full_checkpoint_dpi/manifest.json")
        self.assertEqual(
            e2e["full_checkpoint_module_interfaces_doc"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_isolation_proof"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_cycle_contract"],
            "e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_test_plan"],
            "e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_verilator_execution_recipe"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_verilator_execution_report"],
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json",
        )
        self.assertEqual(
            e2e["full_checkpoint_module_readme_cycle_coverage"],
            "e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json",
        )
        self.assertEqual(e2e["full_checkpoint_module_dpi_status"], "pass")
        self.assertEqual(e2e["full_checkpoint_module_dpi_count"], 7)
        self.assertEqual(e2e["target_package"], "e1/e1-h1/generated/targets/manifest.json")
        self.assertIn("full_tinyllama_checkpoint", {check["name"] for check in e2e["checks"]})
        self.assertEqual({check["status"] for check in e2e["checks"]}, {"pass"})
        self.assertEqual(
            {check["name"] for check in e2e["checks"]},
            {
                "stablehlo_supported",
                "e1_h1_binding",
                "device_program_run",
                "chip_model_run",
                "generated_soc_top",
                "implementation_flists",
                "module_dpi_generation",
                "rtl_lowering",
                "tinyllama_imp2_coverage",
                "full_tinyllama_checkpoint",
                "full_checkpoint_rtl_lowering_plan",
                "full_checkpoint_command_stream",
                "full_checkpoint_rtl_cycle_lowering",
                "full_checkpoint_tile_engine",
                "full_checkpoint_control_scheduler",
                "full_checkpoint_graph_sequencer",
                "full_checkpoint_rtl_top",
                "full_checkpoint_graph_rtl_lowering_proof",
                "full_checkpoint_module_dpi_generation",
                "target_package",
            },
        )
        for artifact in [
            e2e["stablehlo"]["source"],
            e2e["stablehlo"]["export_report"],
            e2e["stablehlo"]["inspection_report"],
            e2e["stablehlo"]["normalized"],
            e2e["binding"],
            e2e["memory_plan"],
            e2e["device_program"]["plan"],
            e2e["device_program"]["run"],
            e2e["chip_model"]["plan"],
            e2e["chip_model"]["run"],
            e2e["l1_5_harness_plan"],
            e2e["hardware_graph"],
            e2e["implementation_matrix"],
            e2e["implementation_flists"]["active"],
            e2e["module_dpi_generation"],
            e2e["module_dpi_manifest"],
            e2e["module_dpi_isolation_proof"],
            e2e["module_dpi_cycle_contract"],
            e2e["module_dpi_test_plan"],
            e2e["module_dpi_verilator_execution_recipe"],
            e2e["module_dpi_verilator_execution_report"],
            e2e["module_dpi_readme_cycle_coverage"],
            e2e["rtl_lowering"],
            e2e["tinyllama_imp2_coverage"],
            e2e["full_tinyllama_checkpoint_execution"],
            e2e["full_checkpoint_rtl_lowering_plan"],
            e2e["full_checkpoint_command_stream"],
            e2e["full_checkpoint_rtl_cycle_lowering"],
            e2e["full_checkpoint_tile_engine"],
            e2e["full_checkpoint_control_scheduler"],
            e2e["full_checkpoint_graph_sequencer"],
            e2e["full_checkpoint_rtl_top"],
            e2e["full_checkpoint_rtl_top_full_verilator_tb"],
            e2e["full_checkpoint_graph_rtl_lowering_proof"],
            e2e["full_checkpoint_module_dpi_generation"],
            e2e["full_checkpoint_module_dpi_manifest"],
            e2e["full_checkpoint_module_interfaces_doc"],
            e2e["full_checkpoint_module_isolation_proof"],
            e2e["full_checkpoint_module_cycle_contract"],
            e2e["full_checkpoint_module_test_plan"],
            e2e["full_checkpoint_module_verilator_execution_recipe"],
            e2e["full_checkpoint_module_verilator_execution_report"],
            e2e["full_checkpoint_module_readme_cycle_coverage"],
            e2e["systemverilog_plan"],
            e2e["target_package_plan"],
            e2e["target_package"],
        ]:
            self.assertTrue((REPO_ROOT / artifact).exists(), artifact)

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

    def test_full_tinyllama_checkpoint_runner_reports_preflight_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "checkpoint.json"
            result = run([
                "python3",
                str(E1_CHECKPOINT.relative_to(REPO_ROOT)),
                "--mode",
                "preflight",
                "--report",
                str(report_path),
            ])
            self.assertIn("PASS e1_full_tinyllama_checkpoint", result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "e1-full-tinyllama-checkpoint-execution-v0")
            self.assertEqual(report["model_id"], "tinyllama-1.1b-chat-v1.0")
            self.assertEqual(report["mode"], "preflight")
            self.assertFalse(report["full_checkpoint_execution"])
            self.assertIn(
                report["status"],
                {"missing_python_dependencies", "missing_checkpoint_cache", "missing_checkpoint_files", "ready"},
            )
            self.assertEqual(report["source"]["repo"], "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
            self.assertIn("torch", report["dependencies"])
            self.assertIn("transformers", report["dependencies"])
            self.assertIn("model", report["checkpoint_files"])
            self.assertIn("tokenizer", report["checkpoint_files"])
            if report["status"] != "ready":
                self.assertTrue(report["missing_dependencies"] or report["missing_checkpoint_files"], report)

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
        self.assertEqual(manifest["implementation_matrix"], "e1/e1-h1/generated/implementation_matrix.json")

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
        self.assertIn("e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv", rtl_files)
        self.assertEqual(len(rtl_files), len(set(rtl_files)))
        for rtl_file in rtl_files:
            self.assertTrue((REPO_ROOT / rtl_file).exists(), rtl_file)

        fpga_filelist = (REPO_ROOT / manifest["fpga"]["filelist"]).read_text(encoding="utf-8").splitlines()
        openroad_filelist = (REPO_ROOT / manifest["openroad"]["filelist"]).read_text(encoding="utf-8").splitlines()
        active_flist = (REPO_ROOT / manifest["implementation_flists"]["active"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(fpga_filelist, rtl_files)
        self.assertEqual(openroad_filelist, rtl_files)
        self.assertEqual(active_flist, rtl_files)
        self.assertEqual(set(manifest["implementation_flists"]["imp1"]), {path.stem for path in IP_DIR.glob("*.json")})
        self.assertEqual(set(manifest["implementation_flists"]["imp2"]), {path.stem for path in IP_DIR.glob("*.json")})
        self.assertTrue(all("/rtl/imp2/" in rtl_file for rtl_file in rtl_files[1:]))

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

    def test_verilator_runs_full_checkpoint_linear_scheduler(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for full checkpoint scheduler smoke")
        scheduler = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_scheduler.sv"
        scheduler_flist = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_scheduler.f"
        scheduler_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_scheduler_tb.cpp"
        tile_engine = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_tile_engine.sv"
        tile_engine_flist = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_tile_engine.f"
        tile_engine_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_tile_engine_tb.cpp"
        control_scheduler = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_control_scheduler.sv"
        control_scheduler_flist = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_control_scheduler.f"
        control_scheduler_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_control_scheduler_tb.cpp"
        graph_sequencer = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_graph_sequencer.sv"
        graph_sequencer_flist = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_graph_sequencer.f"
        graph_sequencer_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_graph_sequencer_tb.cpp"
        full_top = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_full_checkpoint_top.sv"
        full_top_flist = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_full_checkpoint_top.f"
        full_top_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_full_checkpoint_top_tb.cpp"
        full_top_full_tb = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_full_checkpoint_top_full_tb.cpp"
        linear_slot_engine = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_linear_slot_engine.sv"
        control_slot_engine = FULL_CHECKPOINT_GENERATED / "e1_h1_tinyllama_control_slot_engine.sv"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_linear_scheduler",
                "-Mdir",
                str(obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(scheduler_flist.relative_to(REPO_ROOT)),
                str(scheduler_tb.relative_to(REPO_ROOT)),
            ])
            report = json.loads(run([str(obj_dir / "Ve1_h1_tinyllama_linear_scheduler")]).stdout)
            self.assertEqual(report["schema"], "e1-full-checkpoint-rtl-scheduler-smoke-v0")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["checked_commands"], 16)
            self.assertEqual(report["cycles_per_tile_command"], 8)
            self.assertEqual(report["total_tile_commands"], 3784704)
            self.assertEqual(report["issued_commands"], 16)
            tile_obj_dir = Path(tmp) / "obj_tile_engine"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_linear_tile_engine",
                "-Mdir",
                str(tile_obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(tile_engine_flist.relative_to(REPO_ROOT)),
                str(tile_engine_tb.relative_to(REPO_ROOT)),
            ])
            tile_report = json.loads(run([str(tile_obj_dir / "Ve1_h1_tinyllama_linear_tile_engine")]).stdout)
            self.assertEqual(tile_report["schema"], "e1-full-checkpoint-tile-engine-smoke-v0")
            self.assertEqual(tile_report["status"], "pass")
            self.assertEqual(tile_report["checked_commands"], 8)
            self.assertEqual(tile_report["handshakes"], 8)
            self.assertEqual(tile_report["issued_commands"], 8)
            self.assertTrue(tile_report["saw_latched_hold"])
            self.assertTrue(tile_report["saw_array_consume"])
            self.assertTrue(tile_report["saw_scheduler_valid_before_array_valid"])
            control_obj_dir = Path(tmp) / "obj_control_scheduler"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_control_scheduler",
                "-Mdir",
                str(control_obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(control_scheduler_flist.relative_to(REPO_ROOT)),
                str(control_scheduler_tb.relative_to(REPO_ROOT)),
            ])
            control_report = json.loads(run([str(control_obj_dir / "Ve1_h1_tinyllama_control_scheduler")]).stdout)
            self.assertEqual(control_report["schema"], "e1-full-checkpoint-control-scheduler-smoke-v0")
            self.assertEqual(control_report["status"], "pass")
            self.assertEqual(control_report["layers"], 22)
            self.assertEqual(control_report["control_ops_per_layer"], 7)
            self.assertEqual(control_report["total_control_ops"], 154)
            self.assertEqual(control_report["issued_control_ops"], 154)
            self.assertTrue(control_report["saw_backpressure_hold"])
            graph_obj_dir = Path(tmp) / "obj_graph_sequencer"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_graph_sequencer",
                "-Mdir",
                str(graph_obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(graph_sequencer_flist.relative_to(REPO_ROOT)),
                str(graph_sequencer_tb.relative_to(REPO_ROOT)),
            ])
            graph_report = json.loads(run([str(graph_obj_dir / "Ve1_h1_tinyllama_graph_sequencer")]).stdout)
            self.assertEqual(graph_report["schema"], "e1-full-checkpoint-graph-sequencer-smoke-v0")
            self.assertEqual(graph_report["status"], "pass")
            self.assertEqual(graph_report["layers"], 22)
            self.assertEqual(graph_report["slots_per_layer"], 14)
            self.assertEqual(graph_report["total_graph_slots"], 308)
            self.assertEqual(graph_report["launched_control"], 154)
            self.assertEqual(graph_report["launched_linear"], 154)
            self.assertEqual(graph_report["issued_graph_slots"], 308)
            full_top_obj_dir = Path(tmp) / "obj_full_checkpoint_top"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_full_checkpoint_top",
                "-GSmokeMaxTilesPerLinearSlot=2",
                "-Mdir",
                str(full_top_obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(full_top_flist.relative_to(REPO_ROOT)),
                str(full_top_tb.relative_to(REPO_ROOT)),
            ])
            full_top_report = json.loads(run([str(full_top_obj_dir / "Ve1_h1_tinyllama_full_checkpoint_top")]).stdout)
            self.assertEqual(full_top_report["schema"], "e1-full-checkpoint-rtl-top-smoke-v0")
            self.assertEqual(full_top_report["status"], "pass")
            self.assertEqual(full_top_report["layers"], 22)
            self.assertEqual(full_top_report["total_graph_slots"], 308)
            self.assertEqual(full_top_report["launch_linear"], 154)
            self.assertEqual(full_top_report["launch_control"], 154)
            self.assertEqual(full_top_report["smoke_max_tiles_per_linear_slot"], 2)
            self.assertEqual(full_top_report["issued_linear_commands"], 308)
            self.assertEqual(full_top_report["issued_control_ops"], 154)
            self.assertEqual(full_top_report["issued_graph_slots"], 308)
            self.assertTrue(full_top_report["saw_latched_hold"])
            self.assertTrue(full_top_report["saw_array_consume"])
            full_top_full_obj_dir = Path(tmp) / "obj_full_checkpoint_top_full"
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
                "-Wno-WIDTHEXPAND",
                "--top-module",
                "e1_h1_tinyllama_full_checkpoint_top",
                "-GSmokeMaxTilesPerLinearSlot=0",
                "-Mdir",
                str(full_top_full_obj_dir),
                "-CFLAGS",
                "-std=c++17",
                "-f",
                str(full_top_flist.relative_to(REPO_ROOT)),
                str(full_top_full_tb.relative_to(REPO_ROOT)),
            ])
            full_top_full_report = json.loads(run([str(full_top_full_obj_dir / "Ve1_h1_tinyllama_full_checkpoint_top")]).stdout)
            self.assertEqual(full_top_full_report["schema"], "e1-full-checkpoint-rtl-top-full-command-v0")
            self.assertEqual(full_top_full_report["status"], "pass")
            self.assertEqual(full_top_full_report["layers"], 22)
            self.assertEqual(full_top_full_report["total_graph_slots"], 308)
            self.assertEqual(full_top_full_report["launch_linear"], 154)
            self.assertEqual(full_top_full_report["launch_control"], 154)
            self.assertEqual(full_top_full_report["smoke_max_tiles_per_linear_slot"], 0)
            self.assertEqual(full_top_full_report["issued_linear_commands"], 3784704)
            self.assertEqual(full_top_full_report["expected_linear_commands"], 3784704)
            self.assertEqual(full_top_full_report["checked_command_payloads"], 3784704)
            self.assertEqual(full_top_full_report["checked_phase1_scheduler_valids"], 3784704)
            self.assertEqual(full_top_full_report["checked_phase6_array_dones"], 3784704)
            self.assertEqual(full_top_full_report["issued_control_ops"], 154)
            self.assertEqual(full_top_full_report["issued_graph_slots"], 308)
            self.assertLess(full_top_full_report["cycles"], full_top_full_report["cycle_limit"])
            self.assertTrue(full_top_full_report["saw_latched_hold"])
            self.assertTrue(full_top_full_report["saw_array_consume"])
        self.assertTrue(scheduler.exists())
        self.assertTrue(tile_engine.exists())
        self.assertTrue(control_scheduler.exists())
        self.assertTrue(graph_sequencer.exists())
        self.assertTrue(full_top.exists())
        self.assertTrue(full_top_full_tb.exists())
        self.assertTrue(linear_slot_engine.exists())
        self.assertTrue(control_slot_engine.exists())

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

    def test_device_program_compiles_and_runs_host_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "e1_tinyllama_program_host_smoke"
            run(
                [
                    "c++",
                    "-std=c++17",
                    "-DE1_DEVICE_HOST_MODEL",
                    "-I",
                    "e1/code/program",
                    "e1/code/program/e1_tinyllama_program.cpp",
                    "e1/code/program/e1_tinyllama_program_host_smoke.cpp",
                    "-o",
                    str(exe),
                ]
            )
            result = run([str(exe)])
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["writes"], 7)
            self.assertEqual(report["status_reads"], 2)

            schedule_exe = Path(tmp) / "e1_tinyllama_full_schedule_smoke"
            run(
                [
                    "c++",
                    "-std=c++17",
                    "-I",
                    "e1/code/program",
                    "e1/code/program/e1_tinyllama_full_schedule_smoke.cpp",
                    "-o",
                    str(schedule_exe),
                ]
            )
            schedule_report = json.loads(run([str(schedule_exe)]).stdout)
            self.assertEqual(schedule_report["status"], "pass")
            self.assertEqual(schedule_report["layers"], 22)
            self.assertEqual(schedule_report["linear_ops_per_layer"], 7)
            self.assertEqual(schedule_report["commands_per_layer"], 172032)
            self.assertEqual(schedule_report["total_tile_commands"], 3784704)

            rtl_cycle_exe = Path(tmp) / "e1_tinyllama_full_rtl_cycle_smoke"
            run(
                [
                    "c++",
                    "-std=c++17",
                    "-I",
                    "e1/code/program",
                    "e1/code/program/e1_tinyllama_full_rtl_cycle_smoke.cpp",
                    "-o",
                    str(rtl_cycle_exe),
                ]
            )
            rtl_cycle_report = json.loads(run([str(rtl_cycle_exe)]).stdout)
            self.assertEqual(rtl_cycle_report["status"], "pass")
            self.assertEqual(rtl_cycle_report["cycles_per_tile_command"], 8)
            self.assertEqual(rtl_cycle_report["total_tile_commands"], 3784704)
            self.assertEqual(rtl_cycle_report["total_rtl_cycles"], 30277632)


if __name__ == "__main__":
    unittest.main(verbosity=2)
