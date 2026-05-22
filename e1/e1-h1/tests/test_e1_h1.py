#!/usr/bin/env python3
"""E1-H1 tests."""

from __future__ import annotations

import importlib.util
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
GENERATOR = E1_H1 / "tools" / "generate_soc_top.py"
RTL_IP = E1_H1 / "rtl" / "ip"


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


class E1H1Tests(unittest.TestCase):
    def test_architecture_json_contract(self) -> None:
        arch = json.loads(ARCH.read_text(encoding="utf-8"))
        self.assertEqual(arch["example"], "e1")
        self.assertEqual(arch["architecture_id"], "e1-h1")
        self.assertEqual(arch["cpu"]["issue_width"], 3)
        self.assertTrue(arch["cpu"]["bare_metal_only"])
        self.assertTrue(arch["cpu"]["strip_linux_boot_features"])
        self.assertEqual(arch["io"]["external_data_source"]["kind"], "ethernet")
        self.assertEqual(arch["io"]["external_data_source"]["mac_interface"], "rgmii")
        self.assertTrue(arch["io"]["external_data_source"]["digital_only"])
        self.assertEqual(arch["accelerator"]["kind"], "systolic_array")
        self.assertTrue(arch["replaceability"]["required_for_every_module"])

    def test_ip_manifests_are_replaceable_and_connected(self) -> None:
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
            self.assertIn("order", data, path)
            self.assertGreater(len(data["ports"]), 0, path)
            for port in data["ports"]:
                self.assertIn(port["direction"], {"input", "output", "inout"}, path)
                self.assertGreater(int(port["width"]), 0, path)
                self.assertRegex(port["connect"], r"^(top|net)\.[a-zA-Z_][a-zA-Z0-9_]*$")

    def test_generated_soc_top_matches_manifests(self) -> None:
        generator = load_generator()
        expected = generator.generate(ARCH, IP_DIR)
        actual = GENERATED_TOP.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)
        self.assertIn("module e1_h1_soc_top", actual)
        self.assertIn("u_control_cpu", actual)
        self.assertIn("u_rgmii_ethernet_ingress", actual)
        self.assertIn("u_ingress_sram", actual)
        self.assertIn("u_systolic_array", actual)
        self.assertIn("Source of composition: e1/e1-h1/ip/*.json", actual)

    def test_verilator_lints_generated_top_and_mock_ips(self) -> None:
        verilator = shutil.which("verilator")
        self.assertIsNotNone(verilator, "verilator is required for E1-H1 RTL lint")
        rtl_files = sorted(str(path.relative_to(REPO_ROOT)) for path in RTL_IP.glob("*.sv"))
        cmd = [
            verilator,
            "--lint-only",
            "--sv",
            "-Wall",
            "-Wno-DECLFILENAME",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "-Wno-MULTITOP",
            str(GENERATED_TOP.relative_to(REPO_ROOT)),
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
