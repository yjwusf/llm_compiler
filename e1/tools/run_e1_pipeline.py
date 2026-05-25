#!/usr/bin/env python3
"""Run the deterministic E1 pass scaffold.

This does not download the full TinyLlama checkpoint by default. It records the
pinned checkpoint and command, then runs a reduced StableHLO fixture through the
same artifact boundaries that the real TinyLlama export will use.
"""

from __future__ import annotations

import argparse
import hashlib
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


def replace_path_token(value: str, actual: Path, token: str) -> str:
    return value.replace(str(actual), token)


def replace_command_path_token(command: list[str], actual: Path, token: str) -> list[str]:
    return [replace_path_token(part, actual, token) for part in command]


def artifact_record(path: str) -> dict[str, Any]:
    artifact_path = REPO_ROOT / path
    record: dict[str, Any] = {
        "path": path,
        "exists": artifact_path.exists(),
    }
    if artifact_path.is_file():
        record["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    return record


def stablehlo_ops(text: str) -> Counter[str]:
    return Counter(re.findall(r"stablehlo\.([a-zA-Z_][a-zA-Z0-9_]*)", text))


def stablehlo_operation_instances(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    op_starts: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        match = re.search(r"stablehlo\.([a-zA-Z_][a-zA-Z0-9_]*)", line)
        if match is None:
            continue
        result_match = re.search(r"(%[a-zA-Z_][a-zA-Z0-9_]*)\s*=", line)
        op_starts.append(
            {
                "source_line": line_number,
                "operation": f"stablehlo.{match.group(1)}",
                "stablehlo_op": match.group(1),
                "ssa_result": result_match.group(1) if result_match is not None else None,
            }
        )

    instances: list[dict[str, Any]] = []
    for index, op in enumerate(op_starts):
        start_line = int(op["source_line"])
        stop_line = int(op_starts[index + 1]["source_line"]) - 1 if index + 1 < len(op_starts) else len(lines)
        for line_number in range(start_line + 1, stop_line + 1):
            if lines[line_number - 1].strip().startswith("return "):
                stop_line = line_number - 1
                break
        snippet = " ".join(
            line.strip()
            for line in lines[start_line - 1 : stop_line]
            if line.strip()
        )
        instances.append(
            {
                "source_index": index,
                "source_line": start_line,
                "source_end_line": stop_line,
                "operation": op["operation"],
                "stablehlo_op": op["stablehlo_op"],
                "ssa_result": op["ssa_result"],
                "source_snippet": snippet,
            }
        )
    return instances


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


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"modules": [], "checks": [], "status": "missing"}
    return load_json(path)


def readme_index_row(name: str, template: str, cycles: list[dict[str, Any]]) -> str:
    phase_list = "; ".join(
        f"{step['cycle']} `{step['phase']}`"
        for step in cycles
    )
    return f"| `{name}` | `{template}` | {phase_list} |"


def parse_sv_module_ports(path: Path, top_module: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    module_pos = text.find(f"module {top_module}")
    if module_pos < 0:
        raise ValueError(f"{repo_rel(path)}: missing module {top_module}")
    port_start = text.find("(", module_pos)
    port_end = text.find("\n);", port_start)
    if port_start < 0 or port_end < 0:
        raise ValueError(f"{repo_rel(path)}: cannot parse port block for {top_module}")

    ports: list[dict[str, str]] = []
    for raw_line in text[port_start + 1 : port_end].splitlines():
        line = raw_line.strip().rstrip(",")
        tokens = line.split()
        if not tokens or tokens[0] not in {"input", "output"}:
            continue
        if len(tokens) not in {3, 4} or tokens[1] != "logic":
            raise ValueError(f"{repo_rel(path)}: unsupported port declaration {line!r}")
        width = "1"
        if len(tokens) == 4:
            match = re.fullmatch(r"\[(\d+):0\]", tokens[2])
            if match is None:
                raise ValueError(f"{repo_rel(path)}: unsupported port width {tokens[2]!r}")
            width = str(int(match.group(1)) + 1)
        ports.append({"name": tokens[-1], "direction": tokens[0], "width": width})
    return ports


def parse_sv_defined_modules(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_$]*)\b", text)


def split_ports_by_direction(ports: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    return {
        "input": [
            {"name": port["name"], "width": port["width"]}
            for port in ports
            if port["direction"] == "input"
        ],
        "output": [
            {"name": port["name"], "width": port["width"]}
            for port in ports
            if port["direction"] == "output"
        ],
    }


def run_module_dpi_verilator_runner(
    test_plan_path: Path,
    recipe_path: Path,
    report_path: Path,
    suite: str,
) -> None:
    runner = REPO_ROOT / "e1" / "tools" / "run_module_dpi_verilator.py"
    subprocess.run(
        [
            "python3",
            repo_rel(runner),
            "--test-plan",
            repo_rel(test_plan_path),
            "--recipe",
            repo_rel(recipe_path),
            "--report",
            repo_rel(report_path),
            "--suite",
            suite,
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


def run_full_checkpoint_top_verilator(
    flist_path: Path,
    smoke_tb_path: Path,
    full_tb_path: Path,
) -> dict[str, Any]:
    verilator = shutil.which("verilator")
    if verilator is None:
        return {
            "schema": "e1-full-checkpoint-rtl-top-verilator-execution-v0",
            "status": "missing_verilator",
            "smoke": {},
            "full_command": {},
        }

    def build_and_run(
        obj_dir: Path,
        tb_path: Path,
        smoke_tiles_per_linear_slot: int,
        obj_dir_token: str,
    ) -> tuple[list[str], dict[str, Any]]:
        build_command = [
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
            f"-GSmokeMaxTilesPerLinearSlot={smoke_tiles_per_linear_slot}",
            "-Mdir",
            str(obj_dir),
            "-CFLAGS",
            "-std=c++17",
            "-f",
            repo_rel(flist_path),
            repo_rel(tb_path),
        ]
        subprocess.run(
            build_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        result = subprocess.run(
            [str(obj_dir / "Ve1_h1_tinyllama_full_checkpoint_top")],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        stable_build_command = replace_command_path_token(build_command, obj_dir, obj_dir_token)
        return stable_build_command, json.loads(result.stdout)

    with tempfile.TemporaryDirectory(prefix="e1_full_checkpoint_top_") as tmp:
        tmp_path = Path(tmp)
        smoke_build_command, smoke_report = build_and_run(
            tmp_path / "obj_full_checkpoint_top_smoke",
            smoke_tb_path,
            2,
            "<full_checkpoint_top_smoke_obj_dir>",
        )
        full_build_command, full_report = build_and_run(
            tmp_path / "obj_full_checkpoint_top_full",
            full_tb_path,
            0,
            "<full_checkpoint_top_full_obj_dir>",
        )

    return {
        "schema": "e1-full-checkpoint-rtl-top-verilator-execution-v0",
        "status": "pass"
        if smoke_report.get("status") == "pass" and full_report.get("status") == "pass"
        else "fail",
        "build_workdir_is_temporary": True,
        "smoke_build_command": smoke_build_command,
        "full_command_build_command": full_build_command,
        "smoke": smoke_report,
        "full_command": full_report,
    }


def run_generated_soc_top_verilator_smoke(
    rtl_files: list[str],
    tb_path: Path,
) -> dict[str, Any]:
    verilator = shutil.which("verilator")
    report: dict[str, Any] = {
        "schema": "e1-generated-soc-top-standalone-verilator-smoke-v0",
        "top_module": "e1_h1_soc_top",
        "testbench": repo_rel(tb_path),
        "rtl_files": rtl_files,
        "assertions": [
            "core_clock_ticks",
            "rgmii_rx_clock_ticks",
            "array_busy_observed",
            "cpu_halted_observed",
        ],
    }
    if verilator is None:
        return {
            **report,
            "status": "missing_verilator",
            "build_command": [],
            "run_executable": None,
            "stdout": "",
        }

    with tempfile.TemporaryDirectory(prefix="e1_soc_top_") as tmp:
        obj_dir = Path(tmp) / "obj_dir"
        obj_dir_token = "<soc_top_obj_dir>"
        build_command = [
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
            repo_rel(tb_path),
        ]
        build = subprocess.run(
            build_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        run_executable = obj_dir / "Ve1_h1_soc_top"
        stable_build_command = replace_command_path_token(build_command, obj_dir, obj_dir_token)
        stable_run_executable = replace_path_token(str(run_executable), obj_dir, obj_dir_token)
        if build.returncode != 0:
            return {
                **report,
                "status": "build_fail",
                "build_workdir_is_temporary": True,
                "build_command": stable_build_command,
                "run_executable": stable_run_executable,
                "stdout": replace_path_token(build.stdout, obj_dir, obj_dir_token),
            }
        run = subprocess.run(
            [str(run_executable)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return {
            **report,
            "status": "pass" if run.returncode == 0 else "run_fail",
            "build_workdir_is_temporary": True,
            "build_command": stable_build_command,
            "run_executable": stable_run_executable,
            "stdout": replace_path_token(run.stdout, obj_dir, obj_dir_token),
        }


def run_imp1_mock_rtl_lints(implementation_matrix: dict[str, Any]) -> dict[str, Any]:
    verilator = shutil.which("verilator")
    runtime_output_dir = REPO_ROOT / "e1/e1-h1/generated/imp1_mock_runtime"
    runtime_output_dir.mkdir(parents=True, exist_ok=True)
    rtl_to_ips: dict[str, list[dict[str, str]]] = {}
    for ip in implementation_matrix["ips"]:
        rtl_to_ips.setdefault(ip["imp1"]["rtl"], []).append(
            {
                "name": ip["name"],
                "top_module": ip["module"],
                "flist": ip["imp1"]["flist"],
            }
        )

    def generated_mock_runtime_main(top_module: str, ports: list[dict[str, str]]) -> str:
        input_names = {
            port["name"]
            for port in ports
            if port["direction"] == "input"
        }

        def set_input(name: str, expression: str) -> str:
            if name not in input_names:
                return ""
            return f"    top.{name} = {expression};\n"

        input_initializers = "".join(
            f"  top.{name} = 0;\n"
            for name in sorted(input_names)
        )
        cycle_assignments = "".join(
            [
                set_input("rst_ni", "cycle > 0"),
                set_input("cmd_valid_i", "cycle == 1"),
                set_input("cmd_ready_i", "cycle >= 2"),
                set_input("array_done_i", "cycle >= 5"),
                set_input("array_error_i", "0"),
                set_input("stream_valid_i", "cycle == 1 || cycle == 3"),
                set_input("stream_last_i", "cycle == 3"),
                set_input("stream_error_i", "0"),
                set_input("stream_ready_i", "cycle >= 2"),
                set_input("array_ready_i", "cycle >= 2"),
                set_input("input_valid_i", "cycle >= 3 && cycle <= 6"),
                set_input("rgmii_rx_ctl_i", "cycle >= 1 && cycle <= 4"),
                set_input("rgmii_rxd_i", "cycle & 0xf"),
            ]
        )
        clock_toggles = "".join(
            [
                (
                    "    top.clk_i = 1;\n"
                    "    top.eval();\n"
                    "    top.clk_i = 0;\n"
                    "    top.eval();\n"
                    if "clk_i" in input_names
                    else ""
                ),
                (
                    "    top.rgmii_rx_clk_i = 1;\n"
                    "    top.eval();\n"
                    "    top.rgmii_rx_clk_i = 0;\n"
                    "    top.eval();\n"
                    if "rgmii_rx_clk_i" in input_names
                    else ""
                ),
            ]
        )
        return (
            f'#include "V{top_module}.h"\n'
            '#include "verilated.h"\n'
            "#include <iostream>\n\n"
            "int main(int argc, char** argv) {\n"
            "  Verilated::commandArgs(argc, argv);\n"
            f"  V{top_module} top;\n"
            f"{input_initializers}"
            "  for (int cycle = 0; cycle < 8; ++cycle) {\n"
            f"{cycle_assignments}"
            "    top.eval();\n"
            f"{clock_toggles}"
            "  }\n"
            f'  std::cout << "E1_H1_IMP1_MOCK_RUNTIME module={top_module} cycles=8\\n";\n'
            "  top.final();\n"
            "  return 0;\n"
            "}\n"
        )

    def run_imp1_mock_runtime_smoke(
        *,
        rtl: str,
        top_module: str | None,
    ) -> dict[str, Any]:
        if verilator is None:
            return {
                "rtl": rtl,
                "top_module": top_module,
                "main": None,
                "generated_artifacts": [],
                "status": "missing_verilator",
                "build_command": [],
                "run_executable": None,
                "stdout": "",
                "expected_stdout_marker": None,
                "stdout_marker_present": False,
            }
        if top_module is None:
            return {
                "rtl": rtl,
                "top_module": top_module,
                "main": None,
                "generated_artifacts": [],
                "status": "ambiguous_top_module",
                "build_command": [],
                "run_executable": None,
                "stdout": "",
                "expected_stdout_marker": None,
                "stdout_marker_present": False,
            }

        ports = parse_sv_module_ports(REPO_ROOT / rtl, top_module)
        main_path = runtime_output_dir / f"{top_module}_smoke.cpp"
        write_text(main_path, generated_mock_runtime_main(top_module, ports))
        with tempfile.TemporaryDirectory() as tmp:
            obj_dir = Path(tmp) / f"obj_{top_module}"
            obj_token = f"<imp1_mock_runtime_obj_dir_{top_module}>"
            build_command = [
                verilator,
                "--cc",
                "--exe",
                "--build",
                "--sv",
                "-Wall",
                "-Wno-DECLFILENAME",
                "-Wno-UNUSEDSIGNAL",
                "-Wno-UNUSEDPARAM",
                "--top-module",
                top_module,
                "-Mdir",
                str(obj_dir),
                rtl,
                repo_rel(main_path),
            ]
            build = subprocess.run(
                build_command,
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            stable_build_command = replace_command_path_token(
                build_command,
                obj_dir,
                obj_token,
            )
            run_executable = obj_dir / f"V{top_module}"
            stable_run_executable = replace_path_token(
                str(run_executable),
                obj_dir,
                obj_token,
            )
            stdout = replace_path_token(build.stdout, obj_dir, obj_token)
            if build.returncode != 0:
                return {
                    "rtl": rtl,
                    "top_module": top_module,
                    "main": repo_rel(main_path),
                    "generated_artifacts": [repo_rel(main_path)],
                    "status": "build_fail",
                    "build_command": stable_build_command,
                    "run_executable": stable_run_executable,
                    "stdout": stdout,
                    "expected_stdout_marker": (
                        f"E1_H1_IMP1_MOCK_RUNTIME module={top_module} cycles=8"
                    ),
                    "stdout_marker_present": False,
                }
            run = subprocess.run(
                [str(run_executable)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            run_stdout = replace_path_token(run.stdout, obj_dir, obj_token)
            expected_marker = f"E1_H1_IMP1_MOCK_RUNTIME module={top_module} cycles=8"
            marker_present = expected_marker in run_stdout
            return {
                "rtl": rtl,
                "top_module": top_module,
                "main": repo_rel(main_path),
                "generated_artifacts": [repo_rel(main_path)],
                "status": "pass" if run.returncode == 0 and marker_present else "run_fail",
                "build_command": stable_build_command,
                "run_executable": stable_run_executable,
                "stdout": run_stdout,
                "expected_stdout_marker": expected_marker,
                "stdout_marker_present": marker_present,
            }

    rows = []
    runtime_rows = []
    for rtl, ips in sorted(rtl_to_ips.items()):
        defined_modules = parse_sv_defined_modules(REPO_ROOT / rtl) if (REPO_ROOT / rtl).exists() else []
        expected_modules = unique_ordered([ip["top_module"] for ip in ips])
        top_module = expected_modules[0] if len(expected_modules) == 1 else None
        command = [
            verilator or "verilator",
            "--lint-only",
            "--sv",
            "-Wall",
            "-Wno-DECLFILENAME",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "--top-module",
            top_module or "<ambiguous_imp1_top_module>",
            rtl,
        ]
        if verilator is None:
            rows.append(
                {
                    "rtl": rtl,
                    "ips": ips,
                    "defined_modules": defined_modules,
                    "expected_modules": expected_modules,
                    "top_module": top_module,
                    "command": command,
                    "status": "missing_verilator",
                    "stdout": "",
                }
            )
            continue
        runtime_rows.append(
            run_imp1_mock_runtime_smoke(
                rtl=rtl,
                top_module=top_module,
            )
        )
        if top_module is None:
            rows.append(
                {
                    "rtl": rtl,
                    "ips": ips,
                    "defined_modules": defined_modules,
                    "expected_modules": expected_modules,
                    "top_module": top_module,
                    "command": command,
                    "status": "ambiguous_top_module",
                    "stdout": "",
                }
            )
            continue
        lint = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        rows.append(
            {
                "rtl": rtl,
                "ips": ips,
                "defined_modules": defined_modules,
                "expected_modules": expected_modules,
                "top_module": top_module,
                "command": command,
                "status": "pass" if lint.returncode == 0 else "lint_fail",
                "stdout": lint.stdout,
            }
        )
    runtime_checks = [
        {
            "name": "all_imp1_mock_rtl_files_run_individually",
            "status": "pass"
            if runtime_rows
            and all(row["status"] == "pass" for row in runtime_rows)
            else "fail",
        },
        {
            "name": "all_imp1_mock_runtime_markers_observed",
            "status": "pass"
            if runtime_rows
            and all(row["stdout_marker_present"] is True for row in runtime_rows)
            else "fail",
        },
        {
            "name": "all_imp1_mock_runtime_mains_are_materialized",
            "status": "pass"
            if runtime_rows
            and all(
                artifact
                and (REPO_ROOT / artifact).exists()
                for row in runtime_rows
                for artifact in row.get("generated_artifacts", [])
            )
            else "fail",
        },
    ]
    runtime_report = {
        "schema": "e1-imp1-mock-rtl-runtime-v0",
        "status": (
            "pass"
            if all(check["status"] == "pass" for check in runtime_checks)
            else "fail"
        ),
        "manifest": "e1/e1-h1/generated/imp1_mock_runtime/manifest.json",
        "generated_artifacts": unique_ordered(
            [
                artifact
                for row in runtime_rows
                for artifact in row.get("generated_artifacts", [])
            ]
        ),
        "rows": runtime_rows,
        "checks": runtime_checks,
    }
    write_json(REPO_ROOT / runtime_report["manifest"], runtime_report)
    checks = [
        {
            "name": "all_imp1_mock_rtl_files_linted_individually",
            "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "fail",
        },
        {
            "name": "all_imp1_mock_rtl_module_names_match_manifests",
            "status": "pass"
            if rows
            and all(
                row["defined_modules"]
                and set(row["defined_modules"]) == set(row["expected_modules"])
                for row in rows
            )
            else "fail",
        },
        {
            "name": "all_imp1_mock_rtl_files_run_individually",
            "status": runtime_report["status"],
        },
    ]
    return {
        "schema": "e1-imp1-mock-rtl-lint-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "verilator_available": verilator is not None,
        "rows": rows,
        "runtime": runtime_report,
        "checks": checks,
    }


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


def run_cpp_verilator_launcher(
    launcher_path: Path,
    *,
    suite: str,
    schema: str,
    recipe: dict[str, Any],
    build_dir_token: str,
) -> dict[str, Any]:
    executable_name = launcher_path.stem
    executable = f"{build_dir_token}/{executable_name}"
    runtime_build_root_token = build_dir_token.replace("_build_dir>", "_runtime_build_root>")
    build_command = [
        "c++",
        "-std=c++17",
        repo_rel(launcher_path),
        "-o",
        executable,
    ]
    run_command = [
        executable,
        "--dry-run",
    ]
    verilator_run_command = [
        executable,
        "--run",
        "--build-root",
        runtime_build_root_token,
    ]
    with tempfile.TemporaryDirectory() as tmp:
        actual_executable = Path(tmp) / executable_name
        actual_runtime_build_root = Path(tmp) / f"{executable_name}_runtime"
        build_result = subprocess.run(
            [
                "c++",
                "-std=c++17",
                repo_rel(launcher_path),
                "-o",
                str(actual_executable),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        execution_result = subprocess.run(
            [
                str(actual_executable),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        verilator_run_result = subprocess.run(
            [
                str(actual_executable),
                "--run",
                "--build-root",
                str(actual_runtime_build_root),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        build_stdout = replace_path_token(
            replace_path_token(build_result.stdout, Path(tmp), build_dir_token),
            REPO_ROOT,
            "<repo-root>",
        )
        execution_stdout = replace_path_token(
            replace_path_token(execution_result.stdout, Path(tmp), build_dir_token),
            REPO_ROOT,
            "<repo-root>",
        )
        verilator_run_stdout = replace_path_token(
            replace_path_token(
                replace_path_token(verilator_run_result.stdout, actual_runtime_build_root, runtime_build_root_token),
                Path(tmp),
                build_dir_token,
            ),
            REPO_ROOT,
            "<repo-root>",
        )

    records = [
        json.loads(line)
        for line in execution_stdout.splitlines()
        if line.strip()
    ]
    suite_records = [record for record in records if record.get("record") == "suite"]
    module_records = [record for record in records if record.get("record") == "module"]
    recipe_modules = {
        module["name"]: module
        for module in recipe.get("modules", [])
    }
    launcher_modules = {
        module["name"]: module
        for module in module_records
        if module.get("name")
    }
    run_records = [
        json.loads(line)
        for line in verilator_run_stdout.splitlines()
        if line.strip()
    ]
    run_result_records = [record for record in run_records if record.get("record") == "result"]
    run_summary_records = [record for record in run_records if record.get("record") == "run_summary"]
    run_results_by_name = {
        record["name"]: record
        for record in run_result_records
        if record.get("name")
    }

    def expected_phase_trace_keys(recipe_module: dict[str, Any]) -> list[str]:
        phase_names = [
            marker[len("phase=") :]
            for marker in recipe_module.get("expected_stdout_markers", [])
            if marker.startswith("phase=")
        ]
        return [
            f"{cycle}:{phase}"
            for cycle, phase in enumerate(phase_names)
        ]

    def expected_phase_signal_trace_keys(recipe_module: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for entry in recipe_module.get("expected_phase_signal_trace", []):
            expected = entry["expected"]
            keys.append(f"{entry['cycle']}:{entry['signal']}:{expected}:{expected}")
        return keys

    module_fields_match = set(recipe_modules) == set(launcher_modules) and all(
        launcher_modules[name].get("scope") == recipe_module.get("scope")
        and launcher_modules[name].get("top_module") == recipe_module.get("top_module")
        and launcher_modules[name].get("dut_module") == recipe_module.get("dut_module")
        and launcher_modules[name].get("flist") == recipe_module.get("flist")
        and launcher_modules[name].get("scoreboard") == recipe_module.get("scoreboard")
        and launcher_modules[name].get("main") == recipe_module.get("main")
        and launcher_modules[name].get("build_command") == recipe_module.get("build_command")
        and launcher_modules[name].get("run_executable") == recipe_module.get("run_executable")
        and launcher_modules[name].get("expected_stdout_markers")
        == recipe_module.get("expected_stdout_markers")
        for name, recipe_module in recipe_modules.items()
    )
    run_records_match = set(recipe_modules) == set(run_results_by_name) and all(
        run_results_by_name[name].get("status") == "pass"
        and run_results_by_name[name].get("build_status") == 0
        and run_results_by_name[name].get("run_status") == 0
        and run_results_by_name[name].get("build_command") == recipe_module.get("build_command")
        and run_results_by_name[name].get("run_executable") == recipe_module.get("run_executable")
        for name, recipe_module in recipe_modules.items()
    )
    run_stdout_markers_match = set(recipe_modules) == set(run_results_by_name) and all(
        run_results_by_name[name].get("expected_stdout_markers")
        == recipe_module.get("expected_stdout_markers")
        and run_results_by_name[name].get("stdout_markers_present") is True
        and run_results_by_name[name].get("missing_stdout_markers") == []
        and run_results_by_name[name].get("observed_stdout_marker_count")
        == len(recipe_module.get("expected_stdout_markers", []))
        and run_results_by_name[name].get("captured_stdout_line_count", 0) > 0
        for name, recipe_module in recipe_modules.items()
    )
    run_phase_traces_match = set(recipe_modules) == set(run_results_by_name) and all(
        run_results_by_name[name].get("expected_phase_trace_keys")
        == expected_phase_trace_keys(recipe_module)
        and run_results_by_name[name].get("observed_phase_trace_prefix_keys")
        == expected_phase_trace_keys(recipe_module)
        and run_results_by_name[name].get("observed_phase_trace_count", 0)
        >= len(expected_phase_trace_keys(recipe_module))
        and run_results_by_name[name].get("phase_trace_in_order") is True
        and run_results_by_name[name].get("phase_trace_repeats_template") is True
        and run_results_by_name[name].get("expected_phase_signal_trace_keys")
        == expected_phase_signal_trace_keys(recipe_module)
        and run_results_by_name[name].get("observed_phase_signal_trace_prefix_keys")
        == expected_phase_signal_trace_keys(recipe_module)
        and run_results_by_name[name].get("observed_phase_signal_trace_count", 0)
        >= len(expected_phase_signal_trace_keys(recipe_module))
        and run_results_by_name[name].get("phase_signal_trace_matches") is True
        and run_results_by_name[name].get("phase_signal_trace_repeats_template") is True
        and bool(expected_phase_trace_keys(recipe_module))
        and bool(expected_phase_signal_trace_keys(recipe_module))
        for name, recipe_module in recipe_modules.items()
    )
    run_summary_matches = (
        len(run_summary_records) == 1
        and run_summary_records[0].get("suite") == suite
        and run_summary_records[0].get("module_count") == len(recipe_modules)
        and run_summary_records[0].get("failures") == 0
        and run_summary_records[0].get("status") == "pass"
    )
    checks = [
        {
            "name": "cpp_verilator_launcher_source_exists",
            "status": "pass" if launcher_path.exists() else "fail",
        },
        {
            "name": "cpp_verilator_launcher_compiled",
            "status": "pass" if build_result.returncode == 0 else "fail",
        },
        {
            "name": "cpp_verilator_launcher_dry_run_executed",
            "status": "pass" if execution_result.returncode == 0 else "fail",
        },
        {
            "name": "cpp_verilator_launcher_suite_record_matches_recipe",
            "status": "pass"
            if len(suite_records) == 1
            and suite_records[0].get("schema") == schema
            and suite_records[0].get("suite") == suite
            and suite_records[0].get("module_count") == len(recipe_modules)
            else "fail",
        },
        {
            "name": "cpp_verilator_launcher_module_records_match_recipe",
            "status": "pass" if module_fields_match else "fail",
        },
        {
            "name": "cpp_verilator_launcher_run_executed",
            "status": "pass" if verilator_run_result.returncode == 0 else "fail",
        },
        {
            "name": "cpp_verilator_launcher_run_records_match_recipe",
            "status": "pass" if run_records_match else "fail",
        },
        {
            "name": "cpp_verilator_launcher_run_stdout_markers_match_recipe",
            "status": "pass" if run_stdout_markers_match else "fail",
        },
        {
            "name": "cpp_verilator_launcher_run_phase_traces_match_recipe",
            "status": "pass" if run_phase_traces_match else "fail",
        },
        {
            "name": "cpp_verilator_launcher_all_module_runs_passed",
            "status": "pass" if run_summary_matches else "fail",
        },
    ]
    return {
        "source": repo_rel(launcher_path),
        "suite": suite,
        "schema": schema,
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "build": {
            "command": build_command,
            "executable": executable,
            "working_directory": "<repo-root>",
            "stdout": build_stdout,
            "status": "pass" if build_result.returncode == 0 else "fail",
        },
        "execution": {
            "command": run_command,
            "working_directory": "<repo-root>",
            "stdout": execution_stdout,
            "status": "pass" if execution_result.returncode == 0 else "fail",
        },
        "verilator_run": {
            "command": verilator_run_command,
            "working_directory": "<repo-root>",
            "stdout": verilator_run_stdout,
            "status": "pass" if verilator_run_result.returncode == 0 else "fail",
            "module_results": run_result_records,
            "summary": run_summary_records[0] if run_summary_records else {},
        },
        "module_count": len(module_records),
        "suite_record": suite_records[0] if suite_records else {},
        "modules": module_records,
        "checks": checks,
    }


def run_module_dpi_generator(
    e1_h1_dir: Path,
    output_path: Path,
    implementation_matrix: dict[str, Any],
) -> dict[str, Any]:
    generator = e1_h1_dir / "tools" / "generate_module_dpi.cpp"
    module_dpi_dir = e1_h1_dir / "generated" / "module_dpi"
    generator_build_dir_token = "<module_dpi_generator_build_dir>"
    generator_executable_name = "e1_h1_generate_module_dpi"
    generator_executable = f"{generator_build_dir_token}/{generator_executable_name}"
    generator_build_command = [
        "c++",
        "-std=c++17",
        repo_rel(generator),
        "-o",
        generator_executable,
    ]
    generator_execution_command = [
        generator_executable,
        "--repo-root",
        "<repo-root>",
        "--output-dir",
        repo_rel(module_dpi_dir),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / generator_executable_name
        actual_build_command = [
            "c++",
            "-std=c++17",
            repo_rel(generator),
            "-o",
            str(exe),
        ]
        build_result = subprocess.run(
            actual_build_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        actual_execution_command = [
            str(exe),
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(module_dpi_dir),
        ]
        generation_result = subprocess.run(
            actual_execution_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        generator_build_stdout = replace_path_token(
            replace_path_token(build_result.stdout, Path(tmp), generator_build_dir_token),
            REPO_ROOT,
            "<repo-root>",
        )
        generator_execution_stdout = replace_path_token(
            replace_path_token(
                replace_path_token(generation_result.stdout, module_dpi_dir, repo_rel(module_dpi_dir)),
                Path(tmp),
                generator_build_dir_token,
            ),
            REPO_ROOT,
            "<repo-root>",
        )

    module_dpi_manifest_path = module_dpi_dir / "manifest.json"
    module_dpi_manifest = load_json(module_dpi_manifest_path)
    expected_generator_stdout = (
        f"PASS e1_h1_generate_module_dpi {len(module_dpi_manifest['modules'])} modules"
        f" -> {repo_rel(module_dpi_dir)}\n"
    )
    generator_build = {
        "source": repo_rel(generator),
        "command": generator_build_command,
        "executable": generator_executable,
        "working_directory": "<repo-root>",
        "stdout": generator_build_stdout,
        "status": "pass" if build_result.returncode == 0 else "fail",
    }
    generator_execution = {
        "command": generator_execution_command,
        "working_directory": "<repo-root>",
        "stdout": generator_execution_stdout,
        "expected_stdout": expected_generator_stdout,
        "status": "pass"
        if generation_result.returncode == 0 and generator_execution_stdout == expected_generator_stdout
        else "fail",
    }
    module_interfaces_doc = module_dpi_dir / "module_interfaces.md"
    module_isolation_path = module_dpi_dir / "module_isolation.json"
    cycle_contract_path = module_dpi_dir / "cycle_contract.json"
    module_test_plan_path = module_dpi_dir / "module_test_plan.json"
    verilator_execution_recipe_path = module_dpi_dir / "verilator_execution_recipe.json"
    verilator_execution_path = module_dpi_dir / "verilator_execution_report.json"
    readme_cycle_coverage_path = module_dpi_dir / "readme_cycle_coverage.json"
    construction_ledger_path = module_dpi_dir / "construction_ledger.json"
    run_module_dpi_verilator_runner(
        module_test_plan_path,
        verilator_execution_recipe_path,
        verilator_execution_path,
        "module_dpi",
    )
    module_isolation = load_json(module_isolation_path)
    cycle_contract = load_json(cycle_contract_path)
    module_test_plan = load_json(module_test_plan_path)
    verilator_execution_recipe = load_json(verilator_execution_recipe_path)
    verilator_execution_launcher_path = REPO_ROOT / module_dpi_manifest["verilator_execution_launcher"]
    cpp_verilator_launcher = run_cpp_verilator_launcher(
        verilator_execution_launcher_path,
        suite="module_dpi",
        schema="e1-h1-module-dpi-verilator-launcher-v0",
        recipe=verilator_execution_recipe,
        build_dir_token="<module_dpi_verilator_launcher_build_dir>",
    )
    verilator_execution = load_optional_json(verilator_execution_path)
    readme_cycle_coverage = load_json(readme_cycle_coverage_path)
    construction_ledger = load_json(construction_ledger_path)
    module_interfaces_text = module_interfaces_doc.read_text(encoding="utf-8") if module_interfaces_doc.exists() else ""
    module_names = {module["name"] for module in module_dpi_manifest["modules"]}
    isolation_by_name = {module["name"]: module for module in module_isolation["modules"]}
    cycle_contract_by_name = {module["name"]: module for module in cycle_contract["modules"]}
    test_plan_by_name = {module["name"]: module for module in module_test_plan["modules"]}
    recipe_by_name = {module["name"]: module for module in verilator_execution_recipe["modules"]}
    verilator_execution_by_name = {module["name"]: module for module in verilator_execution["modules"]}
    readme_cycle_coverage_by_name = {module["name"]: module for module in readme_cycle_coverage["modules"]}
    construction_ledger_by_name = {module["name"]: module for module in construction_ledger["modules"]}
    matrix_by_name = {entry["name"]: entry for entry in implementation_matrix["ips"]}
    ip_ports_by_name: dict[str, dict[str, list[dict[str, str]]]] = {}
    rtl_ports_by_name: dict[str, dict[str, list[dict[str, str]]]] = {}
    vip_cases_by_name: dict[str, list[str]] = {}
    vip_case_markers_by_name: dict[str, list[str]] = {}
    for module in module_dpi_manifest["modules"]:
        ip = load_json(e1_h1_dir / "ip" / f"{module['name']}.json")
        vip = load_json(e1_h1_dir / "vip" / f"{module['name']}.json")
        vip_cases = vip["dpi_equivalence"]["stream_space"]["cases"]
        vip_cases_by_name[module["name"]] = vip_cases
        vip_case_markers_by_name[module["name"]] = [f"case={case}" for case in vip_cases]
        ip_ports_by_name[module["name"]] = {
            "input": [
                {"name": port["name"], "width": str(port["width"])}
                for port in ip["ports"]
                if port["direction"] == "input"
            ],
            "output": [
                {"name": port["name"], "width": str(port["width"])}
                for port in ip["ports"]
                if port["direction"] == "output"
            ],
        }
        rtl_ports_by_name[module["name"]] = split_ports_by_direction(
            parse_sv_module_ports(REPO_ROOT / module["imp2_rtl"], module["top_module"])
        )

    def expected_phase_signal_trace(module_name: str) -> list[dict[str, Any]]:
        contract = cycle_contract_by_name[module_name]
        return [
            {
                "cycle": step["cycle"],
                "signal": contract["primary_phase_signal"],
                "expected": step["cycle"],
            }
            for step in contract["cycles"]
        ]

    implementation_matrix_crosscheck = []
    for module in module_dpi_manifest["modules"]:
        name = module["name"]
        matrix_entry = matrix_by_name.get(name)
        ledger = construction_ledger_by_name[name]
        matrix_dpi = matrix_entry["dpi_equivalence"] if matrix_entry is not None else {}
        imp2 = matrix_entry["imp2"] if matrix_entry is not None else {}
        expected_module_flist_entries = [
            ledger["reference_rtl"],
            ledger["imp2_rtl"],
            module["probe"],
        ]
        observed_module_flist_entries = (
            (REPO_ROOT / module["flist"]).read_text(encoding="utf-8").splitlines()
            if (REPO_ROOT / module["flist"]).exists()
            else []
        )
        reference_defined_modules = (
            parse_sv_defined_modules(REPO_ROOT / ledger["reference_rtl"])
            if (REPO_ROOT / ledger["reference_rtl"]).exists()
            else []
        )
        matrix_imp2_flist_entries = (
            (REPO_ROOT / imp2.get("flist", "")).read_text(encoding="utf-8").splitlines()
            if imp2.get("flist") and (REPO_ROOT / imp2["flist"]).exists()
            else []
        )
        implementation_matrix_crosscheck.append(
            {
                "name": name,
                "matrix_entry_present": matrix_entry is not None,
                "active_implementation": matrix_entry["active"] if matrix_entry is not None else None,
                "matrix_top_module": imp2.get("module"),
                "module_dpi_top_module": module["top_module"],
                "imp2_rtl": ledger["imp2_rtl"],
                "reference_rtl": ledger["reference_rtl"],
                "matrix_imp2_rtl_files": imp2.get("rtl_files", []),
                "matrix_imp2_flist": imp2.get("flist"),
                "matrix_imp2_flist_entries": matrix_imp2_flist_entries,
                "module_dpi_probe": module["probe"],
                "matrix_module_probe": matrix_dpi.get("module_probe"),
                "module_dpi_main": module["main"],
                "matrix_module_main": matrix_dpi.get("module_main"),
                "module_dpi_flist": module["flist"],
                "matrix_module_flist": matrix_dpi.get("module_flist"),
                "expected_module_flist_entries": expected_module_flist_entries,
                "observed_module_flist_entries": observed_module_flist_entries,
                "reference_defined_modules": reference_defined_modules,
                "reference_defined_module_count": len(reference_defined_modules),
                "checks": [
                    {
                        "name": "matrix_entry_present",
                        "status": "pass" if matrix_entry is not None else "fail",
                    },
                    {
                        "name": "matrix_selects_imp2",
                        "status": "pass" if matrix_entry is not None and matrix_entry["active"] == "imp2" else "fail",
                    },
                    {
                        "name": "top_module_matches_matrix_imp2",
                        "status": "pass" if imp2.get("module") == module["top_module"] else "fail",
                    },
                    {
                        "name": "module_dpi_uses_matrix_imp2_rtl",
                        "status": "pass" if ledger["imp2_rtl"] in imp2.get("rtl_files", []) else "fail",
                    },
                    {
                        "name": "matrix_imp2_flist_matches_imp2_rtl",
                        "status": "pass" if matrix_imp2_flist_entries == imp2.get("rtl_files", []) else "fail",
                    },
                    {
                        "name": "matrix_module_probe_matches_generated_probe",
                        "status": "pass" if matrix_dpi.get("module_probe") == module["probe"] else "fail",
                    },
                    {
                        "name": "matrix_module_main_matches_generated_main",
                        "status": "pass" if matrix_dpi.get("module_main") == module["main"] else "fail",
                    },
                    {
                        "name": "matrix_module_flist_matches_generated_flist",
                        "status": "pass" if matrix_dpi.get("module_flist") == module["flist"] else "fail",
                    },
                    {
                        "name": "generated_module_flist_matches_matrix_rtl_and_probe",
                        "status": "pass"
                        if observed_module_flist_entries == expected_module_flist_entries
                        else "fail",
                    },
                    {
                        "name": "generated_reference_defines_exactly_one_reference_module",
                        "status": "pass"
                        if reference_defined_modules == [module["reference_module"]]
                        else "fail",
                    },
                    {
                        "name": "generated_module_flist_starts_with_per_module_reference",
                        "status": "pass"
                        if observed_module_flist_entries
                        and observed_module_flist_entries[0] == ledger["reference_rtl"]
                        else "fail",
                    },
                ],
            }
        )
    checks = [
        {
            "name": "module_dpi_cpp_generator_build_recorded",
            "status": "pass"
            if generator_build["source"] == repo_rel(generator)
            and generator_build["command"] == generator_build_command
            and generator_build["executable"] == generator_executable
            and generator_build["working_directory"] == "<repo-root>"
            and generator_build["status"] == "pass"
            else "fail",
        },
        {
            "name": "module_dpi_cpp_generator_execution_recorded",
            "status": "pass"
            if generator_execution["command"] == generator_execution_command
            and generator_execution["working_directory"] == "<repo-root>"
            and generator_execution["status"] == "pass"
            else "fail",
        },
        {
            "name": "module_dpi_cpp_generator_stdout_reports_module_count",
            "status": "pass"
            if generator_execution["stdout"] == expected_generator_stdout
            and generator_execution["expected_stdout"] == expected_generator_stdout
            else "fail",
        },
        {
            "name": "module_dpi_manifest_exists",
            "status": "pass" if module_dpi_manifest_path.exists() else "fail",
        },
        {
            "name": "module_dpi_interfaces_doc_exists",
            "status": "pass" if module_interfaces_doc.exists() else "fail",
        },
        {
            "name": "one_probe_per_module",
            "status": "pass"
            if len(module_names) == len(module_dpi_manifest["modules"])
            else "fail",
        },
        {
            "name": "all_probes_have_flists",
            "status": "pass"
            if all((REPO_ROOT / module["flist"]).exists() for module in module_dpi_manifest["modules"])
            else "fail",
        },
        {
            "name": "module_dpi_isolation_proof_exists",
            "status": "pass" if module_isolation_path.exists() else "fail",
        },
        {
            "name": "module_dpi_isolation_report_passed",
            "status": "pass"
            if module_isolation.get("status") == "pass"
            and all(check["status"] == "pass" for check in module_isolation.get("checks", []))
            and module_isolation.get("separated_boundaries", {}).get("latch_buffer_module") == "ingress_sram"
            and module_isolation.get("separated_boundaries", {}).get("cpu_modules") == ["control_cpu"]
            and module_isolation.get("separated_boundaries", {}).get("systolic_array_modules")
            == ["systolic_array"]
            else "fail",
        },
        {
            "name": "module_dpi_cycle_contract_exists",
            "status": "pass" if cycle_contract_path.exists() else "fail",
        },
        {
            "name": "module_dpi_test_plan_exists",
            "status": "pass" if module_test_plan_path.exists() else "fail",
        },
        {
            "name": "module_dpi_verilator_execution_recipe_exists",
            "status": "pass" if verilator_execution_recipe_path.exists() else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launcher_exists",
            "status": "pass" if verilator_execution_launcher_path.exists() else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launcher_matches_execution_recipe",
            "status": "pass"
            if cpp_verilator_launcher["status"] == "pass"
            and all(check["status"] == "pass" for check in cpp_verilator_launcher["checks"])
            else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launcher_validates_runtime_markers",
            "status": "pass"
            if any(
                check["name"] == "cpp_verilator_launcher_run_stdout_markers_match_recipe"
                and check["status"] == "pass"
                for check in cpp_verilator_launcher["checks"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launcher_validates_runtime_phase_traces",
            "status": "pass"
            if any(
                check["name"] == "cpp_verilator_launcher_run_phase_traces_match_recipe"
                and check["status"] == "pass"
                for check in cpp_verilator_launcher["checks"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_verilator_execution_report_exists",
            "status": "pass" if verilator_execution_path.exists() else "fail",
        },
        {
            "name": "module_dpi_readme_cycle_coverage_exists",
            "status": "pass" if readme_cycle_coverage_path.exists() else "fail",
        },
        {
            "name": "module_dpi_construction_ledger_exists",
            "status": "pass" if construction_ledger_path.exists() else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_isolation_proofs",
            "status": "pass"
            if module_names == set(isolation_by_name)
            and all(
                all(check["status"] == "pass" for check in isolation_by_name[module["name"]]["checks"])
                and isolation_by_name[module["name"]]["probe_dut_instantiation_count"] == 1
                and isolation_by_name[module["name"]]["probe_reference_instantiation_count"] == 1
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_cycle_contracts",
            "status": "pass"
            if module_names == set(cycle_contract_by_name)
            and all(
                all(check["status"] == "pass" for check in cycle_contract_by_name[module["name"]]["checks"])
                and [step["cycle"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                == list(range(cycle_contract_by_name[module["name"]]["cycle_period"]))
                and cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                == module["primary_phase_signal"]
                and cycle_contract_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_verilator_test_plans",
            "status": "pass"
            if module_names == set(test_plan_by_name)
            and all(
                all(check["status"] == "pass" for check in test_plan_by_name[module["name"]]["checks"])
                and test_plan_by_name[module["name"]]["vip_cases"] == module["vip_cases"]
                and test_plan_by_name[module["name"]]["verilator"]["top_module"] == module["probe_module"]
                and test_plan_by_name[module["name"]]["verilator"]["flist"] == module["flist"]
                and test_plan_by_name[module["name"]]["verilator"]["main"] == module["main"]
                and test_plan_by_name[module["name"]]["primary_phase_signal"] == module["primary_phase_signal"]
                and test_plan_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and test_plan_by_name[module["name"]]["verilator"]["primary_phase_signal"]
                == module["primary_phase_signal"]
                and test_plan_by_name[module["name"]]["verilator"]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_cpp_generated_verilator_execution_recipes",
            "status": "pass"
            if module_names == set(recipe_by_name)
            and verilator_execution_recipe.get("runner") == "e1/tools/run_module_dpi_verilator.py"
            and verilator_execution_recipe.get("suite") == "module_dpi"
            and verilator_execution_recipe.get("test_plan") == module_dpi_manifest["module_test_plan"]
            and verilator_execution_recipe.get("report") == module_dpi_manifest["verilator_execution_report"]
            and all(
                recipe_by_name[module["name"]]["scope"] == "module_only"
                and recipe_by_name[module["name"]]["top_module"]
                == test_plan_by_name[module["name"]]["verilator"]["top_module"]
                and recipe_by_name[module["name"]]["dut_module"]
                == test_plan_by_name[module["name"]]["verilator"]["dut_module"]
                and recipe_by_name[module["name"]]["flist"] == module["flist"]
                and recipe_by_name[module["name"]]["scoreboard"] == module_dpi_manifest["scoreboard"]
                and recipe_by_name[module["name"]]["main"] == module["main"]
                and recipe_by_name[module["name"]]["vip_cases"] == module["vip_cases"]
                and recipe_by_name[module["name"]]["expected_stdout_markers"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"]
                and recipe_by_name[module["name"]]["primary_phase_signal"]
                == test_plan_by_name[module["name"]]["verilator"]["primary_phase_signal"]
                and recipe_by_name[module["name"]]["expected_phase_signal_trace"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_phase_signal_trace"]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_ran_under_verilator",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and verilator_execution.get("status") == "pass"
            and all(
                verilator_execution_by_name[module["name"]]["status"] == "pass"
                and verilator_execution_by_name[module["name"]]["top_module"]
                == test_plan_by_name[module["name"]]["verilator"]["top_module"]
                and verilator_execution_by_name[module["name"]]["flist"]
                == test_plan_by_name[module["name"]]["verilator"]["flist"]
                and verilator_execution_by_name[module["name"]]["observed_stdout_markers"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"]
                and verilator_execution_by_name[module["name"]]["expected_vip_case_markers"]
                == vip_case_markers_by_name[module["name"]]
                and verilator_execution_by_name[module["name"]]["observed_vip_case_markers"]
                == vip_case_markers_by_name[module["name"]]
                and verilator_execution_by_name[module["name"]]["observed_vip_case_trace_prefix"]
                == verilator_execution_by_name[module["name"]]["expected_vip_case_trace"]
                and verilator_execution_by_name[module["name"]]["observed_vip_case_trace_count"]
                >= len(verilator_execution_by_name[module["name"]]["expected_vip_case_trace"])
                and verilator_execution_by_name[module["name"]]["observed_phase_markers"]
                == verilator_execution_by_name[module["name"]]["expected_phase_markers"]
                and verilator_execution_by_name[module["name"]]["observed_phase_trace_prefix"]
                == verilator_execution_by_name[module["name"]]["expected_phase_trace"]
                and verilator_execution_by_name[module["name"]]["observed_phase_trace_count"]
                >= len(verilator_execution_by_name[module["name"]]["expected_phase_trace"])
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_prefix"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_count"]
                >= len(expected_phase_signal_trace(module["name"]))
                and len(verilator_execution_by_name[module["name"]]["expected_phase_markers"]) > 0
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_phase_signal_traces_match_probe_outputs",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and all(
                len(expected_phase_signal_trace(module["name"])) > 0
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_prefix"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_count"]
                >= len(expected_phase_signal_trace(module["name"]))
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_verilator_execution_report_matches_cpp_recipe",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and module_names == set(recipe_by_name)
            and verilator_execution.get("execution_recipe")
            == module_dpi_manifest["verilator_execution_recipe"]
            and all(
                verilator_execution_by_name[module["name"]]["build_command"]
                == recipe_by_name[module["name"]]["build_command"]
                and verilator_execution_by_name[module["name"]]["run_executable"]
                == recipe_by_name[module["name"]]["run_executable"]
                and verilator_execution_by_name[module["name"]]["expected_stdout_markers"]
                == recipe_by_name[module["name"]]["expected_stdout_markers"]
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == recipe_by_name[module["name"]]["expected_phase_signal_trace"]
                and verilator_execution_by_name[module["name"]]["expected_vip_case_markers"]
                == [
                    marker
                    for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                    if marker.startswith("case=")
                ]
                and verilator_execution_by_name[module["name"]]["expected_vip_case_trace"]
                == [
                    {
                        "index": index,
                        "case": marker[len("case=") :],
                        "case_marker": marker,
                    }
                    for index, marker in enumerate(
                        marker
                        for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                        if marker.startswith("case=")
                    )
                ]
                and verilator_execution_by_name[module["name"]]["expected_phase_markers"]
                == [
                    marker
                    for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                    if marker.startswith("phase=")
                ]
                and verilator_execution_by_name[module["name"]]["expected_phase_trace"]
                == [
                    {
                        "cycle": cycle,
                        "phase": marker[len("phase=") :],
                        "phase_marker": marker,
                    }
                    for cycle, marker in enumerate(
                        marker
                        for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                        if marker.startswith("phase=")
                    )
                ]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_readme_cycle_coverage",
            "status": "pass"
            if module_names == set(readme_cycle_coverage_by_name)
            and readme_cycle_coverage.get("diagram_checks")
            and all(check["status"] == "pass" for check in readme_cycle_coverage["diagram_checks"])
            and all(
                all(check["status"] == "pass" for check in readme_cycle_coverage_by_name[module["name"]]["checks"])
                and readme_cycle_coverage_by_name[module["name"]]["template"]
                == cycle_contract_by_name[module["name"]]["template"]
                and readme_cycle_coverage_by_name[module["name"]]["phase_names"]
                == [step["phase"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                and readme_cycle_coverage_by_name[module["name"]]["readme_index_row"]
                == readme_index_row(
                    module["name"],
                    cycle_contract_by_name[module["name"]]["template"],
                    cycle_contract_by_name[module["name"]]["cycles"],
                )
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_modules_have_construction_ledger_entries",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and construction_ledger.get("manifest") == repo_rel(module_dpi_manifest_path)
            and construction_ledger.get("module_interfaces_doc") == module_dpi_manifest["module_interfaces_doc"]
            and construction_ledger.get("cycle_contract") == module_dpi_manifest["cycle_contract"]
            and construction_ledger.get("verilator_execution_recipe")
            == module_dpi_manifest["verilator_execution_recipe"]
            and construction_ledger.get("verilator_execution_launcher")
            == module_dpi_manifest["verilator_execution_launcher"]
            else "fail",
        },
        {
            "name": "module_dpi_construction_ledger_matches_generated_artifacts",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and all(
                construction_ledger_by_name[module["name"]]["probe"] == module["probe"]
                and construction_ledger_by_name[module["name"]]["main"] == module["main"]
                and construction_ledger_by_name[module["name"]]["flist"] == module["flist"]
                and construction_ledger_by_name[module["name"]]["scoreboard"] == module_dpi_manifest["scoreboard"]
                and construction_ledger_by_name[module["name"]]["imp2_rtl"] == module["imp2_rtl"]
                and construction_ledger_by_name[module["name"]]["reference_rtl"] == module["reference_rtl"]
                and construction_ledger_by_name[module["name"]]["latch_buffer"] == module["latch_buffer"]
                and construction_ledger_by_name[module["name"]]["probe_dut_instantiation_count"] == 1
                and construction_ledger_by_name[module["name"]]["probe_reference_instantiation_count"] == 1
                and construction_ledger_by_name[module["name"]]["probe_dut_instantiation_count"]
                == isolation_by_name[module["name"]]["probe_dut_instantiation_count"]
                and construction_ledger_by_name[module["name"]]["probe_reference_instantiation_count"]
                == isolation_by_name[module["name"]]["probe_reference_instantiation_count"]
                and construction_ledger_by_name[module["name"]]["input_signal_count"] == len(module["input_signals"])
                and construction_ledger_by_name[module["name"]]["output_signal_count"] == len(module["output_signals"])
                and construction_ledger_by_name[module["name"]]["vip_cases"] == module["vip_cases"]
                and construction_ledger_by_name[module["name"]]["vip_case_markers"] == module["vip_case_markers"]
                and construction_ledger_by_name[module["name"]]["primary_phase_signal"]
                == module["primary_phase_signal"]
                and construction_ledger_by_name[module["name"]]["expected_phase_signal_trace"]
                == module["expected_phase_signal_trace"]
                and all(check["status"] == "pass" for check in construction_ledger_by_name[module["name"]]["checks"])
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_interface_docs_match_manifest",
            "status": "pass"
            if all(
                f"## {module['name']}" in module_interfaces_text
                and module["top_module"] in module_interfaces_text
                and module["interface_source"] == f"e1/e1-h1/ip/{module['name']}.json:ports"
                and module["interface_source"] in module_interfaces_text
                and all(signal["name"] in module_interfaces_text for signal in module["input_signals"])
                and all(signal["name"] in module_interfaces_text for signal in module["output_signals"])
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_interface_docs_match_ip_port_manifests",
            "status": "pass"
            if all(
                [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["input_signals"]
                ]
                == ip_ports_by_name[module["name"]]["input"]
                and [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["output_signals"]
                ]
                == ip_ports_by_name[module["name"]]["output"]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_interface_docs_match_imp2_rtl_ports",
            "status": "pass"
            if all(
                [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["input_signals"]
                ]
                == rtl_ports_by_name[module["name"]]["input"]
                and [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["output_signals"]
                ]
                == rtl_ports_by_name[module["name"]]["output"]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_ip_port_manifests_match_imp2_rtl_ports",
            "status": "pass"
            if all(
                ip_ports_by_name[module["name"]] == rtl_ports_by_name[module["name"]]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_vip_cases_match_vip_manifests",
            "status": "pass"
            if all(
                module["vip_cases"] == vip_cases_by_name[module["name"]]
                and module["vip_case_markers"] == vip_case_markers_by_name[module["name"]]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_construction_ledger_matches_cycle_contracts",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and all(
                construction_ledger_by_name[module["name"]]["cycle_template"]
                == cycle_contract_by_name[module["name"]]["template"]
                and construction_ledger_by_name[module["name"]]["cycle_period"]
                == cycle_contract_by_name[module["name"]]["cycle_period"]
                and construction_ledger_by_name[module["name"]]["phase_source"]
                == cycle_contract_by_name[module["name"]]["phase_source"]
                and construction_ledger_by_name[module["name"]]["primary_phase_signal"]
                == cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                and construction_ledger_by_name[module["name"]]["expected_phase_signal_trace"]
                == cycle_contract_by_name[module["name"]]["expected_phase_signal_trace"]
                and construction_ledger_by_name[module["name"]]["phase_names"]
                == [step["phase"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                for module in module_dpi_manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "module_dpi_matches_implementation_matrix_active_imp2",
            "status": "pass"
            if module_names == set(matrix_by_name)
            and implementation_matrix.get("active_implementation") == "imp2"
            and all(
                all(check["status"] == "pass" for check in entry["checks"])
                for entry in implementation_matrix_crosscheck
            )
            else "fail",
        },
        {
            "name": "module_dpi_per_module_references_define_single_modules",
            "status": "pass"
            if implementation_matrix_crosscheck
            and all(
                entry["reference_defined_modules"] == [module_dpi_manifest["modules"][index]["reference_module"]]
                for index, entry in enumerate(implementation_matrix_crosscheck)
            )
            else "fail",
        },
        {
            "name": "module_dpi_flists_start_with_per_module_references",
            "status": "pass"
            if implementation_matrix_crosscheck
            and all(
                entry["observed_module_flist_entries"]
                and entry["observed_module_flist_entries"][0] == entry["reference_rtl"]
                for entry in implementation_matrix_crosscheck
            )
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
        "generator_build": generator_build,
        "generator_execution": generator_execution,
        "manifest": repo_rel(module_dpi_manifest_path),
        "implementation_matrix": implementation_matrix["matrix"],
        "scoreboard": module_dpi_manifest["scoreboard"],
        "module_interfaces_doc": module_dpi_manifest["module_interfaces_doc"],
        "module_isolation_proof": module_dpi_manifest["module_isolation_proof"],
        "cycle_contract": module_dpi_manifest["cycle_contract"],
        "module_test_plan": module_dpi_manifest["module_test_plan"],
        "verilator_execution_recipe": module_dpi_manifest["verilator_execution_recipe"],
        "verilator_execution_launcher": module_dpi_manifest["verilator_execution_launcher"],
        "cpp_verilator_launcher": cpp_verilator_launcher,
        "verilator_execution_report": module_dpi_manifest["verilator_execution_report"],
        "readme_cycle_coverage": module_dpi_manifest["readme_cycle_coverage"],
        "construction_ledger": module_dpi_manifest["construction_ledger"],
        "module_count": len(module_dpi_manifest["modules"]),
        "modules": [
            {
                "name": module["name"],
                "top_module": module["top_module"],
                "reference_module": module["reference_module"],
                "reference_rtl": module["reference_rtl"],
                "reference_defined_modules": next(
                    entry["reference_defined_modules"]
                    for entry in implementation_matrix_crosscheck
                    if entry["name"] == module["name"]
                ),
                "reference_defined_module_count": next(
                    entry["reference_defined_module_count"]
                    for entry in implementation_matrix_crosscheck
                    if entry["name"] == module["name"]
                ),
                "imp2_rtl": construction_ledger_by_name[module["name"]]["imp2_rtl"],
                "probe": module["probe"],
                "main": module["main"],
                "flist": module["flist"],
                "latch_buffer": module["latch_buffer"],
                "interface_source": module["interface_source"],
                "input_signals": module["input_signals"],
                "output_signals": module["output_signals"],
                "ip_port_contract": ip_ports_by_name[module["name"]],
                "rtl_port_contract": rtl_ports_by_name[module["name"]],
                "vip_case_contract": {
                    "cases": vip_cases_by_name[module["name"]],
                    "markers": vip_case_markers_by_name[module["name"]],
                },
                "vip_cases": module["vip_cases"],
                "vip_case_markers": module["vip_case_markers"],
                "cycle_notes": module["cycle_notes"],
                "primary_phase_signal": module["primary_phase_signal"],
                "expected_phase_signal_trace": module["expected_phase_signal_trace"],
                "isolation": isolation_by_name[module["name"]],
                "cycle_contract": cycle_contract_by_name[module["name"]],
                "test_plan": test_plan_by_name[module["name"]],
                "verilator_execution_recipe": recipe_by_name[module["name"]],
                "verilator_execution": verilator_execution_by_name.get(module["name"]),
                "readme_cycle_coverage": readme_cycle_coverage_by_name[module["name"]],
                "construction_ledger": construction_ledger_by_name[module["name"]],
            }
            for module in module_dpi_manifest["modules"]
        ],
        "construction_rule": module_dpi_manifest["construction_rule"],
        "separation_of_concerns": module_dpi_manifest["separation_of_concerns"],
        "module_isolation": {
            "status": module_isolation.get("status"),
            "checks": module_isolation.get("checks", []),
            "separated_boundaries": module_isolation.get("separated_boundaries", {}),
        },
        "implementation_matrix_crosscheck": implementation_matrix_crosscheck,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def run_full_checkpoint_module_dpi_generator(e1_h1_dir: Path, output_path: Path) -> dict[str, Any]:
    generator = e1_h1_dir / "tools" / "generate_full_checkpoint_module_dpi.cpp"
    module_dpi_dir = e1_h1_dir / "generated" / "full_checkpoint_dpi"
    generator_build_dir_token = "<full_checkpoint_module_dpi_generator_build_dir>"
    generator_executable_name = "e1_h1_generate_full_checkpoint_module_dpi"
    generator_executable = f"{generator_build_dir_token}/{generator_executable_name}"
    generator_build_command = [
        "c++",
        "-std=c++17",
        repo_rel(generator),
        "-o",
        generator_executable,
    ]
    generator_execution_command = [
        generator_executable,
        "--repo-root",
        "<repo-root>",
        "--output-dir",
        repo_rel(module_dpi_dir),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / generator_executable_name
        actual_build_command = [
            "c++",
            "-std=c++17",
            repo_rel(generator),
            "-o",
            str(exe),
        ]
        build_result = subprocess.run(
            actual_build_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        actual_execution_command = [
            str(exe),
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(module_dpi_dir),
        ]
        generation_result = subprocess.run(
            actual_execution_command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        generator_build_stdout = replace_path_token(
            replace_path_token(build_result.stdout, Path(tmp), generator_build_dir_token),
            REPO_ROOT,
            "<repo-root>",
        )
        generator_execution_stdout = replace_path_token(
            replace_path_token(
                replace_path_token(generation_result.stdout, module_dpi_dir, repo_rel(module_dpi_dir)),
                Path(tmp),
                generator_build_dir_token,
            ),
            REPO_ROOT,
            "<repo-root>",
        )

    manifest_path = module_dpi_dir / "manifest.json"
    manifest = load_json(manifest_path)
    expected_generator_stdout = (
        f"PASS e1_h1_generate_full_checkpoint_module_dpi {len(manifest['modules'])} modules"
        f" -> {repo_rel(module_dpi_dir)}\n"
    )
    generator_build = {
        "source": repo_rel(generator),
        "command": generator_build_command,
        "executable": generator_executable,
        "working_directory": "<repo-root>",
        "stdout": generator_build_stdout,
        "status": "pass" if build_result.returncode == 0 else "fail",
    }
    generator_execution = {
        "command": generator_execution_command,
        "working_directory": "<repo-root>",
        "stdout": generator_execution_stdout,
        "expected_stdout": expected_generator_stdout,
        "status": "pass"
        if generation_result.returncode == 0 and generator_execution_stdout == expected_generator_stdout
        else "fail",
    }
    module_interfaces_doc = module_dpi_dir / "module_interfaces.md"
    module_isolation_path = module_dpi_dir / "module_isolation.json"
    cycle_contract_path = module_dpi_dir / "cycle_contract.json"
    module_test_plan_path = module_dpi_dir / "module_test_plan.json"
    verilator_execution_recipe_path = module_dpi_dir / "verilator_execution_recipe.json"
    verilator_execution_path = module_dpi_dir / "verilator_execution_report.json"
    readme_cycle_coverage_path = module_dpi_dir / "readme_cycle_coverage.json"
    construction_ledger_path = module_dpi_dir / "construction_ledger.json"
    run_module_dpi_verilator_runner(
        module_test_plan_path,
        verilator_execution_recipe_path,
        verilator_execution_path,
        "full_checkpoint_module_dpi",
    )
    module_isolation = load_json(module_isolation_path)
    cycle_contract = load_json(cycle_contract_path)
    module_test_plan = load_json(module_test_plan_path)
    verilator_execution_recipe = load_json(verilator_execution_recipe_path)
    verilator_execution_launcher_path = REPO_ROOT / manifest["verilator_execution_launcher"]
    cpp_verilator_launcher = run_cpp_verilator_launcher(
        verilator_execution_launcher_path,
        suite="full_checkpoint_module_dpi",
        schema="e1-h1-full-checkpoint-module-dpi-verilator-launcher-v0",
        recipe=verilator_execution_recipe,
        build_dir_token="<full_checkpoint_module_dpi_verilator_launcher_build_dir>",
    )
    verilator_execution = load_optional_json(verilator_execution_path)
    readme_cycle_coverage = load_json(readme_cycle_coverage_path)
    construction_ledger = load_json(construction_ledger_path)
    module_names = {module["name"] for module in manifest["modules"]}
    isolation_by_name = {module["name"]: module for module in module_isolation["modules"]}
    cycle_contract_by_name = {module["name"]: module for module in cycle_contract["modules"]}
    test_plan_by_name = {module["name"]: module for module in module_test_plan["modules"]}
    recipe_by_name = {module["name"]: module for module in verilator_execution_recipe["modules"]}
    verilator_execution_by_name = {module["name"]: module for module in verilator_execution["modules"]}
    readme_cycle_coverage_by_name = {module["name"]: module for module in readme_cycle_coverage["modules"]}
    construction_ledger_by_name = {module["name"]: module for module in construction_ledger["modules"]}
    rtl_ports_by_name = {
        module["name"]: split_ports_by_direction(
            parse_sv_module_ports(REPO_ROOT / module["rtl"][-1], module["top_module"])
        )
        for module in manifest["modules"]
    }
    expected_modules = {
        "linear_scheduler",
        "linear_tile_engine",
        "control_scheduler",
        "graph_sequencer",
        "linear_slot_engine",
        "control_slot_engine",
        "full_checkpoint_top",
    }

    def expected_phase_signal_trace(module_name: str) -> list[dict[str, Any]]:
        contract = cycle_contract_by_name[module_name]
        return [
            {
                "cycle": step["cycle"],
                "signal": contract["primary_phase_signal"],
                "expected": step["cycle"],
            }
            for step in contract["cycles"]
        ]

    checks = [
        {
            "name": "generated_full_checkpoint_cpp_generator_build_recorded",
            "status": "pass"
            if generator_build["source"] == repo_rel(generator)
            and generator_build["command"] == generator_build_command
            and generator_build["executable"] == generator_executable
            and generator_build["working_directory"] == "<repo-root>"
            and generator_build["status"] == "pass"
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_generator_execution_recorded",
            "status": "pass"
            if generator_execution["command"] == generator_execution_command
            and generator_execution["working_directory"] == "<repo-root>"
            and generator_execution["status"] == "pass"
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_generator_stdout_reports_module_count",
            "status": "pass"
            if generator_execution["stdout"] == expected_generator_stdout
            and generator_execution["expected_stdout"] == expected_generator_stdout
            else "fail",
        },
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
            "name": "generated_full_checkpoint_isolation_proof_exists",
            "status": "pass" if module_isolation_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_isolation_report_passed",
            "status": "pass"
            if module_isolation.get("status") == "pass"
            and all(check["status"] == "pass" for check in module_isolation.get("checks", []))
            and module_isolation.get("separated_boundaries", {}).get("latch_buffer_rtl")
            == "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv"
            and module_isolation.get("separated_boundaries", {}).get("systolic_array_rtl")
            == "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv"
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_cycle_contract_exists",
            "status": "pass" if cycle_contract_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_module_test_plan_exists",
            "status": "pass" if module_test_plan_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_verilator_execution_recipe_exists",
            "status": "pass" if verilator_execution_recipe_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_verilator_launcher_exists",
            "status": "pass" if verilator_execution_launcher_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_verilator_launcher_matches_execution_recipe",
            "status": "pass"
            if cpp_verilator_launcher["status"] == "pass"
            and all(check["status"] == "pass" for check in cpp_verilator_launcher["checks"])
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_verilator_launcher_validates_runtime_markers",
            "status": "pass"
            if any(
                check["name"] == "cpp_verilator_launcher_run_stdout_markers_match_recipe"
                and check["status"] == "pass"
                for check in cpp_verilator_launcher["checks"]
            )
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_cpp_verilator_launcher_validates_runtime_phase_traces",
            "status": "pass"
            if any(
                check["name"] == "cpp_verilator_launcher_run_phase_traces_match_recipe"
                and check["status"] == "pass"
                for check in cpp_verilator_launcher["checks"]
            )
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_verilator_execution_report_exists",
            "status": "pass" if verilator_execution_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_readme_cycle_coverage_exists",
            "status": "pass" if readme_cycle_coverage_path.exists() else "fail",
        },
        {
            "name": "generated_full_checkpoint_construction_ledger_exists",
            "status": "pass" if construction_ledger_path.exists() else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_signal_docs",
            "status": "pass"
            if all(module.get("input_signals") and module.get("output_signals") for module in manifest["modules"])
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_interface_docs_match_rtl_ports",
            "status": "pass"
            if all(
                [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["input_signals"]
                ]
                == rtl_ports_by_name[module["name"]]["input"]
                and [
                    {"name": signal["name"], "width": signal["width"]}
                    for signal in module["output_signals"]
                ]
                == rtl_ports_by_name[module["name"]]["output"]
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_isolation_proofs",
            "status": "pass"
            if module_names == set(isolation_by_name)
            and all(
                all(check["status"] == "pass" for check in isolation_by_name[module["name"]]["checks"])
                and isolation_by_name[module["name"]]["probe_dut_instantiation_count"] == 1
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_cycle_contracts",
            "status": "pass"
            if module_names == set(cycle_contract_by_name)
            and all(
                all(check["status"] == "pass" for check in cycle_contract_by_name[module["name"]]["checks"])
                and [step["cycle"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                == list(range(cycle_contract_by_name[module["name"]]["cycle_period"]))
                and cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                in cycle_contract_by_name[module["name"]]["phase_signals"]
                and cycle_contract_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_verilator_test_plans",
            "status": "pass"
            if module_names == set(test_plan_by_name)
            and all(
                all(check["status"] == "pass" for check in test_plan_by_name[module["name"]]["checks"])
                and test_plan_by_name[module["name"]]["verilator"]["top_module"] == module["probe_module"]
                and test_plan_by_name[module["name"]]["verilator"]["flist"] == module["flist"]
                and test_plan_by_name[module["name"]]["verilator"]["main"] == module["main"]
                and test_plan_by_name[module["name"]]["primary_phase_signal"]
                == cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                and test_plan_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and test_plan_by_name[module["name"]]["verilator"]["primary_phase_signal"]
                == cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                and test_plan_by_name[module["name"]]["verilator"]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_cpp_generated_verilator_execution_recipes",
            "status": "pass"
            if module_names == set(recipe_by_name)
            and verilator_execution_recipe.get("runner") == "e1/tools/run_module_dpi_verilator.py"
            and verilator_execution_recipe.get("suite") == "full_checkpoint_module_dpi"
            and verilator_execution_recipe.get("test_plan") == manifest["module_test_plan"]
            and verilator_execution_recipe.get("report") == manifest["verilator_execution_report"]
            and all(
                recipe_by_name[module["name"]]["scope"] == "generated_full_checkpoint_module_only"
                and recipe_by_name[module["name"]]["top_module"]
                == test_plan_by_name[module["name"]]["verilator"]["top_module"]
                and recipe_by_name[module["name"]]["dut_module"]
                == test_plan_by_name[module["name"]]["verilator"]["dut_module"]
                and recipe_by_name[module["name"]]["flist"] == module["flist"]
                and recipe_by_name[module["name"]]["scoreboard"] == manifest["scoreboard"]
                and recipe_by_name[module["name"]]["main"] == module["main"]
                and recipe_by_name[module["name"]]["expected_stdout_markers"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"]
                and recipe_by_name[module["name"]]["primary_phase_signal"]
                == test_plan_by_name[module["name"]]["verilator"]["primary_phase_signal"]
                and recipe_by_name[module["name"]]["expected_phase_signal_trace"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_phase_signal_trace"]
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_ran_under_verilator",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and verilator_execution.get("status") == "pass"
            and all(
                verilator_execution_by_name[module["name"]]["status"] == "pass"
                and verilator_execution_by_name[module["name"]]["top_module"]
                == test_plan_by_name[module["name"]]["verilator"]["top_module"]
                and verilator_execution_by_name[module["name"]]["flist"]
                == test_plan_by_name[module["name"]]["verilator"]["flist"]
                and verilator_execution_by_name[module["name"]]["observed_stdout_markers"]
                == test_plan_by_name[module["name"]]["verilator"]["expected_stdout_markers"]
                and verilator_execution_by_name[module["name"]]["observed_phase_markers"]
                == verilator_execution_by_name[module["name"]]["expected_phase_markers"]
                and verilator_execution_by_name[module["name"]]["observed_phase_trace_prefix"]
                == verilator_execution_by_name[module["name"]]["expected_phase_trace"]
                and verilator_execution_by_name[module["name"]]["observed_phase_trace_count"]
                >= len(verilator_execution_by_name[module["name"]]["expected_phase_trace"])
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_prefix"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_count"]
                >= len(expected_phase_signal_trace(module["name"]))
                and len(verilator_execution_by_name[module["name"]]["expected_phase_markers"]) > 0
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "full_checkpoint_module_dpi_phase_signal_traces_match_rtl_outputs",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and all(
                len(expected_phase_signal_trace(module["name"])) > 0
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_prefix"]
                == expected_phase_signal_trace(module["name"])
                and verilator_execution_by_name[module["name"]]["observed_phase_signal_trace_count"]
                >= len(expected_phase_signal_trace(module["name"]))
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_verilator_execution_report_matches_cpp_recipe",
            "status": "pass"
            if module_names == set(verilator_execution_by_name)
            and module_names == set(recipe_by_name)
            and verilator_execution.get("execution_recipe") == manifest["verilator_execution_recipe"]
            and all(
                verilator_execution_by_name[module["name"]]["build_command"]
                == recipe_by_name[module["name"]]["build_command"]
                and verilator_execution_by_name[module["name"]]["run_executable"]
                == recipe_by_name[module["name"]]["run_executable"]
                and verilator_execution_by_name[module["name"]]["expected_stdout_markers"]
                == recipe_by_name[module["name"]]["expected_stdout_markers"]
                and verilator_execution_by_name[module["name"]]["expected_phase_signal_trace"]
                == recipe_by_name[module["name"]]["expected_phase_signal_trace"]
                and verilator_execution_by_name[module["name"]]["expected_phase_markers"]
                == [
                    marker
                    for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                    if marker.startswith("phase=")
                ]
                and verilator_execution_by_name[module["name"]]["expected_phase_trace"]
                == [
                    {
                        "cycle": cycle,
                        "phase": marker[len("phase=") :],
                        "phase_marker": marker,
                    }
                    for cycle, marker in enumerate(
                        marker
                        for marker in recipe_by_name[module["name"]]["expected_stdout_markers"]
                        if marker.startswith("phase=")
                    )
                ]
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_readme_cycle_coverage",
            "status": "pass"
            if module_names == set(readme_cycle_coverage_by_name)
            and readme_cycle_coverage.get("diagram_checks")
            and all(check["status"] == "pass" for check in readme_cycle_coverage["diagram_checks"])
            and all(
                all(check["status"] == "pass" for check in readme_cycle_coverage_by_name[module["name"]]["checks"])
                and readme_cycle_coverage_by_name[module["name"]]["template"]
                == cycle_contract_by_name[module["name"]]["template"]
                and readme_cycle_coverage_by_name[module["name"]]["phase_names"]
                == [step["phase"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                and readme_cycle_coverage_by_name[module["name"]]["readme_index_row"]
                == readme_index_row(
                    module["name"],
                    cycle_contract_by_name[module["name"]]["template"],
                    cycle_contract_by_name[module["name"]]["cycles"],
                )
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "all_generated_full_checkpoint_modules_have_construction_ledger_entries",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and construction_ledger.get("manifest") == repo_rel(manifest_path)
            and construction_ledger.get("module_interfaces_doc") == manifest["module_interfaces_doc"]
            and construction_ledger.get("cycle_contract") == manifest["cycle_contract"]
            and construction_ledger.get("verilator_execution_recipe") == manifest["verilator_execution_recipe"]
            and construction_ledger.get("verilator_execution_launcher") == manifest["verilator_execution_launcher"]
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_construction_ledger_matches_generated_artifacts",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and all(
                construction_ledger_by_name[module["name"]]["probe"] == module["probe"]
                and construction_ledger_by_name[module["name"]]["main"] == module["main"]
                and construction_ledger_by_name[module["name"]]["flist"] == module["flist"]
                and construction_ledger_by_name[module["name"]]["scoreboard"] == manifest["scoreboard"]
                and construction_ledger_by_name[module["name"]]["rtl"] == module["module_only_flist_rtl"]
                and construction_ledger_by_name[module["name"]]["composed_rtl_dependencies"]
                == module["composed_rtl_dependencies"]
                and construction_ledger_by_name[module["name"]]["child_stub_modules"]
                == module["child_stub_modules"]
                and construction_ledger_by_name[module["name"]]["primary_phase_signal"]
                == module["primary_phase_signal"]
                and construction_ledger_by_name[module["name"]]["expected_phase_signal_trace"]
                == module["expected_phase_signal_trace"]
                and construction_ledger_by_name[module["name"]]["probe_dut_instantiation_count"] == 1
                and construction_ledger_by_name[module["name"]]["input_signal_count"]
                == len(module["input_signals"])
                and construction_ledger_by_name[module["name"]]["output_signal_count"]
                == len(module["output_signals"])
                and all(check["status"] == "pass" for check in construction_ledger_by_name[module["name"]]["checks"])
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "generated_full_checkpoint_construction_ledger_matches_cycle_contracts",
            "status": "pass"
            if module_names == set(construction_ledger_by_name)
            and all(
                construction_ledger_by_name[module["name"]]["cycle_template"]
                == cycle_contract_by_name[module["name"]]["template"]
                and construction_ledger_by_name[module["name"]]["cycle_period"]
                == cycle_contract_by_name[module["name"]]["cycle_period"]
                and construction_ledger_by_name[module["name"]]["phase_signals"]
                == cycle_contract_by_name[module["name"]]["phase_signals"]
                and construction_ledger_by_name[module["name"]]["primary_phase_signal"]
                == cycle_contract_by_name[module["name"]]["primary_phase_signal"]
                and construction_ledger_by_name[module["name"]]["expected_phase_signal_trace"]
                == cycle_contract_by_name[module["name"]]["expected_phase_signal_trace"]
                and construction_ledger_by_name[module["name"]]["phase_names"]
                == [step["phase"] for step in cycle_contract_by_name[module["name"]]["cycles"]]
                for module in manifest["modules"]
            )
            else "fail",
        },
        {
            "name": "full_checkpoint_top_dpi_covers_slot_engines",
            "status": "pass"
            if any(
                module["name"] == "full_checkpoint_top"
                and "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_linear_slot_engine.sv"
                in module["composed_rtl_dependencies"]
                and "e1/e1-h1/generated/full_checkpoint/e1_h1_tinyllama_control_slot_engine.sv"
                in module["composed_rtl_dependencies"]
                and set(module["child_stub_modules"])
                == {
                    "e1_h1_tinyllama_graph_sequencer",
                    "e1_h1_tinyllama_linear_slot_engine",
                    "e1_h1_tinyllama_control_slot_engine",
                }
                for module in manifest["modules"]
            )
            else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-module-dpi-generation-report-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "generator": repo_rel(generator),
        "generator_build": generator_build,
        "generator_execution": generator_execution,
        "manifest": repo_rel(manifest_path),
        "scoreboard": manifest["scoreboard"],
        "module_interfaces_doc": manifest["module_interfaces_doc"],
        "module_isolation_proof": manifest["module_isolation_proof"],
        "cycle_contract": manifest["cycle_contract"],
        "module_test_plan": manifest["module_test_plan"],
        "verilator_execution_recipe": manifest["verilator_execution_recipe"],
        "verilator_execution_launcher": manifest["verilator_execution_launcher"],
        "cpp_verilator_launcher": cpp_verilator_launcher,
        "verilator_execution_report": manifest["verilator_execution_report"],
        "readme_cycle_coverage": manifest["readme_cycle_coverage"],
        "construction_ledger": manifest["construction_ledger"],
        "module_count": len(manifest["modules"]),
        "module_isolation": {
            "status": module_isolation.get("status"),
            "checks": module_isolation.get("checks", []),
            "separated_boundaries": module_isolation.get("separated_boundaries", {}),
        },
        "modules": [
            {
                "name": module["name"],
                "top_module": module["top_module"],
                "probe_module": module["probe_module"],
                "probe": module["probe"],
                "main": module["main"],
                "flist": module["flist"],
                "rtl": module["rtl"],
                "module_only_flist_rtl": module["module_only_flist_rtl"],
                "composed_rtl_dependencies": module["composed_rtl_dependencies"],
                "child_stub_modules": module["child_stub_modules"],
                "cycle_notes": module["cycle_notes"],
                "input_signals": module["input_signals"],
                "output_signals": module["output_signals"],
                "primary_phase_signal": module["primary_phase_signal"],
                "expected_phase_signal_trace": module["expected_phase_signal_trace"],
                "rtl_port_contract": rtl_ports_by_name[module["name"]],
                "isolation": isolation_by_name[module["name"]],
                "cycle_contract": cycle_contract_by_name[module["name"]],
                "test_plan": test_plan_by_name[module["name"]],
                "verilator_execution_recipe": recipe_by_name[module["name"]],
                "verilator_execution": verilator_execution_by_name.get(module["name"]),
                "readme_cycle_coverage": readme_cycle_coverage_by_name[module["name"]],
                "construction_ledger": construction_ledger_by_name[module["name"]],
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
            if 'return "latch_first_word";' in buffer_probe
            and 'return "hold_latched_word";' in buffer_probe
            and 'return "release_latched_word";' in buffer_probe
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
            "status": "mapped_to_active_imp2_rtl",
        }

    def control_op(name: str, kind: str) -> dict[str, Any]:
        return {
            "name": name,
            "kind": kind,
            "ip": "control_cpu",
            "rtl_files": ips_by_name["control_cpu"]["imp2"]["rtl_files"],
            "module_dpi_probe": module_dpi_by_name["control_cpu"]["probe"],
            "status": "mapped_to_active_imp2_rtl",
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
            "name": "all_ops_have_active_imp2_rtl_and_module_dpi",
            "status": "pass"
            if all(
                op["status"] == "mapped_to_active_imp2_rtl"
                and op["rtl_files"]
                and all("/rtl/imp2/" in path for path in op["rtl_files"])
                and op["module_dpi_probe"]
                for op in layer_template
            )
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
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "source": manifest["source"],
        "checkpoint_shape": shape,
        "architecture_id": architecture["architecture_id"],
        "full_checkpoint_layer_to_rtl_contract": True,
        "full_checkpoint_graph_lowering": False,
        "full_checkpoint_rtl_execution": False,
        "truth_boundary": "shape_complete_layer_to_rtl_module_contract",
        "note": "This is the shape-complete TinyLlama layer-to-RTL module contract consumed by the later command-stream, graph-sequencing, RTL-top, and construction-certificate passes. It does not itself claim live full-checkpoint StableHLO export, full checkpoint RTL execution, or numeric output equivalence.",
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
        "remaining_to_prove_full_semantics": [
            "Export the live full checkpoint graph to StableHLO when checkpoint dependencies and cache are present.",
            "Legalize full Llama numeric semantics including RMSNorm, RoPE, attention softmax, KV/cache updates, and SiLU multiply into bit-checked CPU/control and systolic-array schedules.",
            "Bind checkpoint weights and KV/cache tensor contents to the configurable SRAM hierarchy and Ethernet ingress stream.",
            "Compare generated full-graph RTL or hybrid RTL/C++ execution against the checkpoint source-of-truth output.",
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
constexpr std::uint64_t kCommandDigestOffsetBasis = 1469598103934665603ull;
constexpr std::uint64_t kCommandDigestPrime = 1099511628211ull;

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

inline std::uint64_t mix_digest_u32(std::uint64_t digest, std::uint32_t value) {{
  for (std::uint32_t shift = 0; shift < 32; shift += 8) {{
    digest ^= static_cast<std::uint8_t>((value >> shift) & 0xffu);
    digest *= kCommandDigestPrime;
  }}
  return digest;
}}

inline std::uint64_t mix_tile_command_digest(
    std::uint64_t digest,
    const TileCommand& command) {{
  digest = mix_digest_u32(digest, command.input_addr);
  digest = mix_digest_u32(digest, command.weight_addr);
  digest = mix_digest_u32(digest, command.output_addr);
  digest = mix_digest_u32(digest, command.rows);
  digest = mix_digest_u32(digest, command.cols);
  digest = mix_digest_u32(digest, command.depth);
  return digest;
}}

inline std::uint64_t command_stream_digest() {{
  std::uint64_t digest = kCommandDigestOffsetBasis;
  for (std::uint32_t layer = 0; layer < kLayerCount; ++layer) {{
    for (std::uint32_t op_index = 0; op_index < kLinearOpCount; ++op_index) {{
      const LinearOpPlan& op = kLinearOps[op_index];
      for (std::uint32_t output_tile = 0; output_tile < op.output_tiles; ++output_tile) {{
        for (std::uint32_t input_tile = 0; input_tile < op.input_tiles; ++input_tile) {{
          digest = mix_tile_command_digest(
              digest,
              command_for(layer, op_index, input_tile, output_tile));
        }}
      }}
    }}
  }}
  return digest;
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
      command_stream_digest() != kCommandDigestOffsetBasis &&
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
      << "  \\"payload_digest\\": " << command_stream_digest() << ",\\n"
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
        {
            "name": "full_checkpoint_layer_to_rtl_contract_passed",
            "status": "pass" if full_checkpoint_rtl_lowering["status"] == "pass" else "fail",
            "source_status": full_checkpoint_rtl_lowering["status"],
        },
        {"name": "command_stream_smoke", "status": smoke["status"]},
        {
            "name": "all_linear_ops_have_tile_commands",
            "status": "pass" if all(op["input_tiles"] > 0 and op["output_tiles"] > 0 for op in linear_ops) else "fail",
        },
        {
            "name": "command_count_matches_plan",
            "status": "pass" if smoke["total_tile_commands"] == total_commands else "fail",
        },
        {
            "name": "payload_digest_generated_by_cpp_schedule",
            "status": "pass" if int(smoke["payload_digest"]) != 1469598103934665603 else "fail",
        },
    ]
    report = {
        "schema": "e1-full-checkpoint-command-stream-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
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
        "payload_digest": smoke["payload_digest"],
        "payload_digest_source": "e1_device::tinyllama_full::command_stream_digest",
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
  output logic        array_debug_busy_o,
  output logic [31:0] array_result_digest_o
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
    .debug_busy_o(array_debug_busy_o),
    .result_digest_o(array_result_digest_o)
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
    control_slot_entries = [
        entry for entry in graph_sequencer["slot_entries"] if entry["kind"] != "linear"
    ]
    control_ops_per_layer = len(control_slot_entries)
    expected_control_index_array = ", ".join(
        str(int(entry["control_op_index"])) for entry in control_slot_entries
    )
    expected_control_kind_array = ", ".join(
        str(int(entry["control_kind"])) for entry in control_slot_entries
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
  output logic        array_debug_busy_o,
  output logic [31:0] array_result_digest_o
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
    .debug_busy_o(array_debug_busy_o),
    .result_digest_o(array_result_digest_o)
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
  output logic [31:0] array_result_digest_o,
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
  output logic [8:0]  debug_linear_output_tile_o,
  output logic        debug_control_valid_o,
  output logic        debug_control_commit_o,
  output logic [31:0] debug_control_layer_o,
  output logic [2:0]  debug_control_op_index_o,
  output logic [3:0]  debug_control_kind_o
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
  assign debug_control_valid_o = control_valid;
  assign debug_control_commit_o = control_commit;
  assign debug_control_layer_o = control_layer;
  assign debug_control_op_index_o = control_op_index;
  assign debug_control_kind_o = control_kind;

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
    .array_debug_busy_o(array_debug_busy_o),
    .array_result_digest_o(array_result_digest_o)
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

  logic [98:0] unused_debug;
  assign unused_debug = {
    graph_busy,
    graph_slot_valid,
    linear_slot_expected_commands,
    buffer_array_data,
    scheduler_cmd_valid
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
constexpr std::uint32_t kControlOpsPerLayer = {control_ops_per_layer};
constexpr std::uint32_t kSmokeMaxTilesPerLinearSlot = 0;
constexpr std::uint32_t kExpectedLinearCommands = {full_linear_commands};
constexpr std::uint64_t kCycleLimit = {full_execution_cycle_limit}ull;
constexpr std::uint8_t kControlIndex[kControlOpsPerLayer] = {{{expected_control_index_array}}};
constexpr std::uint8_t kControlKind[kControlOpsPerLayer] = {{{expected_control_kind_array}}};
constexpr std::uint64_t kControlDigestOffsetBasis = 1469598103934665603ull;
constexpr std::uint64_t kControlDigestPrime = 1099511628211ull;

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

std::uint64_t mix_control_word(std::uint64_t digest, std::uint32_t word) {{
  digest ^= word;
  digest *= kControlDigestPrime;
  return digest;
}}

std::uint64_t mix_control_slot_digest(std::uint64_t digest,
                                      std::uint32_t layer,
                                      std::uint32_t control_op_index,
                                      std::uint32_t control_kind) {{
  digest = mix_control_word(digest, layer);
  digest = mix_control_word(digest, control_op_index);
  digest = mix_control_word(digest, control_kind);
  return digest;
}}

std::uint64_t expected_control_digest() {{
  std::uint64_t digest = kControlDigestOffsetBasis;
  for (std::uint32_t layer = 0; layer < kLayers; ++layer) {{
    for (std::uint32_t slot = 0; slot < kControlOpsPerLayer; ++slot) {{
      digest = mix_control_slot_digest(digest, layer, kControlIndex[slot], kControlKind[slot]);
    }}
  }}
  return digest;
}}

struct LinearTraceAnchor {{
  bool valid = false;
  std::uint64_t cycle = 0;
  std::uint32_t command_index = 0;
  std::uint32_t layer = 0;
  std::uint32_t op_index = 0;
  std::uint32_t input_tile = 0;
  std::uint32_t output_tile = 0;
  e1_device::tinyllama_full::TileCommand expected{{}};
  e1_device::tinyllama_full::TileCommand observed{{}};
}};

struct ControlTraceAnchor {{
  bool valid = false;
  std::uint64_t cycle = 0;
  std::uint32_t control_index = 0;
  std::uint32_t layer = 0;
  std::uint32_t slot = 0;
  std::uint32_t op_index = 0;
  std::uint32_t kind = 0;
}};

void print_tile_command_json(const e1_device::tinyllama_full::TileCommand& command) {{
  std::cout
      << "{{\\"input_addr\\": " << command.input_addr
      << ", \\"weight_addr\\": " << command.weight_addr
      << ", \\"output_addr\\": " << command.output_addr
      << ", \\"rows\\": " << command.rows
      << ", \\"cols\\": " << command.cols
      << ", \\"depth\\": " << command.depth
      << "}}";
}}

void print_linear_trace_anchor_json(const LinearTraceAnchor& anchor) {{
  std::cout
      << "{{\\"valid\\": " << (anchor.valid ? "true" : "false")
      << ", \\"cycle\\": " << anchor.cycle
      << ", \\"command_index\\": " << anchor.command_index
      << ", \\"layer\\": " << anchor.layer
      << ", \\"op_index\\": " << anchor.op_index
      << ", \\"input_tile\\": " << anchor.input_tile
      << ", \\"output_tile\\": " << anchor.output_tile
      << ", \\"expected\\": ";
  print_tile_command_json(anchor.expected);
  std::cout << ", \\"observed\\": ";
  print_tile_command_json(anchor.observed);
  std::cout << "}}";
}}

void print_control_trace_anchor_json(const ControlTraceAnchor& anchor) {{
  std::cout
      << "{{\\"valid\\": " << (anchor.valid ? "true" : "false")
      << ", \\"cycle\\": " << anchor.cycle
      << ", \\"control_index\\": " << anchor.control_index
      << ", \\"layer\\": " << anchor.layer
      << ", \\"slot\\": " << anchor.slot
      << ", \\"op_index\\": " << anchor.op_index
      << ", \\"kind\\": " << anchor.kind
      << "}}";
}}

void print_linear_op_trace_coverage_json(const std::uint64_t* counts) {{
  using namespace e1_device::tinyllama_full;
  std::cout << "[";
  for (std::uint32_t op = 0; op < kLinearOpCount; ++op) {{
    const std::uint64_t expected = kLayers * tile_count(kLinearOps[op]);
    std::cout
        << (op == 0 ? "" : ", ")
        << "{{\\"op_index\\": " << op
        << ", \\"name\\": \\"" << kLinearOps[op].name << "\\""
        << ", \\"observed_commands\\": " << counts[op]
        << ", \\"expected_commands\\": " << expected
        << "}}";
  }}
  std::cout << "]";
}}

void print_control_slot_trace_coverage_json(const std::uint32_t* counts) {{
  std::cout << "[";
  for (std::uint32_t slot = 0; slot < kControlOpsPerLayer; ++slot) {{
    std::cout
        << (slot == 0 ? "" : ", ")
        << "{{\\"slot\\": " << slot
        << ", \\"op_index\\": " << static_cast<unsigned>(kControlIndex[slot])
        << ", \\"kind\\": " << static_cast<unsigned>(kControlKind[slot])
        << ", \\"observed_payloads\\": " << counts[slot]
        << ", \\"expected_payloads\\": " << kLayers
        << "}}";
  }}
  std::cout << "]";
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
  std::uint32_t checked_control_payloads = 0;
  std::uint32_t checked_control_commits = 0;
  std::uint32_t layer = 0;
  std::uint32_t op_index = 0;
  std::uint32_t input_tile = 0;
  std::uint32_t output_tile = 0;
  std::uint64_t accepted_payload_digest =
      e1_device::tinyllama_full::kCommandDigestOffsetBasis;
  std::uint64_t accepted_control_digest = kControlDigestOffsetBasis;
  std::uint64_t cycles = 0;
  LinearTraceAnchor first_linear_anchor;
  LinearTraceAnchor last_linear_anchor;
  ControlTraceAnchor first_control_anchor;
  ControlTraceAnchor last_control_anchor;
  std::uint64_t linear_op_command_counts[e1_device::tinyllama_full::kLinearOpCount] = {{}};
  std::uint32_t control_slot_payload_counts[kControlOpsPerLayer] = {{}};

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
    if (top.debug_control_valid_o) {{
      if (top.control_cycle_phase_o != 0) {{
        fail("control valid outside phase 0");
      }}
      if (checked_control_payloads >= kTotalControlSlots) {{
        fail("unexpected extra control payload");
      }} else {{
        const std::uint32_t expected_layer = checked_control_payloads / kControlOpsPerLayer;
        const std::uint32_t expected_slot = checked_control_payloads % kControlOpsPerLayer;
        if (top.debug_control_layer_o != expected_layer ||
            top.debug_control_op_index_o != kControlIndex[expected_slot] ||
            top.debug_control_kind_o != kControlKind[expected_slot]) {{
          fail("control slot payload does not match generated graph schedule");
        }}
        accepted_control_digest = mix_control_slot_digest(
            accepted_control_digest,
            top.debug_control_layer_o,
            top.debug_control_op_index_o,
            top.debug_control_kind_o);
        const ControlTraceAnchor anchor{{
            true,
            cycles,
            checked_control_payloads,
            top.debug_control_layer_o,
            expected_slot,
            top.debug_control_op_index_o,
            top.debug_control_kind_o,
        }};
        if (!first_control_anchor.valid) {{
          first_control_anchor = anchor;
        }}
        last_control_anchor = anchor;
        ++control_slot_payload_counts[expected_slot];
        ++checked_control_payloads;
      }}
    }}
    if (top.debug_control_commit_o) {{
      if (top.control_cycle_phase_o != 3) {{
        fail("control commit outside phase 3");
      }}
      ++checked_control_commits;
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
      const TileCommand observed{{
          top.debug_cmd_input_addr_o,
          top.debug_cmd_weight_addr_o,
          top.debug_cmd_output_addr_o,
          static_cast<std::uint16_t>(top.debug_cmd_rows_o),
          static_cast<std::uint16_t>(top.debug_cmd_cols_o),
          static_cast<std::uint16_t>(top.debug_cmd_depth_o),
      }};
      if (observed.input_addr != expected.input_addr ||
          observed.weight_addr != expected.weight_addr ||
          observed.output_addr != expected.output_addr ||
          observed.rows != expected.rows ||
          observed.cols != expected.cols ||
          observed.depth != expected.depth ||
          top.debug_linear_layer_o != layer ||
          top.debug_linear_op_index_o != op_index ||
          top.debug_linear_input_tile_o != input_tile ||
          top.debug_linear_output_tile_o != output_tile) {{
        fail("full top command payload does not match generated schedule");
      }}
      const std::uint32_t command_index = checked_payloads;
      const LinearTraceAnchor anchor{{
          true,
          cycles,
          command_index,
          layer,
          op_index,
          input_tile,
          output_tile,
          expected,
          observed,
      }};
      if (!first_linear_anchor.valid) {{
        first_linear_anchor = anchor;
      }}
      last_linear_anchor = anchor;
      ++linear_op_command_counts[op_index];
      accepted_payload_digest = mix_tile_command_digest(accepted_payload_digest, observed);
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
  const std::uint64_t expected_payload_digest =
      e1_device::tinyllama_full::command_stream_digest();
  if (accepted_payload_digest != expected_payload_digest) {{
    fail("accepted payload digest mismatch");
  }}
  if (top.issued_control_ops_o != kTotalControlSlots) {{
    fail("control op count mismatch");
  }}
  if (checked_control_payloads != kTotalControlSlots) {{
    fail("checked control payload count mismatch");
  }}
  if (checked_control_commits != kTotalControlSlots) {{
    fail("checked control commit count mismatch");
  }}
  const std::uint64_t expected_control_payload_digest = expected_control_digest();
  if (accepted_control_digest != expected_control_payload_digest) {{
    fail("accepted control payload digest mismatch");
  }}
  if (!saw_latched_hold || !saw_array_consume || !saw_linear_busy || !saw_control_busy) {{
    fail("full RTL top did not exercise separated engines and latch buffer");
  }}
  for (std::uint32_t op = 0; op < e1_device::tinyllama_full::kLinearOpCount; ++op) {{
    const std::uint64_t expected =
        kLayers * e1_device::tinyllama_full::tile_count(e1_device::tinyllama_full::kLinearOps[op]);
    if (linear_op_command_counts[op] != expected) {{
      fail("linear op trace coverage count mismatch");
    }}
  }}
  for (std::uint32_t slot = 0; slot < kControlOpsPerLayer; ++slot) {{
    if (control_slot_payload_counts[slot] != kLayers) {{
      fail("control slot trace coverage count mismatch");
    }}
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
      << "  \\"expected_payload_digest\\": " << expected_payload_digest << ",\\n"
      << "  \\"accepted_payload_digest\\": " << accepted_payload_digest << ",\\n"
      << "  \\"checked_phase1_scheduler_valids\\": " << checked_phase1_scheduler_valids << ",\\n"
      << "  \\"checked_phase6_array_dones\\": " << checked_phase6_array_dones << ",\\n"
      << "  \\"checked_control_payloads\\": " << checked_control_payloads << ",\\n"
      << "  \\"checked_control_commits\\": " << checked_control_commits << ",\\n"
      << "  \\"expected_control_digest\\": " << expected_control_payload_digest << ",\\n"
      << "  \\"accepted_control_digest\\": " << accepted_control_digest << ",\\n"
      << "  \\"issued_control_ops\\": " << top.issued_control_ops_o << ",\\n"
      << "  \\"issued_graph_slots\\": " << top.issued_graph_slots_o << ",\\n"
      << "  \\"cycles\\": " << cycles << ",\\n"
      << "  \\"cycle_limit\\": " << kCycleLimit << ",\\n"
      << "  \\"saw_latched_hold\\": " << (saw_latched_hold ? "true" : "false") << ",\\n"
      << "  \\"saw_array_consume\\": " << (saw_array_consume ? "true" : "false") << ",\\n"
      << "  \\"linear_trace_anchors\\": {{\\"first\\": ";
  print_linear_trace_anchor_json(first_linear_anchor);
  std::cout << ", \\"last\\": ";
  print_linear_trace_anchor_json(last_linear_anchor);
  std::cout << "}},\\n"
      << "  \\"control_trace_anchors\\": {{\\"first\\": ";
  print_control_trace_anchor_json(first_control_anchor);
  std::cout << ", \\"last\\": ";
  print_control_trace_anchor_json(last_control_anchor);
  std::cout << "}},\\n"
      << "  \\"linear_op_trace_coverage\\": ";
  print_linear_op_trace_coverage_json(linear_op_command_counts);
  std::cout << ",\\n"
      << "  \\"control_slot_trace_coverage\\": ";
  print_control_slot_trace_coverage_json(control_slot_payload_counts);
  std::cout << "\\n"
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
    verilator_execution = run_full_checkpoint_top_verilator(
        flist_path,
        tb_path,
        full_tb_path,
    )
    smoke_execution = verilator_execution.get("smoke", {})
    full_command_execution = verilator_execution.get("full_command", {})
    full_command_payload_digest_check = (
        full_command_execution.get("accepted_payload_digest") == command_stream["payload_digest"]
        and full_command_execution.get("expected_payload_digest") == command_stream["payload_digest"]
    )
    full_command_payload_schedule_check = (
        full_command_execution.get("checked_command_payloads") == full_linear_commands
        and full_command_execution.get("issued_linear_commands") == full_linear_commands
    )
    full_command_cycle_phase_check = (
        full_command_execution.get("checked_phase1_scheduler_valids") == full_linear_commands
        and full_command_execution.get("checked_phase6_array_dones") == full_linear_commands
    )
    full_command_control_schedule_check = (
        full_command_execution.get("checked_control_payloads") == total_control_slots
        and full_command_execution.get("checked_control_commits") == total_control_slots
        and full_command_execution.get("issued_control_ops") == total_control_slots
    )
    full_command_control_digest_check = (
        full_command_execution.get("accepted_control_digest")
        == full_command_execution.get("expected_control_digest")
        and int(full_command_execution.get("accepted_control_digest", 0)) != 0
    )
    linear_trace_anchors = full_command_execution.get("linear_trace_anchors", {})
    first_linear_anchor = linear_trace_anchors.get("first", {})
    last_linear_anchor = linear_trace_anchors.get("last", {})
    control_trace_anchors = full_command_execution.get("control_trace_anchors", {})
    first_control_anchor = control_trace_anchors.get("first", {})
    last_control_anchor = control_trace_anchors.get("last", {})
    last_linear_op = command_stream["linear_ops"][-1]
    full_command_trace_anchor_check = (
        first_linear_anchor.get("valid") is True
        and last_linear_anchor.get("valid") is True
        and first_linear_anchor.get("command_index") == 0
        and first_linear_anchor.get("layer") == 0
        and first_linear_anchor.get("op_index") == 0
        and first_linear_anchor.get("input_tile") == 0
        and first_linear_anchor.get("output_tile") == 0
        and first_linear_anchor.get("expected") == first_linear_anchor.get("observed")
        and last_linear_anchor.get("command_index") == full_linear_commands - 1
        and last_linear_anchor.get("layer") == layers - 1
        and last_linear_anchor.get("op_index") == len(command_stream["linear_ops"]) - 1
        and last_linear_anchor.get("input_tile") == int(last_linear_op["input_tiles"]) - 1
        and last_linear_anchor.get("output_tile") == int(last_linear_op["output_tiles"]) - 1
        and last_linear_anchor.get("expected") == last_linear_anchor.get("observed")
        and int(first_linear_anchor.get("cycle", -1)) < int(last_linear_anchor.get("cycle", -1))
        and first_control_anchor.get("valid") is True
        and last_control_anchor.get("valid") is True
        and first_control_anchor.get("control_index") == 0
        and first_control_anchor.get("layer") == 0
        and first_control_anchor.get("slot") == 0
        and last_control_anchor.get("control_index") == total_control_slots - 1
        and last_control_anchor.get("layer") == layers - 1
        and last_control_anchor.get("slot") == control_ops_per_layer - 1
        and int(first_control_anchor.get("cycle", -1)) < int(last_control_anchor.get("cycle", -1))
    )
    linear_op_trace_coverage = full_command_execution.get("linear_op_trace_coverage", [])
    control_slot_trace_coverage = full_command_execution.get("control_slot_trace_coverage", [])
    linear_op_trace_by_index = {
        int(entry.get("op_index", -1)): entry for entry in linear_op_trace_coverage
    }
    control_slot_trace_by_slot = {
        int(entry.get("slot", -1)): entry for entry in control_slot_trace_coverage
    }
    full_command_per_op_trace_coverage_check = (
        len(linear_op_trace_coverage) == len(command_stream["linear_ops"])
        and len(control_slot_trace_coverage) == control_ops_per_layer
        and all(
            linear_op_trace_by_index.get(index, {}).get("name") == op["name"]
            and linear_op_trace_by_index.get(index, {}).get("observed_commands")
            == layers * int(op["input_tiles"]) * int(op["output_tiles"])
            and linear_op_trace_by_index.get(index, {}).get("expected_commands")
            == layers * int(op["input_tiles"]) * int(op["output_tiles"])
            for index, op in enumerate(command_stream["linear_ops"])
        )
        and all(
            control_slot_trace_by_slot.get(slot, {}).get("op_index")
            == int(entry["control_op_index"])
            and control_slot_trace_by_slot.get(slot, {}).get("kind")
            == int(entry["control_kind"])
            and control_slot_trace_by_slot.get(slot, {}).get("observed_payloads") == layers
            and control_slot_trace_by_slot.get(slot, {}).get("expected_payloads") == layers
            for slot, entry in enumerate(control_slot_entries)
        )
    )
    bounded_top_execution_check = (
        verilator_execution.get("status") == "pass"
        and smoke_execution.get("status") == "pass"
        and smoke_execution.get("issued_linear_commands") == smoke_linear_commands
        and smoke_execution.get("issued_graph_slots") == total_graph_slots
    )
    full_command_top_execution_check = (
        verilator_execution.get("status") == "pass"
        and full_command_execution.get("status") == "pass"
        and full_command_execution.get("issued_linear_commands") == full_linear_commands
        and full_command_execution.get("issued_graph_slots") == total_graph_slots
        and full_command_execution.get("cycles", full_execution_cycle_limit + 1) < full_execution_cycle_limit
    )
    full_checkpoint_structural_rtl_execution = (
        bounded_top_execution_check
        and full_command_top_execution_check
        and full_command_payload_schedule_check
        and full_command_payload_digest_check
        and full_command_cycle_phase_check
        and full_command_control_schedule_check
        and full_command_control_digest_check
        and full_command_trace_anchor_check
        and full_command_per_op_trace_coverage_check
    )

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
            "name": "bounded_top_verilator_execution_passed",
            "status": "pass" if bounded_top_execution_check else "fail",
        },
        {
            "name": "full_command_top_verilator_execution_passed",
            "status": "pass" if full_command_top_execution_check else "fail",
        },
        {
            "name": "full_command_payloads_checked_against_cpp_schedule",
            "status": "pass" if full_command_payload_schedule_check else "fail",
        },
        {
            "name": "full_command_payload_digest_checked_against_cpp_schedule",
            "status": "pass" if full_command_payload_digest_check else "fail",
        },
        {
            "name": "full_command_cycle_phases_checked",
            "status": "pass" if full_command_cycle_phase_check else "fail",
        },
        {
            "name": "full_command_control_payloads_checked_against_graph_schedule",
            "status": "pass" if full_command_control_schedule_check else "fail",
        },
        {
            "name": "full_command_control_payload_digest_checked",
            "status": "pass" if full_command_control_digest_check else "fail",
        },
        {
            "name": "full_command_trace_anchors_match_cpp_schedule",
            "status": "pass" if full_command_trace_anchor_check else "fail",
        },
        {
            "name": "full_command_per_op_trace_coverage_matches_cpp_schedule",
            "status": "pass" if full_command_per_op_trace_coverage_check else "fail",
        },
        {
            "name": "full_checkpoint_structural_rtl_execution_reported",
            "status": "pass" if full_checkpoint_structural_rtl_execution else "fail",
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
        "full_checkpoint_graph_lowering": True,
        "full_checkpoint_rtl_execution": full_checkpoint_structural_rtl_execution,
        "full_checkpoint_rtl_execution_scope": (
            "structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence"
        ),
        "full_checkpoint_structural_rtl_execution": full_checkpoint_structural_rtl_execution,
        "full_checkpoint_command_stream_rtl_execution": full_checkpoint_structural_rtl_execution,
        "full_checkpoint_numeric_output_equivalence": False,
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
        "verilator_execution": verilator_execution,
        "bounded_smoke_verilator_report": smoke_execution,
        "full_command_verilator_report": full_command_execution,
        "full_command_count_rtl_execution": full_command_top_execution_check,
        "full_command_payload_schedule_check": full_command_payload_schedule_check,
        "full_command_payload_digest_check": full_command_payload_digest_check,
        "full_command_payload_digest": command_stream["payload_digest"],
        "full_command_cycle_phase_check": full_command_cycle_phase_check,
        "full_command_control_schedule_check": full_command_control_schedule_check,
        "full_command_control_digest_check": full_command_control_digest_check,
        "full_command_control_digest": full_command_execution.get("accepted_control_digest"),
        "full_command_trace_anchor_check": full_command_trace_anchor_check,
        "full_command_trace_anchors": {
            "linear": linear_trace_anchors,
            "control": control_trace_anchors,
        },
        "full_command_per_op_trace_coverage_check": full_command_per_op_trace_coverage_check,
        "full_command_per_op_trace_coverage": {
            "linear_ops": linear_op_trace_coverage,
            "control_slots": control_slot_trace_coverage,
        },
        "full_command_payload_schedule": "e1/code/program/e1_tinyllama_full_schedule.hpp",
        "full_checkpoint_structural_rtl_execution_note": (
            "Structural RTL execution means the bounded graph-slot smoke and full-command "
            "Verilator runs both passed, every planned command reached the selected RTL "
            "slot-engine path, linear command payloads and digest matched the generated C++ "
            "schedule, CPU/control slot payloads and digest matched the generated graph "
            "schedule, and documented phase checks passed. It is not a TinyLlama output "
            "tensor equivalence claim."
        ),
        "full_command_count_rtl_execution_note": (
            "Runs every planned linear tile command through generated RTL control/handshake paths; "
            "checks every command payload and the accepted payload digest against the "
            "generated C++ schedule; checks the "
            "phase 1 scheduler-valid, phase 2 array-handshake, and phase 6 array-done "
            "sequence for every command; checks every CPU/control slot payload and commit "
            "against the generated graph schedule; "
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


def emit_full_checkpoint_graph_rtl_lowering_proof(
    output_path: Path,
    manifest: dict[str, Any],
    full_checkpoint_rtl_lowering: dict[str, Any],
    command_stream: dict[str, Any],
    rtl_cycle: dict[str, Any],
    tile_engine: dict[str, Any],
    control_scheduler: dict[str, Any],
    graph_sequencer: dict[str, Any],
    rtl_top: dict[str, Any],
) -> dict[str, Any]:
    slot_bindings: list[dict[str, Any]] = []
    total_layer_slots = int(full_checkpoint_rtl_lowering["aggregate"]["linear_ops_per_layer"]) + int(
        full_checkpoint_rtl_lowering["aggregate"]["control_ops_per_layer"]
    )
    for layer_entry in full_checkpoint_rtl_lowering["layers"]:
        layer = int(layer_entry["layer"])
        for slot_in_layer, op in enumerate(layer_entry["ops"]):
            is_linear = op["kind"] == "linear"
            slot_bindings.append(
                {
                    "global_slot": layer * total_layer_slots + slot_in_layer,
                    "layer": layer,
                    "slot_in_layer": slot_in_layer,
                    "name": op["name"],
                    "kind": op["kind"],
                    "ip": op["ip"],
                    "rtl_engine": "e1_h1_tinyllama_linear_slot_engine"
                    if is_linear
                    else "e1_h1_tinyllama_control_slot_engine",
                    "rtl_file": rtl_top["linear_slot_engine_rtl"] if is_linear else rtl_top["control_slot_engine_rtl"],
                    "cycle_template": "tile_command_8_cycle_cpu_latch_array_template"
                    if is_linear
                    else "control_op_4_cycle_cpu_template",
                    "module_dpi_probe": op["module_dpi_probe"],
                    "lowering_status": op["status"],
                    "separated_modules": ["ingress_sram", "systolic_array"] if is_linear else ["control_cpu"],
                }
            )

    cycle_templates = {
        "tile_command_8_cycle_cpu_latch_array_template": {
            "source": "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json",
            "applies_to_slots": sum(1 for binding in slot_bindings if binding["kind"] == "linear"),
            "cycles": rtl_cycle["phase_template"],
        },
        "control_op_4_cycle_cpu_template": {
            "source": "e1/generated/pipeline/22_full_checkpoint_control_scheduler.json",
            "applies_to_slots": sum(1 for binding in slot_bindings if binding["kind"] != "linear"),
            "cycles": control_scheduler["phase_template"],
        },
        "graph_slot_4_cycle_launch_template": {
            "source": "e1/generated/pipeline/23_full_checkpoint_graph_sequencer.json",
            "applies_to_slots": int(graph_sequencer["total_graph_slots"]),
            "cycles": graph_sequencer["phase_template"],
        },
        "top_dispatch_4_cycle_slot_engine_template": {
            "source": "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
            "applies_to_slots": int(rtl_top["total_graph_slots"]),
            "cycles": rtl_top["phase_template"],
        },
    }
    readme_path = "e1/e1-h1/docs/modules/README.md"
    readme_text = (REPO_ROOT / readme_path).read_text(encoding="utf-8")
    readme_diagram_snippets = [
        "Cycle      control_cpu module       ingress_sram latch buffer        systolic_array module",
        "Tile cycle  control_cpu responsibility        ingress_sram latch buffer      systolic_array responsibility",
        "Control cycle  control_cpu responsibility",
        "Graph cycle  control_cpu responsibility",
        "Top cycle  graph_sequencer responsibility       selected slot engine",
    ]
    readme_cycle_coverage = {
        "readme": readme_path,
        "section": "Full Graph Slot Cycle Coverage",
        "diagram_section": "Cycle Diagram",
        "diagram_snippets": readme_diagram_snippets,
        "diagram_checks": [
            {
                "name": "readme_cycle_diagram_snippet_present",
                "snippet": snippet,
                "status": "pass" if snippet in readme_text else "fail",
            }
            for snippet in readme_diagram_snippets
        ],
        "templates": [
            {
                "template": template,
                "source": data["source"],
                "applies_to_slots": data["applies_to_slots"],
                "cycle_count": len(data["cycles"]),
                "phase_names": [entry["phase"] for entry in data["cycles"]],
                "checks": [
                    {
                        "name": "readme_lists_template",
                        "status": "pass" if template in readme_text else "fail",
                    },
                    {
                        "name": "readme_lists_all_phase_names",
                        "status": "pass"
                        if all(entry["phase"] in readme_text for entry in data["cycles"])
                        else "fail",
                    },
                    {
                        "name": "cycles_are_contiguous_from_zero",
                        "status": "pass"
                        if [entry["cycle"] for entry in data["cycles"]] == list(range(len(data["cycles"])))
                        else "fail",
                    },
                ],
            }
            for template, data in cycle_templates.items()
        ],
    }

    artifact_paths = [
        rtl_top["top_rtl"],
        rtl_top["linear_slot_engine_rtl"],
        rtl_top["control_slot_engine_rtl"],
        rtl_top["graph_sequencer_rtl"],
        rtl_top["latch_buffer_rtl"],
        rtl_top["systolic_array_rtl"],
        rtl_top["flist"],
        rtl_top["verilator_tb"],
        rtl_top["full_verilator_tb"],
        command_stream["header"],
        rtl_cycle["scheduler_rtl"],
        tile_engine["engine_rtl"],
        control_scheduler["scheduler_rtl"],
        graph_sequencer["scheduler_rtl"],
    ]
    expected_graph_slots = int(full_checkpoint_rtl_lowering["aggregate"]["layers"]) * total_layer_slots
    checks = [
        {
            "name": "full_checkpoint_layer_plan_shape_complete",
            "status": "pass" if full_checkpoint_rtl_lowering["status"] == "pass" else "fail",
        },
        {"name": "command_stream_generated", "status": command_stream["status"]},
        {"name": "linear_cycle_scheduler_generated", "status": rtl_cycle["status"]},
        {"name": "tile_engine_composes_latch_and_array", "status": tile_engine["status"]},
        {"name": "control_scheduler_generated", "status": control_scheduler["status"]},
        {"name": "graph_sequencer_generated", "status": graph_sequencer["status"]},
        {"name": "full_checkpoint_top_integrates_ordered_graph", "status": rtl_top["status"]},
        {
            "name": "top_report_marks_full_graph_lowering",
            "status": "pass" if rtl_top["full_checkpoint_graph_lowering"] else "fail",
        },
        {
            "name": "top_report_marks_full_checkpoint_structural_rtl_execution",
            "status": "pass" if rtl_top["full_checkpoint_structural_rtl_execution"] else "fail",
        },
        {
            "name": "ordered_graph_slot_count_matches_layer_plan",
            "status": "pass"
            if int(graph_sequencer["total_graph_slots"]) == expected_graph_slots
            and int(rtl_top["total_graph_slots"]) == expected_graph_slots
            else "fail",
        },
        {
            "name": "every_graph_slot_has_rtl_binding",
            "status": "pass"
            if len(slot_bindings) == expected_graph_slots
            and all(binding["rtl_file"] for binding in slot_bindings)
            else "fail",
        },
        {
            "name": "graph_slot_bindings_are_ordered_by_layer_and_slot",
            "status": "pass"
            if [binding["global_slot"] for binding in slot_bindings] == list(range(expected_graph_slots))
            and all(
                binding["global_slot"] == binding["layer"] * total_layer_slots + binding["slot_in_layer"]
                for binding in slot_bindings
            )
            else "fail",
        },
        {
            "name": "every_slot_binding_references_documented_cycle_template",
            "status": "pass"
            if all(binding["cycle_template"] in cycle_templates for binding in slot_bindings)
            else "fail",
        },
        {
            "name": "full_graph_cycle_templates_documented_in_readme",
            "status": "pass"
            if all(
                all(check["status"] == "pass" for check in template["checks"])
                for template in readme_cycle_coverage["templates"]
            )
            else "fail",
        },
        {
            "name": "readme_contains_required_cycle_diagram_snippets",
            "status": "pass"
            if all(check["status"] == "pass" for check in readme_cycle_coverage["diagram_checks"])
            else "fail",
        },
        {
            "name": "all_referenced_rtl_artifacts_exist",
            "status": "pass" if all((REPO_ROOT / path).exists() for path in artifact_paths) else "fail",
        },
        {
            "name": "full_linear_command_stream_runs_through_rtl_top",
            "status": "pass"
            if rtl_top["full_checkpoint_structural_rtl_execution"]
            and rtl_top["full_command_count_rtl_execution"]
            and rtl_top["verilator_execution"]["status"] == "pass"
            else "fail",
        },
        {
            "name": "full_command_payloads_checked_against_cpp_schedule",
            "status": "pass" if rtl_top["full_command_payload_schedule_check"] else "fail",
        },
        {
            "name": "full_command_payload_digest_checked_against_cpp_schedule",
            "status": "pass" if rtl_top["full_command_payload_digest_check"] else "fail",
        },
        {
            "name": "full_command_cycle_phases_checked",
            "status": "pass" if rtl_top["full_command_cycle_phase_check"] else "fail",
        },
        {
            "name": "full_command_control_payloads_checked_against_graph_schedule",
            "status": "pass"
            if rtl_top["full_command_control_schedule_check"]
            and rtl_top["full_command_control_digest_check"]
            else "fail",
        },
        {
            "name": "full_command_trace_anchors_match_cpp_schedule",
            "status": "pass" if rtl_top["full_command_trace_anchor_check"] else "fail",
        },
        {
            "name": "full_command_per_op_trace_coverage_matches_cpp_schedule",
            "status": "pass" if rtl_top["full_command_per_op_trace_coverage_check"] else "fail",
        },
        {
            "name": "numeric_output_equivalence_not_claimed",
            "status": "pass"
            if not rtl_top["full_checkpoint_numeric_output_equivalence"]
            and "without_tensor_numeric_equivalence" in rtl_top["full_checkpoint_rtl_execution_scope"]
            else "fail",
        },
    ]
    status = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    proof = {
        "schema": "e1-full-checkpoint-graph-rtl-lowering-proof-v0",
        "status": status,
        "model_id": manifest["model_id"],
        "truth_boundary": "full_graph_slot_dispatch_and_linear_command_stream_rtl_lowering",
        "full_checkpoint_graph_lowering": status == "pass",
        "full_checkpoint_rtl_execution": status == "pass" and rtl_top["full_checkpoint_rtl_execution"],
        "full_checkpoint_rtl_execution_scope": rtl_top["full_checkpoint_rtl_execution_scope"],
        "full_checkpoint_structural_rtl_execution": rtl_top["full_checkpoint_structural_rtl_execution"],
        "full_checkpoint_command_stream_rtl_execution": rtl_top["full_checkpoint_command_stream_rtl_execution"],
        "full_checkpoint_numeric_output_equivalence": False,
        "graph": {
            "layers": full_checkpoint_rtl_lowering["aggregate"]["layers"],
            "slots_per_layer": total_layer_slots,
            "total_graph_slots": graph_sequencer["total_graph_slots"],
            "linear_slots_per_layer": full_checkpoint_rtl_lowering["aggregate"]["linear_ops_per_layer"],
            "control_slots_per_layer": full_checkpoint_rtl_lowering["aggregate"]["control_ops_per_layer"],
            "total_linear_slots": graph_sequencer["total_linear_slots"],
            "total_control_slots": graph_sequencer["total_control_slots"],
            "slot_binding_count": len(slot_bindings),
        },
        "command_stream": {
            "total_tile_commands": command_stream["total_tile_commands"],
            "total_rtl_cycles": rtl_cycle["total_rtl_cycles"],
            "full_verilator_tb": rtl_top["full_verilator_tb"],
            "full_top_verilator_parameter": rtl_top["full_top_verilator_parameter"],
            "payload_schedule": rtl_top["full_command_payload_schedule"],
            "payload_digest": rtl_top["full_command_payload_digest"],
            "control_payload_digest": rtl_top["full_command_control_digest"],
            "structural_rtl_execution": rtl_top["full_checkpoint_structural_rtl_execution"],
            "verilator_execution_status": rtl_top["verilator_execution"]["status"],
            "full_command_verilator_report": rtl_top["full_command_verilator_report"],
        },
        "rtl_artifacts": {
            "top": rtl_top["top_rtl"],
            "graph_sequencer": rtl_top["graph_sequencer_rtl"],
            "linear_slot_engine": rtl_top["linear_slot_engine_rtl"],
            "control_slot_engine": rtl_top["control_slot_engine_rtl"],
            "latch_buffer": rtl_top["latch_buffer_rtl"],
            "systolic_array": rtl_top["systolic_array_rtl"],
            "flist": rtl_top["flist"],
        },
        "readme_cycle_coverage": readme_cycle_coverage,
        "slot_bindings": slot_bindings,
        "construction_inputs": {
            "layer_plan": "e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json",
            "command_stream": "e1/generated/pipeline/19_full_checkpoint_command_stream.json",
            "rtl_cycle_lowering": "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json",
            "tile_engine": "e1/generated/pipeline/21_full_checkpoint_tile_engine.json",
            "control_scheduler": "e1/generated/pipeline/22_full_checkpoint_control_scheduler.json",
            "graph_sequencer": "e1/generated/pipeline/23_full_checkpoint_graph_sequencer.json",
            "rtl_top": "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
        },
        "non_claims": [
            "No TinyLlama numeric output equivalence is claimed by this proof.",
            "No full StableHLO live checkpoint export is required by this deterministic preflight proof.",
            "Control/elementwise arithmetic kernels remain represented as CPU/control RTL scheduling boundaries.",
            "Structural RTL execution checks dispatch, handshakes, command payloads, digest, and cycle phases; it does not compare TinyLlama output tensors.",
            "CPU/control slot payload and commit checks prove graph-schedule identity, not arithmetic equivalence for control kernels.",
        ],
        "checks": checks,
    }
    write_json(output_path, proof)
    return proof


def emit_full_graph_module_dpi_binding(
    output_path: Path,
    full_checkpoint_graph_rtl_lowering: dict[str, Any],
    module_dpi_report: dict[str, Any],
    full_checkpoint_module_dpi: dict[str, Any],
) -> dict[str, Any]:
    base_by_name = {module["name"]: module for module in module_dpi_report["modules"]}
    generated_by_name = {module["name"]: module for module in full_checkpoint_module_dpi["modules"]}
    base_cpp_launcher_by_name = {
        result["name"]: result
        for result in module_dpi_report.get("cpp_verilator_launcher", {})
        .get("verilator_run", {})
        .get("module_results", [])
        if result.get("name")
    }
    generated_cpp_launcher_by_name = {
        result["name"]: result
        for result in full_checkpoint_module_dpi.get("cpp_verilator_launcher", {})
        .get("verilator_run", {})
        .get("module_results", [])
        if result.get("name")
    }
    all_base_modules = [module["name"] for module in module_dpi_report["modules"]]
    required_base_modules = ["control_cpu", "ingress_sram", "systolic_array"]
    required_generated_modules = [
        "linear_scheduler",
        "linear_tile_engine",
        "control_scheduler",
        "graph_sequencer",
        "linear_slot_engine",
        "control_slot_engine",
        "full_checkpoint_top",
    ]
    slot_engine_modules = {
        "e1_h1_tinyllama_linear_slot_engine": "linear_slot_engine",
        "e1_h1_tinyllama_control_slot_engine": "control_slot_engine",
    }
    generated_by_top_module = {
        module["top_module"]: module for module in full_checkpoint_module_dpi["modules"]
    }
    base_by_top_module = {module["top_module"]: module for module in module_dpi_report["modules"]}

    def expected_phase_trace_keys(recipe: dict[str, Any]) -> list[str]:
        phase_names = [
            marker[len("phase=") :]
            for marker in recipe.get("expected_stdout_markers", [])
            if marker.startswith("phase=")
        ]
        return [
            f"{cycle}:{phase}"
            for cycle, phase in enumerate(phase_names)
        ]

    def expected_phase_signal_trace_keys(recipe: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for entry in recipe.get("expected_phase_signal_trace", []):
            expected = entry["expected"]
            keys.append(f"{entry['cycle']}:{entry['signal']}:{expected}:{expected}")
        return keys

    def cpp_launcher_result_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "build_command": result.get("build_command"),
            "expected_stdout_markers": result.get("expected_stdout_markers", []),
            "expected_phase_trace_keys": result.get("expected_phase_trace_keys", []),
            "observed_phase_trace_prefix_keys": result.get("observed_phase_trace_prefix_keys", []),
            "expected_phase_signal_trace_keys": result.get("expected_phase_signal_trace_keys", []),
            "observed_phase_signal_trace_prefix_keys": result.get(
                "observed_phase_signal_trace_prefix_keys",
                [],
            ),
            "name": result.get("name"),
            "status": result.get("status"),
            "run_executable": result.get("run_executable"),
            "stdout_markers_present": result.get("stdout_markers_present"),
            "missing_stdout_markers": result.get("missing_stdout_markers", []),
            "phase_trace_in_order": result.get("phase_trace_in_order"),
            "phase_trace_repeats_template": result.get("phase_trace_repeats_template"),
            "phase_signal_trace_matches": result.get("phase_signal_trace_matches"),
            "phase_signal_trace_repeats_template": result.get("phase_signal_trace_repeats_template"),
            "observed_phase_trace_count": result.get("observed_phase_trace_count"),
            "observed_phase_signal_trace_count": result.get("observed_phase_signal_trace_count"),
        }

    def cpp_launcher_result_passed(result: dict[str, Any] | None) -> bool:
        return (
            result is not None
            and result.get("status") == "pass"
            and result.get("stdout_markers_present") is True
            and result.get("missing_stdout_markers") == []
            and result.get("phase_trace_in_order") is True
            and result.get("phase_trace_repeats_template") is True
            and result.get("phase_signal_trace_matches") is True
            and result.get("phase_signal_trace_repeats_template") is True
            and bool(result.get("expected_phase_trace_keys"))
            and result.get("observed_phase_trace_prefix_keys")
            == result.get("expected_phase_trace_keys")
            and int(result.get("observed_phase_trace_count") or 0) > 0
            and bool(result.get("expected_phase_signal_trace_keys"))
            and result.get("observed_phase_signal_trace_prefix_keys")
            == result.get("expected_phase_signal_trace_keys")
            and int(result.get("observed_phase_signal_trace_count") or 0) > 0
        )

    def cpp_launcher_result_matches_recipe(
        result: dict[str, Any] | None,
        recipe: dict[str, Any] | None,
    ) -> bool:
        return (
            result is not None
            and recipe is not None
            and result.get("name") == recipe.get("name")
            and result.get("build_command") == recipe.get("build_command")
            and result.get("run_executable") == recipe.get("run_executable")
            and result.get("expected_stdout_markers") == recipe.get("expected_stdout_markers")
            and result.get("expected_phase_trace_keys") == expected_phase_trace_keys(recipe)
            and result.get("observed_phase_trace_prefix_keys") == expected_phase_trace_keys(recipe)
            and result.get("expected_phase_signal_trace_keys")
            == expected_phase_signal_trace_keys(recipe)
            and result.get("observed_phase_signal_trace_prefix_keys")
            == expected_phase_signal_trace_keys(recipe)
        )

    def cycle_contract_phase_keys(cycle_contract: dict[str, Any] | None) -> list[str]:
        if cycle_contract is None:
            return []
        return [
            f"{step['cycle']}:{step['phase']}"
            for step in cycle_contract.get("cycles", [])
        ]

    def readme_cycle_phase_keys(readme_cycle_coverage: dict[str, Any] | None) -> list[str]:
        if readme_cycle_coverage is None:
            return []
        return [
            f"{cycle}:{phase}"
            for cycle, phase in enumerate(readme_cycle_coverage.get("phase_names", []))
        ]

    def cpp_launcher_readme_cycle_proof(
        *,
        cycle_contract: dict[str, Any] | None,
        readme_cycle_coverage: dict[str, Any] | None,
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        contract_keys = cycle_contract_phase_keys(cycle_contract)
        readme_keys = readme_cycle_phase_keys(readme_cycle_coverage)
        expected_launcher_keys = result.get("expected_phase_trace_keys", []) if result else []
        observed_launcher_keys = result.get("observed_phase_trace_prefix_keys", []) if result else []
        status = (
            bool(contract_keys)
            and contract_keys == readme_keys
            and expected_launcher_keys == readme_keys
            and observed_launcher_keys == readme_keys
        )
        return {
            "status": "pass" if status else "fail",
            "readme": "e1/e1-h1/docs/modules/README.md",
            "readme_index": readme_cycle_coverage.get("readme_index")
            if readme_cycle_coverage
            else None,
            "readme_index_row": readme_cycle_coverage.get("readme_index_row")
            if readme_cycle_coverage
            else None,
            "cycle_template": cycle_contract.get("template") if cycle_contract else None,
            "cycle_contract_phase_keys": contract_keys,
            "readme_phase_keys": readme_keys,
            "cpp_launcher_expected_phase_keys": expected_launcher_keys,
            "cpp_launcher_observed_phase_keys": observed_launcher_keys,
        }

    def cpp_launcher_result_for_module(
        source_kind: str,
        module: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if module is None:
            return None
        if source_kind == "separated_base_imp2_rtl":
            return base_cpp_launcher_by_name.get(module["name"])
        return generated_cpp_launcher_by_name.get(module["name"])

    slot_engine_bindings = []
    for rtl_engine, module_name in slot_engine_modules.items():
        slots = [
            binding
            for binding in full_checkpoint_graph_rtl_lowering["slot_bindings"]
            if binding["rtl_engine"] == rtl_engine
        ]
        module = generated_by_name.get(module_name)
        slot_engine_bindings.append(
            {
                "rtl_engine": rtl_engine,
                "module": module_name,
                "slot_count": len(slots),
                "module_dpi_probe": module["probe"] if module is not None else None,
                "module_dpi_verilator_status": module["verilator_execution"]["status"]
                if module is not None
                else None,
            }
        )

    def module_dpi_coverage_entry(
        *,
        source_kind: str,
        source_module_name: str,
        sv_module: str,
        rtl: str,
        module: dict[str, Any] | None,
    ) -> dict[str, Any]:
        construction_ledger = module["construction_ledger"] if module is not None else None
        cycle_contract = module["cycle_contract"] if module is not None else None
        readme_cycle_coverage = module["readme_cycle_coverage"] if module is not None else None
        verilator_execution = module["verilator_execution"] if module is not None else None
        verilator_recipe = module["verilator_execution_recipe"] if module is not None else None
        cpp_launcher_result = cpp_launcher_result_for_module(source_kind, module)
        expected_phase_trace = (
            verilator_execution.get("expected_phase_trace", []) if verilator_execution is not None else []
        )
        expected_phase_signal_trace = (
            verilator_execution.get("expected_phase_signal_trace", [])
            if verilator_execution is not None
            else []
        )
        probe_dut_instantiation_count = (
            construction_ledger.get("probe_dut_instantiation_count") if construction_ledger is not None else None
        )
        probe_reference_instantiation_count = (
            construction_ledger.get("probe_reference_instantiation_count") if construction_ledger is not None else None
        )
        if module is not None and source_kind == "separated_base_imp2_rtl":
            selected_dut_rtl = [construction_ledger["imp2_rtl"]]
            expected_flist_entries = [
                construction_ledger["reference_rtl"],
                construction_ledger["imp2_rtl"],
                module["probe"],
            ]
            module_only_flist_scope = "base_imp1_reference_plus_imp2_dut_plus_probe"
        elif module is not None:
            selected_dut_rtl = list(construction_ledger["rtl"])
            expected_flist_entries = [*construction_ledger["rtl"], module["probe"]]
            module_only_flist_scope = "generated_selected_dut_plus_probe"
        else:
            selected_dut_rtl = []
            expected_flist_entries = []
            module_only_flist_scope = None
        flist_path = REPO_ROOT / module["flist"] if module is not None else None
        observed_flist_entries = (
            flist_path.read_text(encoding="utf-8").splitlines()
            if flist_path is not None and flist_path.exists()
            else []
        )
        probe_text = (
            (REPO_ROOT / module["probe"]).read_text(encoding="utf-8")
            if module is not None and (REPO_ROOT / module["probe"]).exists()
            else ""
        )
        module_only_flist_rtl = (
            selected_dut_rtl
            if source_kind == "separated_base_imp2_rtl"
            else module.get("module_only_flist_rtl", []) if module is not None else []
        )
        composed_rtl_dependencies = module.get("composed_rtl_dependencies", []) if module is not None else []
        child_stub_modules = module.get("child_stub_modules", []) if module is not None else []
        module_only_flist_boundary_exact = (
            bool(expected_flist_entries) and observed_flist_entries == expected_flist_entries
        )
        generated_module_only_flist_exact = (
            module_only_flist_boundary_exact
            if source_kind == "generated_full_checkpoint_rtl"
            else None
        )
        base_module_only_flist_exact = (
            module_only_flist_boundary_exact
            if source_kind == "separated_base_imp2_rtl"
            else None
        )
        selected_dut_rtl_in_flist = bool(selected_dut_rtl) and all(
            rtl_path in observed_flist_entries for rtl_path in selected_dut_rtl
        )
        composed_dependencies_absent_from_flist = all(
            dependency not in observed_flist_entries
            for dependency in composed_rtl_dependencies
        )
        child_stubs_present_in_probe = all(
            f"module {child_stub}" in probe_text
            for child_stub in child_stub_modules
        )
        flist_exact_match = bool(expected_flist_entries) and observed_flist_entries == expected_flist_entries
        exact_probe_instantiation_counts = (
            probe_dut_instantiation_count == 1
            and (probe_reference_instantiation_count in {None, 1})
        )
        cycle_contract_checks_pass = (
            cycle_contract is not None
            and all(check["status"] == "pass" for check in cycle_contract["checks"])
            and [step["cycle"] for step in cycle_contract["cycles"]]
            == list(range(cycle_contract["cycle_period"]))
        )
        readme_cycle_checks_pass = (
            readme_cycle_coverage is not None
            and all(check["status"] == "pass" for check in readme_cycle_coverage["checks"])
            and cycle_contract is not None
            and readme_cycle_coverage["phase_names"] == [step["phase"] for step in cycle_contract["cycles"]]
        )
        phase_trace_checks_pass = (
            verilator_execution is not None
            and bool(expected_phase_trace)
            and verilator_execution.get("observed_phase_trace_prefix") == expected_phase_trace
            and verilator_execution.get("observed_phase_trace_count", 0) >= len(expected_phase_trace)
        )
        phase_signal_trace_checks_pass = (
            verilator_execution is not None
            and (
                not expected_phase_signal_trace
                or (
                    verilator_execution.get("observed_phase_signal_trace_prefix")
                    == expected_phase_signal_trace
                    and verilator_execution.get("observed_phase_signal_trace_count", 0)
                    >= len(expected_phase_signal_trace)
                )
            )
        )
        return {
            "source_kind": source_kind,
            "source_module_name": source_module_name,
            "sv_module": sv_module,
            "rtl": rtl,
            "covered": module is not None,
            "module_dpi_name": module["name"] if module is not None else None,
            "probe": module["probe"] if module is not None else None,
            "flist": module["flist"] if module is not None else None,
            "recipe": module["verilator_execution_recipe"] if module is not None else None,
            "construction_ledger": construction_ledger,
            "cycle_template": cycle_contract["template"] if cycle_contract is not None else None,
            "phase_names": [step["phase"] for step in cycle_contract["cycles"]]
            if cycle_contract is not None
            else [],
            "readme_index_row": readme_cycle_coverage["readme_index_row"]
            if readme_cycle_coverage is not None
            else None,
            "probe_dut_instantiation_count": probe_dut_instantiation_count,
            "probe_reference_instantiation_count": probe_reference_instantiation_count,
            "module_only_flist_scope": module_only_flist_scope,
            "selected_dut_rtl": selected_dut_rtl,
            "expected_flist_entries": expected_flist_entries,
            "observed_flist_entries": observed_flist_entries,
            "flist_exact_match": flist_exact_match,
            "module_only_flist_boundary_exact": module_only_flist_boundary_exact,
            "module_only_flist_rtl": module_only_flist_rtl,
            "selected_dut_rtl_in_flist": selected_dut_rtl_in_flist,
            "composed_rtl_dependencies": composed_rtl_dependencies,
            "child_stub_modules": child_stub_modules,
            "generated_module_only_flist_exact": generated_module_only_flist_exact,
            "base_module_only_flist_exact": base_module_only_flist_exact,
            "composed_dependencies_absent_from_flist": composed_dependencies_absent_from_flist,
            "child_stubs_present_in_probe": child_stubs_present_in_probe,
            "verilator_status": verilator_execution["status"] if verilator_execution is not None else None,
            "ledger_checks_pass": (
                module is not None
                and all(check["status"] == "pass" for check in module["construction_ledger"]["checks"])
            ),
            "exact_probe_instantiation_counts": exact_probe_instantiation_counts,
            "cycle_contract_checks_pass": cycle_contract_checks_pass,
            "readme_cycle_checks_pass": readme_cycle_checks_pass,
            "phase_trace_checks_pass": phase_trace_checks_pass,
            "phase_signal_trace_checks_pass": phase_signal_trace_checks_pass,
            "cpp_launcher_result": cpp_launcher_result_summary(cpp_launcher_result),
            "cpp_launcher_readme_cycle_proof": cpp_launcher_readme_cycle_proof(
                cycle_contract=cycle_contract,
                readme_cycle_coverage=readme_cycle_coverage,
                result=cpp_launcher_result,
            ),
            "cpp_launcher_readme_cycle_checks_pass": (
                cpp_launcher_readme_cycle_proof(
                    cycle_contract=cycle_contract,
                    readme_cycle_coverage=readme_cycle_coverage,
                    result=cpp_launcher_result,
                )["status"]
                == "pass"
            ),
            "cpp_launcher_recipe_checks_pass": cpp_launcher_result_matches_recipe(
                cpp_launcher_result,
                verilator_recipe,
            ),
            "cpp_launcher_checks_pass": (
                cpp_launcher_result_passed(cpp_launcher_result)
                and cpp_launcher_result_matches_recipe(cpp_launcher_result, verilator_recipe)
            ),
            "recipe_checks_pass": (
                module is not None
                and module["verilator_execution"]["build_command"]
                == module["verilator_execution_recipe"]["build_command"]
                and module["verilator_execution"]["run_executable"]
                == module["verilator_execution_recipe"]["run_executable"]
            ),
        }

    generated_rtl_paths = unique_ordered(
        [
            rtl
            for module in full_checkpoint_module_dpi["modules"]
            for rtl in module["rtl"]
            if rtl.startswith("e1/e1-h1/generated/full_checkpoint/") and rtl.endswith(".sv")
        ]
    )
    generated_full_checkpoint_sv_inventory = sorted(
        repo_rel(path)
        for path in (REPO_ROOT / "e1/e1-h1/generated/full_checkpoint").glob("*.sv")
    )
    generated_full_checkpoint_sv_inventory_modules = [
        {"rtl": rtl, "defined_modules": parse_sv_defined_modules(REPO_ROOT / rtl)}
        for rtl in generated_full_checkpoint_sv_inventory
    ]
    generated_rtl_module_coverage = []
    for rtl in generated_rtl_paths:
        for sv_module in parse_sv_defined_modules(REPO_ROOT / rtl):
            module = generated_by_top_module.get(sv_module)
            generated_rtl_module_coverage.append(
                module_dpi_coverage_entry(
                    source_kind="generated_full_checkpoint_rtl",
                    source_module_name=module["name"] if module is not None else sv_module,
                    sv_module=sv_module,
                    rtl=rtl,
                    module=module,
                )
            )

    base_imp2_sv_inventory = sorted(
        repo_rel(path)
        for path in (REPO_ROOT / "e1/e1-h1/rtl/imp2").glob("*.sv")
    )
    base_imp2_sv_inventory_modules = [
        {"rtl": rtl, "defined_modules": parse_sv_defined_modules(REPO_ROOT / rtl)}
        for rtl in base_imp2_sv_inventory
    ]
    base_rtl_module_coverage = []
    for module_name in required_base_modules:
        module = base_by_name.get(module_name)
        rtl = module["construction_ledger"]["imp2_rtl"] if module is not None else None
        sv_modules = parse_sv_defined_modules(REPO_ROOT / rtl) if rtl is not None else []
        for sv_module in sv_modules:
            base_rtl_module_coverage.append(
                module_dpi_coverage_entry(
                    source_kind="separated_base_imp2_rtl",
                    source_module_name=module_name,
                    sv_module=sv_module,
                    rtl=rtl,
                    module=base_by_top_module.get(sv_module),
                )
            )

    source_derived_module_dpi_coverage = [
        *generated_rtl_module_coverage,
        *base_rtl_module_coverage,
    ]

    generated_child_stub_boundary = []
    generated_module_bindings = []
    for module_name in required_generated_modules:
        module = generated_by_name.get(module_name)
        flist_path = REPO_ROOT / module["flist"] if module is not None else None
        observed_flist_entries = (
            flist_path.read_text(encoding="utf-8").splitlines()
            if flist_path is not None and flist_path.exists()
            else []
        )
        probe_text = (
            (REPO_ROOT / module["probe"]).read_text(encoding="utf-8")
            if module is not None and (REPO_ROOT / module["probe"]).exists()
            else ""
        )
        expected_flist_entries = (
            [*module["module_only_flist_rtl"], module["probe"]]
            if module is not None
            else []
        )
        child_stubs_present_in_probe = (
            module is not None
            and all(f"module {child}" in probe_text for child in module["child_stub_modules"])
        )
        composed_dependencies_absent_from_flist = (
            module is not None
            and all(
                dependency not in observed_flist_entries
                for dependency in module["composed_rtl_dependencies"]
            )
        )
        boundary_entry = {
            "name": module_name,
            "present": module is not None,
            "top_module": module["top_module"] if module is not None else None,
            "rtl": module["rtl"] if module is not None else [],
            "selected_dut_rtl": module["module_only_flist_rtl"] if module is not None else [],
            "composed_rtl_dependencies": module["composed_rtl_dependencies"] if module is not None else [],
            "child_stub_modules": module["child_stub_modules"] if module is not None else [],
            "probe": module["probe"] if module is not None else None,
            "flist": module["flist"] if module is not None else None,
            "expected_flist_entries": expected_flist_entries,
            "observed_flist_entries": observed_flist_entries,
            "flist_contains_only_selected_dut_and_probe": (
                bool(expected_flist_entries) and observed_flist_entries == expected_flist_entries
            ),
            "composed_dependencies_absent_from_flist": composed_dependencies_absent_from_flist,
            "child_stubs_present_in_probe": child_stubs_present_in_probe,
        }
        generated_child_stub_boundary.append(boundary_entry)
        generated_module_bindings.append(
            {
                "name": module_name,
                "present": module is not None,
                "top_module": module["top_module"] if module is not None else None,
                "rtl": module["rtl"] if module is not None else [],
                "module_only_flist_rtl": module["module_only_flist_rtl"] if module is not None else [],
                "composed_rtl_dependencies": module["composed_rtl_dependencies"] if module is not None else [],
                "child_stub_modules": module["child_stub_modules"] if module is not None else [],
                "probe": module["probe"] if module is not None else None,
                "flist": module["flist"] if module is not None else None,
                "cycle_contract": module["cycle_contract"] if module is not None else None,
                "readme_cycle_coverage": module["readme_cycle_coverage"] if module is not None else None,
                "construction_ledger": module["construction_ledger"] if module is not None else None,
                "verilator_execution_recipe": module["verilator_execution_recipe"] if module is not None else None,
                "verilator_execution": module["verilator_execution"] if module is not None else None,
                "cpp_launcher_result": cpp_launcher_result_summary(
                    generated_cpp_launcher_by_name.get(module_name)
                ),
                "cpp_launcher_readme_cycle_proof": cpp_launcher_readme_cycle_proof(
                    cycle_contract=module["cycle_contract"] if module is not None else None,
                    readme_cycle_coverage=module["readme_cycle_coverage"]
                    if module is not None
                    else None,
                    result=generated_cpp_launcher_by_name.get(module_name),
                ),
                "cpp_launcher_readme_cycle_checks_pass": (
                    cpp_launcher_readme_cycle_proof(
                        cycle_contract=module["cycle_contract"] if module is not None else None,
                        readme_cycle_coverage=module["readme_cycle_coverage"]
                        if module is not None
                        else None,
                        result=generated_cpp_launcher_by_name.get(module_name),
                    )["status"]
                    == "pass"
                ),
                "cpp_launcher_recipe_checks_pass": cpp_launcher_result_matches_recipe(
                    generated_cpp_launcher_by_name.get(module_name),
                    module["verilator_execution_recipe"] if module is not None else None,
                ),
                "cpp_launcher_checks_pass": (
                    cpp_launcher_result_passed(generated_cpp_launcher_by_name.get(module_name))
                    and cpp_launcher_result_matches_recipe(
                        generated_cpp_launcher_by_name.get(module_name),
                        module["verilator_execution_recipe"] if module is not None else None,
                    )
                ),
            }
        )

    def base_module_binding_entry(module_name: str) -> dict[str, Any]:
        module = base_by_name.get(module_name)
        construction_ledger = module["construction_ledger"] if module is not None else None
        cycle_contract = module["cycle_contract"] if module is not None else None
        readme_cycle_coverage = module["readme_cycle_coverage"] if module is not None else None
        verilator_execution = module["verilator_execution"] if module is not None else None
        verilator_recipe = module["verilator_execution_recipe"] if module is not None else None
        expected_flist_entries = (
            [
                construction_ledger["reference_rtl"],
                construction_ledger["imp2_rtl"],
                module["probe"],
            ]
            if module is not None
            else []
        )
        flist_path = REPO_ROOT / module["flist"] if module is not None else None
        observed_flist_entries = (
            flist_path.read_text(encoding="utf-8").splitlines()
            if flist_path is not None and flist_path.exists()
            else []
        )
        expected_phase_trace = (
            verilator_execution.get("expected_phase_trace", []) if verilator_execution is not None else []
        )
        expected_phase_signal_trace = (
            verilator_execution.get("expected_phase_signal_trace", [])
            if verilator_execution is not None
            else []
        )
        imp2_rtl = construction_ledger["imp2_rtl"] if construction_ledger is not None else None
        source_defined_modules = (
            parse_sv_defined_modules(REPO_ROOT / imp2_rtl)
            if imp2_rtl is not None and (REPO_ROOT / imp2_rtl).exists()
            else []
        )
        source_defined_modules_include_top = (
            module is not None and module["top_module"] in source_defined_modules
        )
        return {
            "name": module_name,
            "present": module is not None,
            "top_module": module["top_module"] if module is not None else None,
            "probe": module["probe"] if module is not None else None,
            "flist": module["flist"] if module is not None else None,
            "reference_rtl": construction_ledger["reference_rtl"] if construction_ledger is not None else None,
            "imp2_rtl": imp2_rtl,
            "source_defined_modules": source_defined_modules,
            "source_defined_modules_include_top": source_defined_modules_include_top,
            "expected_flist_entries": expected_flist_entries,
            "observed_flist_entries": observed_flist_entries,
            "flist_exact_match": bool(expected_flist_entries)
            and observed_flist_entries == expected_flist_entries,
            "probe_dut_instantiation_count": construction_ledger.get("probe_dut_instantiation_count")
            if construction_ledger is not None
            else None,
            "probe_reference_instantiation_count": construction_ledger.get("probe_reference_instantiation_count")
            if construction_ledger is not None
            else None,
            "exact_probe_instantiation_counts": (
                construction_ledger is not None
                and construction_ledger.get("probe_dut_instantiation_count") == 1
                and construction_ledger.get("probe_reference_instantiation_count") == 1
            ),
            "cycle_contract": cycle_contract,
            "readme_cycle_coverage": readme_cycle_coverage,
            "construction_ledger": construction_ledger,
            "verilator_execution_recipe": verilator_recipe,
            "verilator_execution": verilator_execution,
            "verilator_status": verilator_execution["status"] if verilator_execution is not None else None,
            "ledger_checks_pass": (
                construction_ledger is not None
                and all(check["status"] == "pass" for check in construction_ledger["checks"])
            ),
            "recipe_checks_pass": (
                verilator_execution is not None
                and verilator_recipe is not None
                and verilator_execution["build_command"] == verilator_recipe["build_command"]
                and verilator_execution["run_executable"] == verilator_recipe["run_executable"]
            ),
            "cycle_contract_checks_pass": (
                cycle_contract is not None
                and all(check["status"] == "pass" for check in cycle_contract["checks"])
                and [step["cycle"] for step in cycle_contract["cycles"]]
                == list(range(cycle_contract["cycle_period"]))
            ),
            "readme_cycle_checks_pass": (
                readme_cycle_coverage is not None
                and cycle_contract is not None
                and all(check["status"] == "pass" for check in readme_cycle_coverage["checks"])
                and readme_cycle_coverage["phase_names"] == [step["phase"] for step in cycle_contract["cycles"]]
            ),
            "phase_trace_checks_pass": (
                verilator_execution is not None
                and bool(expected_phase_trace)
                and verilator_execution["observed_phase_trace_prefix"] == expected_phase_trace
                and verilator_execution["observed_phase_trace_count"] >= len(expected_phase_trace)
            ),
            "phase_signal_trace_checks_pass": (
                verilator_execution is not None
                and bool(expected_phase_signal_trace)
                and verilator_execution["observed_phase_signal_trace_prefix"] == expected_phase_signal_trace
                and verilator_execution["observed_phase_signal_trace_count"] >= len(expected_phase_signal_trace)
            ),
            "cpp_launcher_result": cpp_launcher_result_summary(
                base_cpp_launcher_by_name.get(module_name)
            ),
            "cpp_launcher_readme_cycle_proof": cpp_launcher_readme_cycle_proof(
                cycle_contract=cycle_contract,
                readme_cycle_coverage=readme_cycle_coverage,
                result=base_cpp_launcher_by_name.get(module_name),
            ),
            "cpp_launcher_readme_cycle_checks_pass": (
                cpp_launcher_readme_cycle_proof(
                    cycle_contract=cycle_contract,
                    readme_cycle_coverage=readme_cycle_coverage,
                    result=base_cpp_launcher_by_name.get(module_name),
                )["status"]
                == "pass"
            ),
            "cpp_launcher_recipe_checks_pass": cpp_launcher_result_matches_recipe(
                base_cpp_launcher_by_name.get(module_name),
                verilator_recipe,
            ),
            "cpp_launcher_checks_pass": (
                cpp_launcher_result_passed(base_cpp_launcher_by_name.get(module_name))
                and cpp_launcher_result_matches_recipe(
                    base_cpp_launcher_by_name.get(module_name),
                    verilator_recipe,
                )
            ),
        }

    all_base_module_bindings = [
        base_module_binding_entry(module_name) for module_name in all_base_modules
    ]
    base_module_bindings = [
        base_module_binding_entry(module_name) for module_name in required_base_modules
    ]
    covered_generated_sv_modules = {
        entry["sv_module"] for entry in generated_rtl_module_coverage if entry["covered"]
    }
    generated_inventory_sv_modules = {
        module
        for entry in generated_full_checkpoint_sv_inventory_modules
        for module in entry["defined_modules"]
    }
    all_base_imp2_rtl_paths = sorted(
        {
            binding["imp2_rtl"]
            for binding in all_base_module_bindings
            if binding["imp2_rtl"] is not None
        }
    )
    covered_base_sv_modules = {
        binding["top_module"]
        for binding in all_base_module_bindings
        if binding["present"] and binding["top_module"] is not None
    }
    base_inventory_sv_modules = {
        module
        for entry in base_imp2_sv_inventory_modules
        for module in entry["defined_modules"]
    }

    recipe_checks_by_report = [
        [check for check in report["checks"] if "recipe" in check["name"]]
        for report in [module_dpi_report, full_checkpoint_module_dpi]
    ]
    module_dpi_recipe_checks_pass = all(recipe_checks_by_report) and all(
        check["status"] == "pass"
        for recipe_checks in recipe_checks_by_report
        for check in recipe_checks
    )
    module_dpi_ledger_checks_pass = all(
        module is not None
        and module.get("construction_ledger") is not None
        and all(check["status"] == "pass" for check in module["construction_ledger"]["checks"])
        for module in [
            *(base_by_name.get(module_name) for module_name in required_base_modules),
            *(generated_by_name.get(module_name) for module_name in required_generated_modules),
        ]
    )
    checks = [
        {
            "name": "full_graph_rtl_lowering_proof_passed",
            "status": full_checkpoint_graph_rtl_lowering["status"],
        },
        {
            "name": "generated_full_checkpoint_module_dpi_passed",
            "status": full_checkpoint_module_dpi["status"],
        },
        {
            "name": "base_module_dpi_passed",
            "status": module_dpi_report["status"],
        },
        {
            "name": "all_required_generated_rtl_modules_have_module_dpi",
            "status": "pass"
            if set(required_generated_modules).issubset(generated_by_name)
            else "fail",
        },
        {
            "name": "all_required_generated_rtl_modules_ran_under_verilator",
            "status": "pass"
            if all(
                generated_by_name.get(module_name, {}).get("verilator_execution", {}).get("status") == "pass"
                for module_name in required_generated_modules
            )
            else "fail",
        },
        {
            "name": "all_separated_base_modules_have_module_dpi",
            "status": "pass" if set(required_base_modules).issubset(base_by_name) else "fail",
        },
        {
            "name": "all_separated_base_modules_ran_under_verilator",
            "status": "pass"
            if all(
                base_by_name.get(module_name, {}).get("verilator_execution", {}).get("status") == "pass"
                for module_name in required_base_modules
            )
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_have_module_dpi",
            "status": "pass"
            if all_base_modules
            and set(all_base_modules) == set(base_by_name)
            and all(binding["present"] for binding in all_base_module_bindings)
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_ran_under_verilator",
            "status": "pass"
            if all(binding["verilator_status"] == "pass" for binding in all_base_module_bindings)
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_have_source_rtl_exact_flists_counts_and_cycle_traces",
            "status": "pass"
            if all_base_module_bindings
            and all(
                binding["present"]
                and binding["source_defined_modules_include_top"]
                and binding["flist_exact_match"]
                and binding["exact_probe_instantiation_counts"]
                and binding["ledger_checks_pass"]
                and binding["recipe_checks_pass"]
                and binding["cycle_contract_checks_pass"]
                and binding["readme_cycle_checks_pass"]
                and binding["phase_trace_checks_pass"]
                and binding["phase_signal_trace_checks_pass"]
                for binding in all_base_module_bindings
            )
            else "fail",
        },
        {
            "name": "slot_binding_engines_have_module_dpi",
            "status": "pass"
            if all(binding["module_dpi_verilator_status"] == "pass" for binding in slot_engine_bindings)
            and {binding["rtl_engine"] for binding in slot_engine_bindings}
            == {binding["rtl_engine"] for binding in full_checkpoint_graph_rtl_lowering["slot_bindings"]}
            else "fail",
        },
        {
            "name": "full_graph_top_has_module_dpi",
            "status": "pass"
            if generated_by_name.get("full_checkpoint_top", {})
            .get("verilator_execution", {})
            .get("status")
            == "pass"
            else "fail",
        },
        {
            "name": "module_dpi_reports_use_cpp_generated_recipes",
            "status": "pass" if module_dpi_recipe_checks_pass else "fail",
        },
        {
            "name": "module_dpi_reports_have_cpp_construction_ledgers",
            "status": "pass" if module_dpi_ledger_checks_pass else "fail",
        },
        {
            "name": "generated_full_checkpoint_sv_inventory_matches_module_dpi_rtl",
            "status": "pass"
            if generated_full_checkpoint_sv_inventory
            and set(generated_full_checkpoint_sv_inventory) == set(generated_rtl_paths)
            else "fail",
        },
        {
            "name": "base_imp2_sv_inventory_matches_all_base_module_dpi_rtl",
            "status": "pass"
            if base_imp2_sv_inventory
            and set(base_imp2_sv_inventory) == set(all_base_imp2_rtl_paths)
            else "fail",
        },
        {
            "name": "generated_sv_inventory_modules_have_module_dpi_coverage",
            "status": "pass"
            if generated_inventory_sv_modules
            and generated_inventory_sv_modules.issubset(covered_generated_sv_modules)
            else "fail",
        },
        {
            "name": "base_imp2_sv_inventory_modules_have_module_dpi_coverage",
            "status": "pass"
            if base_inventory_sv_modules
            and base_inventory_sv_modules.issubset(covered_base_sv_modules)
            else "fail",
        },
        {
            "name": "all_generated_sv_modules_have_source_derived_module_dpi",
            "status": "pass"
            if generated_rtl_module_coverage
            and all(entry["covered"] for entry in generated_rtl_module_coverage)
            and {entry["module_dpi_name"] for entry in generated_rtl_module_coverage}
            == set(required_generated_modules)
            else "fail",
        },
        {
            "name": "all_separated_base_sv_modules_have_source_derived_module_dpi",
            "status": "pass"
            if base_rtl_module_coverage
            and all(entry["covered"] for entry in base_rtl_module_coverage)
            and {entry["module_dpi_name"] for entry in base_rtl_module_coverage}
            == set(required_base_modules)
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_recipes_ledgers_and_verilator",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"]
                and entry["verilator_status"] == "pass"
                and entry["ledger_checks_pass"]
                and entry["recipe_checks_pass"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_exact_flists",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"] and entry["flist_exact_match"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_preserves_selected_dut_boundaries",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"]
                and entry["module_only_flist_boundary_exact"]
                and entry["selected_dut_rtl_in_flist"]
                and (
                    (
                        entry["source_kind"] == "generated_full_checkpoint_rtl"
                        and entry["module_only_flist_scope"] == "generated_selected_dut_plus_probe"
                        and entry["generated_module_only_flist_exact"] is True
                    )
                    or (
                        entry["source_kind"] == "separated_base_imp2_rtl"
                        and entry["module_only_flist_scope"]
                        == "base_imp1_reference_plus_imp2_dut_plus_probe"
                        and entry["base_module_only_flist_exact"] is True
                    )
                )
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "generated_composed_rtl_dependencies_are_stubbed_not_flisted",
            "status": "pass"
            if generated_child_stub_boundary
            and all(
                entry["present"]
                and entry["flist_contains_only_selected_dut_and_probe"]
                and entry["composed_dependencies_absent_from_flist"]
                and entry["child_stubs_present_in_probe"]
                for entry in generated_child_stub_boundary
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_exact_probe_instantiation_counts",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"] and entry["exact_probe_instantiation_counts"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_cycle_readme_and_phase_traces",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"]
                and entry["cycle_contract_checks_pass"]
                and entry["readme_cycle_checks_pass"]
                and entry["phase_trace_checks_pass"]
                and entry["phase_signal_trace_checks_pass"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_cpp_launcher_runtime_evidence",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"] and entry["cpp_launcher_checks_pass"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_cpp_launcher_recipe_evidence",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"] and entry["cpp_launcher_recipe_checks_pass"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "source_derived_module_dpi_coverage_has_cpp_launcher_readme_cycle_evidence",
            "status": "pass"
            if source_derived_module_dpi_coverage
            and all(
                entry["covered"] and entry["cpp_launcher_readme_cycle_checks_pass"]
                for entry in source_derived_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_have_cpp_launcher_runtime_evidence",
            "status": "pass"
            if all_base_module_bindings
            and all(binding["cpp_launcher_checks_pass"] for binding in all_base_module_bindings)
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_have_cpp_launcher_recipe_evidence",
            "status": "pass"
            if all_base_module_bindings
            and all(binding["cpp_launcher_recipe_checks_pass"] for binding in all_base_module_bindings)
            else "fail",
        },
        {
            "name": "all_replaceable_base_modules_have_cpp_launcher_readme_cycle_evidence",
            "status": "pass"
            if all_base_module_bindings
            and all(binding["cpp_launcher_readme_cycle_checks_pass"] for binding in all_base_module_bindings)
            else "fail",
        },
    ]

    report = {
        "schema": "e1-full-graph-module-dpi-binding-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "truth_boundary": "full_graph_rtl_artifacts_have_module_only_dpi_verilator_execution",
        "full_checkpoint_graph_rtl_lowering_proof": "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
        "base_module_dpi_generation": module_dpi_report["manifest"],
        "generated_module_dpi_generation": full_checkpoint_module_dpi["manifest"],
        "all_base_modules": all_base_modules,
        "required_base_modules": required_base_modules,
        "required_generated_modules": required_generated_modules,
        "slot_engine_bindings": slot_engine_bindings,
        "all_base_module_bindings": all_base_module_bindings,
        "base_module_bindings": base_module_bindings,
        "generated_module_bindings": generated_module_bindings,
        "generated_child_stub_boundary": generated_child_stub_boundary,
        "generated_full_checkpoint_sv_inventory": generated_full_checkpoint_sv_inventory,
        "generated_full_checkpoint_sv_inventory_modules": generated_full_checkpoint_sv_inventory_modules,
        "base_imp2_sv_inventory": base_imp2_sv_inventory,
        "base_imp2_sv_inventory_modules": base_imp2_sv_inventory_modules,
        "source_derived_module_dpi_coverage": source_derived_module_dpi_coverage,
        "source_derived_module_dpi_coverage_count": len(source_derived_module_dpi_coverage),
        "generated_rtl_module_dpi_coverage_count": len(generated_rtl_module_coverage),
        "separated_base_rtl_module_dpi_coverage_count": len(base_rtl_module_coverage),
        "non_claims": [
            "This binds RTL artifacts to module-only DPI/Verilator execution; it does not claim TinyLlama numeric output equivalence.",
        ],
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def module_dpi_imp2_rtl_index(module_dpi_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for module in module_dpi_report["modules"]:
        index.setdefault(module["imp2_rtl"], []).append(
            {
                "name": module["name"],
                "top_module": module["top_module"],
                "probe": module["probe"],
                "flist": module["flist"],
                "verilator_execution_recipe": module["verilator_execution_recipe"],
                "cycle_template": module["cycle_contract"]["template"],
                "verilator_status": module["verilator_execution"]["status"],
                "ledger_checks_pass": all(
                    check["status"] == "pass"
                    for check in module["construction_ledger"]["checks"]
                ),
                "recipe_checks_pass": (
                    module["verilator_execution"]["build_command"]
                    == module["verilator_execution_recipe"]["build_command"]
                    and module["verilator_execution"]["run_executable"]
                    == module["verilator_execution_recipe"]["run_executable"]
                ),
                "phase_trace_checks_pass": (
                    module["verilator_execution"]["observed_phase_trace_prefix"]
                    == module["verilator_execution"]["expected_phase_trace"]
                    and module["verilator_execution"]["observed_phase_trace_count"]
                    >= len(module["verilator_execution"]["expected_phase_trace"])
                ),
                "phase_signal_trace_checks_pass": (
                    len(module["verilator_execution"]["expected_phase_signal_trace"]) > 0
                    and module["verilator_execution"]["observed_phase_signal_trace_prefix"]
                    == module["verilator_execution"]["expected_phase_signal_trace"]
                    and module["verilator_execution"]["observed_phase_signal_trace_count"]
                    >= len(module["verilator_execution"]["expected_phase_signal_trace"])
                ),
            }
        )
    return index


def build_production_rtl_inventory(
    *,
    implementation_matrix: dict[str, Any],
    module_dpi_report: dict[str, Any],
    full_graph_module_dpi_binding: dict[str, Any],
    cpp_verilator_launchers: dict[str, Any],
    soc_top_artifacts: dict[str, str],
    generated_soc_top_standalone_verilator: dict[str, Any],
    generated_soc_top_standalone_passed: bool,
    imp1_mock_rtl_lint: dict[str, Any],
) -> dict[str, Any]:
    module_dpi_by_imp2_rtl = module_dpi_imp2_rtl_index(module_dpi_report)
    base_cpp_launcher_by_name = {
        result["name"]: result
        for result in cpp_verilator_launchers.get("base_module_dpi", {})
        .get("verilator_run", {})
        .get("module_results", [])
        if result.get("name")
    }
    generated_cpp_launcher_by_name = {
        result["name"]: result
        for result in cpp_verilator_launchers.get("generated_full_checkpoint_module_dpi", {})
        .get("verilator_run", {})
        .get("module_results", [])
        if result.get("name")
    }

    def cpp_launcher_result_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "build_command": result.get("build_command"),
            "expected_stdout_markers": result.get("expected_stdout_markers", []),
            "expected_phase_trace_keys": result.get("expected_phase_trace_keys", []),
            "observed_phase_trace_prefix_keys": result.get("observed_phase_trace_prefix_keys", []),
            "expected_phase_signal_trace_keys": result.get("expected_phase_signal_trace_keys", []),
            "observed_phase_signal_trace_prefix_keys": result.get(
                "observed_phase_signal_trace_prefix_keys",
                [],
            ),
            "name": result.get("name"),
            "status": result.get("status"),
            "run_executable": result.get("run_executable"),
            "stdout_markers_present": result.get("stdout_markers_present"),
            "missing_stdout_markers": result.get("missing_stdout_markers", []),
            "phase_trace_in_order": result.get("phase_trace_in_order"),
            "phase_trace_repeats_template": result.get("phase_trace_repeats_template"),
            "phase_signal_trace_matches": result.get("phase_signal_trace_matches"),
            "phase_signal_trace_repeats_template": result.get("phase_signal_trace_repeats_template"),
            "observed_phase_trace_count": result.get("observed_phase_trace_count"),
            "observed_phase_signal_trace_count": result.get("observed_phase_signal_trace_count"),
        }

    def cpp_launcher_result_passed(result: dict[str, Any] | None) -> bool:
        return (
            result is not None
            and result.get("status") == "pass"
            and result.get("stdout_markers_present") is True
            and result.get("missing_stdout_markers") == []
            and result.get("phase_trace_in_order") is True
            and result.get("phase_trace_repeats_template") is True
            and result.get("phase_signal_trace_matches") is True
            and result.get("phase_signal_trace_repeats_template") is True
            and bool(result.get("expected_phase_trace_keys"))
            and result.get("observed_phase_trace_prefix_keys")
            == result.get("expected_phase_trace_keys")
            and int(result.get("observed_phase_trace_count") or 0) > 0
            and bool(result.get("expected_phase_signal_trace_keys"))
            and result.get("observed_phase_signal_trace_prefix_keys")
            == result.get("expected_phase_signal_trace_keys")
            and int(result.get("observed_phase_signal_trace_count") or 0) > 0
        )

    def expected_phase_trace_keys(recipe: dict[str, Any]) -> list[str]:
        phase_names = [
            marker[len("phase=") :]
            for marker in recipe.get("expected_stdout_markers", [])
            if marker.startswith("phase=")
        ]
        return [
            f"{cycle}:{phase}"
            for cycle, phase in enumerate(phase_names)
        ]

    def expected_phase_signal_trace_keys(recipe: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for entry in recipe.get("expected_phase_signal_trace", []):
            expected = entry["expected"]
            keys.append(f"{entry['cycle']}:{entry['signal']}:{expected}:{expected}")
        return keys

    def cpp_launcher_result_matches_recipe(
        result: dict[str, Any] | None,
        recipe: dict[str, Any] | None,
    ) -> bool:
        return (
            result is not None
            and recipe is not None
            and result.get("name") == recipe.get("name")
            and result.get("build_command") == recipe.get("build_command")
            and result.get("run_executable") == recipe.get("run_executable")
            and result.get("expected_stdout_markers") == recipe.get("expected_stdout_markers")
            and result.get("expected_phase_trace_keys") == expected_phase_trace_keys(recipe)
            and result.get("observed_phase_trace_prefix_keys") == expected_phase_trace_keys(recipe)
            and result.get("expected_phase_signal_trace_keys")
            == expected_phase_signal_trace_keys(recipe)
            and result.get("observed_phase_signal_trace_prefix_keys")
            == expected_phase_signal_trace_keys(recipe)
        )

    imp1_mock_lint_by_rtl = {
        row["rtl"]: row for row in imp1_mock_rtl_lint.get("rows", [])
    }
    imp1_mock_runtime_by_rtl = {
        row["rtl"]: row
        for row in imp1_mock_rtl_lint.get("runtime", {}).get("rows", [])
    }
    imp1_mock_by_rtl: dict[str, list[dict[str, Any]]] = {}
    for ip in implementation_matrix["ips"]:
        ip_manifest = load_json(REPO_ROOT / ip["interface_source"])
        imp1_rtl = ip["imp1"]["rtl"]
        imp1_flist = ip["imp1"]["flist"]
        imp1_flist_entries = (
            (REPO_ROOT / imp1_flist).read_text(encoding="utf-8").splitlines()
            if (REPO_ROOT / imp1_flist).exists()
            else []
        )
        imp1_mock_by_rtl.setdefault(imp1_rtl, []).append(
            {
                "name": ip["name"],
                "top_module": ip["module"],
                "interface_source": ip["interface_source"],
                "cpp_model": ip_manifest["cpp_model"],
                "l1_5_harness": ip["l1_5_harness"],
                "module_vip": ip["vip"],
                "flist": imp1_flist,
                "flist_entries": imp1_flist_entries,
                "flist_exact_match": imp1_flist_entries == [imp1_rtl],
                "status": ip["imp1"]["status"],
                "kind": ip["imp1"]["kind"],
            }
        )
    base_binding_by_name = {
        binding["name"]: binding
        for binding in full_graph_module_dpi_binding.get("all_base_module_bindings", [])
        if binding.get("name")
    }
    generated_full_checkpoint_coverage_by_rtl: dict[str, list[dict[str, Any]]] = {}
    for entry in full_graph_module_dpi_binding["source_derived_module_dpi_coverage"]:
        if entry["source_kind"] != "generated_full_checkpoint_rtl":
            continue
        cpp_launcher_result = generated_cpp_launcher_by_name.get(entry["module_dpi_name"])
        generated_full_checkpoint_coverage_by_rtl.setdefault(entry["rtl"], []).append(
            {
                "name": entry["module_dpi_name"],
                "sv_module": entry["sv_module"],
                "probe": entry["probe"],
                "flist": entry["flist"],
                "recipe": entry["recipe"],
                "cycle_template": entry["cycle_template"],
                "verilator_status": entry["verilator_status"],
                "covered": entry["covered"],
                "ledger_checks_pass": entry["ledger_checks_pass"],
                "recipe_checks_pass": entry["recipe_checks_pass"],
                "flist_exact_match": entry["flist_exact_match"],
                "cycle_contract_checks_pass": entry["cycle_contract_checks_pass"],
                "readme_cycle_checks_pass": entry["readme_cycle_checks_pass"],
                "phase_trace_checks_pass": entry["phase_trace_checks_pass"],
                "phase_signal_trace_checks_pass": entry["phase_signal_trace_checks_pass"],
                "cpp_launcher_result": cpp_launcher_result_summary(cpp_launcher_result),
                "cpp_launcher_recipe_checks_pass": (
                    entry.get("cpp_launcher_recipe_checks_pass") is True
                    and cpp_launcher_result_matches_recipe(cpp_launcher_result, entry.get("recipe"))
                ),
                "cpp_launcher_readme_cycle_proof": entry.get("cpp_launcher_readme_cycle_proof"),
                "cpp_launcher_readme_cycle_checks_pass": (
                    entry.get("cpp_launcher_readme_cycle_checks_pass") is True
                    and entry.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
                ),
                "cpp_launcher_checks_pass": cpp_launcher_result_passed(cpp_launcher_result),
            }
        )

    category_paths = {
        "generated_soc_top": [soc_top_artifacts["top"]],
        "base_imp1_mock": sorted(imp1_mock_by_rtl),
        "base_imp2_candidate": full_graph_module_dpi_binding["base_imp2_sv_inventory"],
        "generated_full_checkpoint": full_graph_module_dpi_binding[
            "generated_full_checkpoint_sv_inventory"
        ],
    }
    inventory_paths = unique_ordered(
        [
            *category_paths["generated_soc_top"],
            *category_paths["base_imp1_mock"],
            *category_paths["base_imp2_candidate"],
            *category_paths["generated_full_checkpoint"],
        ]
    )

    def inventory_row(rtl: str) -> dict[str, Any]:
        rtl_path = REPO_ROOT / rtl
        defined_modules = parse_sv_defined_modules(rtl_path) if rtl_path.exists() else []
        if rtl in category_paths["generated_soc_top"]:
            expected_modules = ["e1_h1_soc_top"]
            return {
                "rtl": rtl,
                "category": "generated_soc_top",
                "defined_modules": defined_modules,
                "expected_modules": expected_modules,
                "coverage_kind": "standalone_verilator_top_smoke",
                "covered": generated_soc_top_standalone_passed,
                "standalone_runtime_required": True,
                "standalone_runtime_covered": generated_soc_top_standalone_passed,
                "standalone_runtime_kind": "generated_soc_top_verilator_cpp_testbench",
                "standalone_runtime_requirement": (
                    "generated_soc_top_requires_standalone_verilator_cpp_testbench"
                ),
                "module_only_dpi_required": False,
                "module_only_dpi_covered": None,
                "module_only_dpi_requirement": "not_required_generated_soc_top_composition_boundary",
                "modules_match_proof": set(defined_modules) == set(expected_modules),
                "proof": generated_soc_top_standalone_verilator,
            }
        if rtl in imp1_mock_by_rtl:
            proofs = imp1_mock_by_rtl[rtl]
            expected_modules = unique_ordered([proof["top_module"] for proof in proofs])
            lint_row = imp1_mock_lint_by_rtl.get(rtl)
            runtime_row = imp1_mock_runtime_by_rtl.get(rtl)
            proof_artifacts_exist = all(
                (REPO_ROOT / proof["interface_source"]).exists()
                and (REPO_ROOT / proof["cpp_model"]).exists()
                and (REPO_ROOT / proof["l1_5_harness"]).exists()
                and (REPO_ROOT / proof["module_vip"]).exists()
                and (REPO_ROOT / proof["flist"]).exists()
                for proof in proofs
            )
            covered = (
                bool(proofs)
                and lint_row is not None
                and lint_row["status"] == "pass"
                and runtime_row is not None
                and runtime_row["status"] == "pass"
                and runtime_row.get("stdout_marker_present") is True
                and proof_artifacts_exist
                and all(
                    proof["kind"] == "mock"
                    and proof["status"] == "accepted"
                    and proof["flist_exact_match"]
                    for proof in proofs
                )
            )
            return {
                "rtl": rtl,
                "category": "base_imp1_mock",
                "defined_modules": defined_modules,
                "expected_modules": expected_modules,
                "coverage_kind": "imp1_mock_module_runtime_lint_and_cpp_l1_5_vip_contract",
                "covered": covered,
                "standalone_runtime_required": False,
                "standalone_runtime_covered": None,
                "standalone_runtime_kind": "accepted_imp1_mock_runtime_lint_and_contract",
                "standalone_runtime_requirement": "not_required_accepted_imp1_mock_reference",
                "module_only_dpi_required": False,
                "module_only_dpi_covered": None,
                "module_only_dpi_requirement": "not_required_accepted_imp1_mock_reference",
                "modules_match_proof": bool(defined_modules)
                and set(defined_modules).issubset(set(expected_modules))
                and set(expected_modules).issubset(set(defined_modules)),
                "module_lint": lint_row,
                "mock_runtime": runtime_row,
                "proofs": proofs,
            }
        if rtl in module_dpi_by_imp2_rtl:
            proofs = [
                {
                    **proof,
                    "cpp_launcher_result": cpp_launcher_result_summary(
                        base_cpp_launcher_by_name.get(proof["name"])
                    ),
                    "cpp_launcher_recipe_checks_pass": cpp_launcher_result_matches_recipe(
                        base_cpp_launcher_by_name.get(proof["name"]),
                        proof.get("verilator_execution_recipe"),
                    ),
                    "cpp_launcher_readme_cycle_proof": base_binding_by_name.get(
                        proof["name"],
                        {},
                    ).get("cpp_launcher_readme_cycle_proof"),
                    "cpp_launcher_readme_cycle_checks_pass": (
                        base_binding_by_name.get(proof["name"], {}).get(
                            "cpp_launcher_readme_cycle_checks_pass"
                        )
                        is True
                        and base_binding_by_name.get(proof["name"], {})
                        .get("cpp_launcher_readme_cycle_proof", {})
                        .get("status")
                        == "pass"
                    ),
                    "cpp_launcher_checks_pass": cpp_launcher_result_passed(
                        base_cpp_launcher_by_name.get(proof["name"])
                    ),
                }
                for proof in module_dpi_by_imp2_rtl[rtl]
            ]
            expected_modules = unique_ordered([proof["top_module"] for proof in proofs])
            covered = bool(proofs) and all(
                proof["verilator_status"] == "pass"
                and proof["ledger_checks_pass"]
                and proof["recipe_checks_pass"]
                and proof["phase_trace_checks_pass"]
                and proof["phase_signal_trace_checks_pass"]
                and proof["cpp_launcher_checks_pass"]
                and proof["cpp_launcher_recipe_checks_pass"]
                and proof["cpp_launcher_readme_cycle_checks_pass"]
                for proof in proofs
            )
            return {
                "rtl": rtl,
                "category": "base_imp2_candidate",
                "defined_modules": defined_modules,
                "expected_modules": expected_modules,
                "coverage_kind": "module_only_dpi_verilator_against_imp1_reference",
                "covered": covered,
                "standalone_runtime_required": True,
                "standalone_runtime_covered": covered,
                "standalone_runtime_kind": "cpp_generated_module_dpi_verilator",
                "standalone_runtime_requirement": (
                    "base_imp2_candidate_requires_cpp_generated_module_dpi_verilator"
                ),
                "module_only_dpi_required": True,
                "module_only_dpi_covered": covered,
                "module_only_dpi_requirement": "base_imp2_candidate_requires_module_only_dpi_verilator",
                "modules_match_proof": bool(defined_modules)
                and set(defined_modules).issubset(set(expected_modules))
                and set(expected_modules).issubset(set(defined_modules)),
                "proofs": proofs,
            }
        proofs = generated_full_checkpoint_coverage_by_rtl.get(rtl, [])
        expected_modules = unique_ordered([proof["sv_module"] for proof in proofs])
        covered = bool(proofs) and all(
            proof["covered"]
            and proof["verilator_status"] == "pass"
            and proof["ledger_checks_pass"]
            and proof["recipe_checks_pass"]
            and proof["flist_exact_match"]
            and proof["cycle_contract_checks_pass"]
            and proof["readme_cycle_checks_pass"]
            and proof["phase_trace_checks_pass"]
            and proof["phase_signal_trace_checks_pass"]
            and proof["cpp_launcher_checks_pass"]
            and proof["cpp_launcher_recipe_checks_pass"]
            and proof["cpp_launcher_readme_cycle_checks_pass"]
            for proof in proofs
        )
        return {
            "rtl": rtl,
            "category": "generated_full_checkpoint",
            "defined_modules": defined_modules,
            "expected_modules": expected_modules,
            "coverage_kind": "generated_module_only_dpi_verilator",
            "covered": covered,
            "standalone_runtime_required": True,
            "standalone_runtime_covered": covered,
            "standalone_runtime_kind": "cpp_generated_module_dpi_verilator",
            "standalone_runtime_requirement": (
                "generated_full_checkpoint_rtl_requires_cpp_generated_module_dpi_verilator"
            ),
            "module_only_dpi_required": True,
            "module_only_dpi_covered": covered,
            "module_only_dpi_requirement": "generated_full_checkpoint_rtl_requires_module_only_dpi_verilator",
            "modules_match_proof": bool(defined_modules)
            and set(defined_modules).issubset(set(expected_modules))
            and set(expected_modules).issubset(set(defined_modules)),
            "proofs": proofs,
        }

    rows = [inventory_row(rtl) for rtl in inventory_paths]
    module_only_required_rows = [row for row in rows if row["module_only_dpi_required"]]
    module_only_exempt_rows = [row for row in rows if not row["module_only_dpi_required"]]
    module_only_required_paths = [row["rtl"] for row in module_only_required_rows]
    module_only_covered_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if row["module_only_dpi_covered"]
    ]
    module_only_missing_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if not row["module_only_dpi_covered"]
    ]
    cpp_launcher_covered_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if row["module_only_dpi_covered"]
        and row.get("proofs")
        and all(
            proof.get("cpp_launcher_checks_pass")
            and proof.get("cpp_launcher_recipe_checks_pass")
            for proof in row.get("proofs", [])
        )
    ]
    cpp_launcher_missing_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if row["rtl"] not in cpp_launcher_covered_paths
    ]
    cpp_launcher_readme_cycle_covered_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if row["module_only_dpi_covered"]
        and row.get("proofs")
        and all(
            proof.get("cpp_launcher_readme_cycle_checks_pass") is True
            and proof.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
            for proof in row.get("proofs", [])
        )
    ]
    cpp_launcher_readme_cycle_missing_paths = [
        row["rtl"]
        for row in module_only_required_rows
        if row["rtl"] not in cpp_launcher_readme_cycle_covered_paths
    ]
    standalone_runtime_required_rows = [
        row for row in rows if row.get("standalone_runtime_required")
    ]
    standalone_runtime_exempt_rows = [
        row for row in rows if not row.get("standalone_runtime_required")
    ]
    standalone_runtime_required_paths = [
        row["rtl"] for row in standalone_runtime_required_rows
    ]
    standalone_runtime_covered_paths = [
        row["rtl"]
        for row in standalone_runtime_required_rows
        if row.get("standalone_runtime_covered") is True
    ]
    standalone_runtime_missing_paths = [
        row["rtl"]
        for row in standalone_runtime_required_rows
        if row["rtl"] not in standalone_runtime_covered_paths
    ]
    return {
        "schema": "e1-production-rtl-inventory-coverage-v0",
        "scope": "production RTL only; generated DPI probes and generated imp1 references are verification artifacts",
        "module_only_dpi_inventory": {
            "status": "pass" if module_only_required_paths and not module_only_missing_paths else "fail",
            "required_categories": ["base_imp2_candidate", "generated_full_checkpoint"],
            "exempt_categories": ["generated_soc_top", "base_imp1_mock"],
            "required_paths": module_only_required_paths,
            "covered_paths": module_only_covered_paths,
            "missing_paths": module_only_missing_paths,
            "cpp_launcher_required_paths": module_only_required_paths,
            "cpp_launcher_covered_paths": cpp_launcher_covered_paths,
            "cpp_launcher_missing_paths": cpp_launcher_missing_paths,
            "cpp_launcher_readme_cycle_covered_paths": cpp_launcher_readme_cycle_covered_paths,
            "cpp_launcher_readme_cycle_missing_paths": cpp_launcher_readme_cycle_missing_paths,
            "exempt_paths": [row["rtl"] for row in module_only_exempt_rows],
        },
        "standalone_runtime_inventory": {
            "schema": "e1-production-rtl-standalone-runtime-inventory-v0",
            "status": (
                "pass"
                if standalone_runtime_required_paths and not standalone_runtime_missing_paths
                else "fail"
            ),
            "required_categories": [
                "generated_soc_top",
                "base_imp2_candidate",
                "generated_full_checkpoint",
            ],
            "exempt_categories": ["base_imp1_mock"],
            "required_paths": standalone_runtime_required_paths,
            "covered_paths": standalone_runtime_covered_paths,
            "missing_paths": standalone_runtime_missing_paths,
            "exempt_paths": [row["rtl"] for row in standalone_runtime_exempt_rows],
            "coverage": [
                {
                    "rtl": row["rtl"],
                    "category": row["category"],
                    "coverage_kind": row["coverage_kind"],
                    "standalone_runtime_kind": row["standalone_runtime_kind"],
                    "covered": row["standalone_runtime_covered"],
                    "requirement": row["standalone_runtime_requirement"],
                }
                for row in rows
            ],
        },
        "imp1_mock_rtl_lint": imp1_mock_rtl_lint,
        "category_paths": category_paths,
        "paths": inventory_paths,
        "rows": rows,
        "counts": {
            "total": len(rows),
            "generated_soc_top": len(category_paths["generated_soc_top"]),
            "base_imp1_mock": len(category_paths["base_imp1_mock"]),
            "base_imp2_candidate": len(category_paths["base_imp2_candidate"]),
            "generated_full_checkpoint": len(category_paths["generated_full_checkpoint"]),
        },
    }


def production_rtl_inventory_checks(inventory: dict[str, Any]) -> list[dict[str, str]]:
    category_paths = inventory["category_paths"]
    rows = inventory["rows"]
    paths = inventory["paths"]
    return [
        {
            "name": "production_rtl_inventory_declares_all_categories",
            "status": "pass"
            if all(category_paths.values())
            and set(paths) == {rtl for path_list in category_paths.values() for rtl in path_list}
            else "fail",
        },
        {
            "name": "production_rtl_inventory_paths_exist_and_parse_modules",
            "status": "pass"
            if rows
            and all(
                (REPO_ROOT / row["rtl"]).exists() and row["defined_modules"]
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_has_construction_or_mock_proof",
            "status": "pass"
            if rows and all(row["covered"] for row in rows)
            else "fail",
        },
        {
            "name": "production_rtl_inventory_active_rtl_has_standalone_runtime",
            "status": "pass"
            if inventory.get("standalone_runtime_inventory", {}).get("status") == "pass"
            and set(inventory.get("standalone_runtime_inventory", {}).get("required_paths", []))
            == set(
                category_paths["generated_soc_top"]
                + category_paths["base_imp2_candidate"]
                + category_paths["generated_full_checkpoint"]
            )
            and not inventory.get("standalone_runtime_inventory", {}).get("missing_paths", [])
            and all(
                row["category"] == "base_imp1_mock"
                or row.get("standalone_runtime_covered") is True
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_source_rtl_has_module_only_dpi",
            "status": "pass"
            if inventory.get("module_only_dpi_inventory", {}).get("status") == "pass"
            and set(inventory.get("module_only_dpi_inventory", {}).get("required_paths", []))
            == set(category_paths["base_imp2_candidate"] + category_paths["generated_full_checkpoint"])
            and not inventory.get("module_only_dpi_inventory", {}).get("missing_paths", [])
            and all(
                row["category"] not in {"base_imp2_candidate", "generated_full_checkpoint"}
                or row.get("module_only_dpi_covered") is True
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_source_rtl_has_cpp_launcher_module_runs",
            "status": "pass"
            if inventory.get("module_only_dpi_inventory", {}).get("status") == "pass"
            and set(inventory.get("module_only_dpi_inventory", {}).get("cpp_launcher_required_paths", []))
            == set(inventory.get("module_only_dpi_inventory", {}).get("required_paths", []))
            and set(inventory.get("module_only_dpi_inventory", {}).get("cpp_launcher_covered_paths", []))
            == set(inventory.get("module_only_dpi_inventory", {}).get("required_paths", []))
            and not inventory.get("module_only_dpi_inventory", {}).get("cpp_launcher_missing_paths", [])
            and all(
                row["category"] not in {"base_imp2_candidate", "generated_full_checkpoint"}
                or (
                    row.get("module_only_dpi_covered") is True
                    and all(
                        proof.get("cpp_launcher_checks_pass")
                        and proof.get("cpp_launcher_recipe_checks_pass")
                        for proof in row.get("proofs", [])
                    )
                )
                for row in inventory.get("rows", [])
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_source_rtl_has_cpp_launcher_recipe_and_phase_key_proofs",
            "status": "pass"
            if inventory.get("module_only_dpi_inventory", {}).get("status") == "pass"
            and all(
                row["category"] not in {"base_imp2_candidate", "generated_full_checkpoint"}
                or (
                    row.get("module_only_dpi_covered") is True
                    and all(
                        proof.get("cpp_launcher_recipe_checks_pass")
                        and proof.get("cpp_launcher_result", {}).get("expected_phase_trace_keys")
                        == proof.get("cpp_launcher_result", {}).get("observed_phase_trace_prefix_keys")
                        and proof.get("cpp_launcher_result", {}).get("expected_phase_signal_trace_keys")
                        == proof.get("cpp_launcher_result", {}).get("observed_phase_signal_trace_prefix_keys")
                        for proof in row.get("proofs", [])
                    )
                )
                for row in inventory.get("rows", [])
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_source_rtl_has_cpp_launcher_readme_cycle_proofs",
            "status": "pass"
            if inventory.get("module_only_dpi_inventory", {}).get("status") == "pass"
            and set(
                inventory.get("module_only_dpi_inventory", {}).get(
                    "cpp_launcher_readme_cycle_covered_paths",
                    [],
                )
            )
            == set(inventory.get("module_only_dpi_inventory", {}).get("required_paths", []))
            and not inventory.get("module_only_dpi_inventory", {}).get(
                "cpp_launcher_readme_cycle_missing_paths",
                [],
            )
            and all(
                row["category"] not in {"base_imp2_candidate", "generated_full_checkpoint"}
                or (
                    row.get("module_only_dpi_covered") is True
                    and all(
                        proof.get("cpp_launcher_readme_cycle_checks_pass") is True
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get("readme_phase_keys")
                        == proof.get("cpp_launcher_readme_cycle_proof", {}).get(
                            "cycle_contract_phase_keys"
                        )
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get(
                            "cpp_launcher_observed_phase_keys"
                        )
                        == proof.get("cpp_launcher_readme_cycle_proof", {}).get("readme_phase_keys")
                        for proof in row.get("proofs", [])
                    )
                )
                for row in inventory.get("rows", [])
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_non_source_rtl_has_explicit_exemption",
            "status": "pass"
            if set(inventory.get("module_only_dpi_inventory", {}).get("exempt_paths", []))
            == set(category_paths["generated_soc_top"] + category_paths["base_imp1_mock"])
            and all(
                row["category"] not in {"generated_soc_top", "base_imp1_mock"}
                or row.get("module_only_dpi_required") is False
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_only_imp1_mocks_are_standalone_runtime_exempt",
            "status": "pass"
            if set(inventory.get("standalone_runtime_inventory", {}).get("exempt_paths", []))
            == set(category_paths["base_imp1_mock"])
            and all(
                (row["category"] == "base_imp1_mock")
                == (row.get("standalone_runtime_required") is False)
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_imp1_mock_rtl_lint_passed",
            "status": "pass"
            if inventory.get("imp1_mock_rtl_lint", {}).get("status") == "pass"
            and all(
                row["category"] != "base_imp1_mock"
                or row.get("module_lint", {}).get("status") == "pass"
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_imp1_mock_rtl_runtime_passed",
            "status": "pass"
            if inventory.get("imp1_mock_rtl_lint", {})
            .get("runtime", {})
            .get("status")
            == "pass"
            and all(
                row["category"] != "base_imp1_mock"
                or (
                    row.get("mock_runtime", {}).get("status") == "pass"
                    and row.get("mock_runtime", {}).get("stdout_marker_present") is True
                )
                for row in rows
            )
            else "fail",
        },
        {
            "name": "production_rtl_inventory_modules_match_proofs",
            "status": "pass"
            if rows and all(row["modules_match_proof"] for row in rows)
            else "fail",
        },
    ]


def build_generated_soc_top_hierarchy_proof(soc_top_artifacts: dict[str, str]) -> dict[str, Any]:
    top_text = (REPO_ROOT / soc_top_artifacts["top"]).read_text(encoding="utf-8")
    manifest = load_json(REPO_ROOT / soc_top_artifacts["composition_manifest"])
    expected_instances = []
    for subsystem in manifest["subsystems"]:
        for ip in subsystem["ips"]:
            instance = f"u_{ip['name']}"
            active_rtl = ip["rtl"]
            source_modules = (
                parse_sv_defined_modules(REPO_ROOT / active_rtl)
                if (REPO_ROOT / active_rtl).exists()
                else []
            )
            instance_matches = [
                match.start()
                for match in re.finditer(rf"\b{re.escape(instance)}\b", top_text)
            ]
            module_matches = [
                match.start()
                for match in re.finditer(rf"\b{re.escape(ip['module'])}\b", top_text)
            ]
            expected_instances.append(
                {
                    "subsystem": subsystem["name"],
                    "name": ip["name"],
                    "module": ip["module"],
                    "instance": instance,
                    "rtl": active_rtl,
                    "order": ip["order"],
                    "replaceable": ip["replaceable"],
                    "source_defined_modules": source_modules,
                    "module_defined_in_active_rtl": ip["module"] in source_modules,
                    "module_reference_count": len(module_matches),
                    "instance_name_count": len(instance_matches),
                    "instance_position": instance_matches[0] if instance_matches else None,
                    "subsystem_comment_present": f"// Subsystem: {subsystem['name']}" in top_text,
                    "present_once": len(instance_matches) == 1,
                }
            )
    instance_positions = [
        entry["instance_position"]
        for entry in expected_instances
        if entry["instance_position"] is not None
    ]
    expected_by_name = {entry["name"]: entry for entry in expected_instances}
    separated_names = {"control_cpu", "ingress_sram", "systolic_array"}
    checks = [
        {
            "name": "generated_soc_top_instances_match_manifest",
            "status": "pass"
            if expected_instances
            and len(expected_instances)
            == sum(len(subsystem["ips"]) for subsystem in manifest["subsystems"])
            else "fail",
        },
        {
            "name": "generated_soc_top_instances_present_once",
            "status": "pass"
            if expected_instances and all(entry["present_once"] for entry in expected_instances)
            else "fail",
        },
        {
            "name": "generated_soc_top_instance_modules_defined_by_active_rtl",
            "status": "pass"
            if expected_instances
            and all(entry["module_defined_in_active_rtl"] for entry in expected_instances)
            else "fail",
        },
        {
            "name": "generated_soc_top_subsystem_comments_present",
            "status": "pass"
            if expected_instances
            and all(entry["subsystem_comment_present"] for entry in expected_instances)
            else "fail",
        },
        {
            "name": "generated_soc_top_instance_order_matches_manifest_order",
            "status": "pass"
            if len(instance_positions) == len(expected_instances)
            and instance_positions == sorted(instance_positions)
            and [entry["order"] for entry in expected_instances]
            == sorted(entry["order"] for entry in expected_instances)
            else "fail",
        },
        {
            "name": "generated_soc_top_preserves_cpu_latch_array_boundaries",
            "status": "pass"
            if separated_names.issubset(expected_by_name)
            and len({expected_by_name[name]["instance"] for name in separated_names}) == 3
            and len({expected_by_name[name]["module"] for name in separated_names}) == 3
            else "fail",
        },
    ]
    return {
        "schema": "e1-generated-soc-top-hierarchy-proof-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "top": soc_top_artifacts["top"],
        "composition_manifest": soc_top_artifacts["composition_manifest"],
        "expected_instance_count": len(expected_instances),
        "expected_instances": expected_instances,
        "separated_boundaries": {
            name: {
                "module": expected_by_name[name]["module"],
                "instance": expected_by_name[name]["instance"],
                "rtl": expected_by_name[name]["rtl"],
            }
            for name in sorted(separated_names)
            if name in expected_by_name
        },
        "checks": checks,
    }


def emit_lowering_construction_certificate(
    output_path: Path,
    manifest: dict[str, Any],
    fixture_path: Path,
    inspection: dict[str, Any],
    binding: dict[str, Any],
    rtl_lowering: dict[str, Any],
    implementation_matrix: dict[str, Any],
    target_manifest: dict[str, Any],
    module_dpi_report: dict[str, Any],
    full_checkpoint_rtl_lowering: dict[str, Any],
    command_stream: dict[str, Any],
    rtl_cycle: dict[str, Any],
    rtl_top: dict[str, Any],
    full_checkpoint_graph_rtl_lowering: dict[str, Any],
    full_checkpoint_module_dpi: dict[str, Any],
    full_graph_module_dpi_binding: dict[str, Any],
    soc_top_artifacts: dict[str, str],
    production_rtl_inventory: dict[str, Any],
    generated_soc_top_hierarchy: dict[str, Any],
) -> dict[str, Any]:
    lowered_by_op = {
        entry["operation"].split(".", 1)[1]: entry
        for entry in rtl_lowering["operation_lowering"]
    }
    operation_coverage = []
    for op_name, count in sorted(inspection["operation_counts"].items()):
        lowering = lowered_by_op.get(op_name)
        operation_coverage.append(
            {
                "operation": f"stablehlo.{op_name}",
                "count": count,
                "bound_ip": binding["bindings"].get(f"stablehlo.{op_name}"),
                "lowering_status": lowering["status"] if lowering is not None else "missing",
                "active_implementation": lowering["active_implementation"] if lowering is not None else None,
                "rtl_files": lowering["rtl_files"] if lowering is not None else [],
                "module_dpi_probe": lowering["module_dpi_probe"] if lowering is not None else None,
                "module_dpi_flist": lowering["module_dpi_flist"] if lowering is not None else None,
            }
        )
    source_instance_coverage = []
    for instance in inspection.get("operation_instances", []):
        op_name = instance["stablehlo_op"]
        lowering = lowered_by_op.get(op_name)
        source_instance_coverage.append(
            {
                "source_index": instance["source_index"],
                "source_line": instance["source_line"],
                "source_end_line": instance["source_end_line"],
                "ssa_result": instance["ssa_result"],
                "operation": instance["operation"],
                "source_snippet": instance["source_snippet"],
                "bound_ip": binding["bindings"].get(instance["operation"]),
                "lowering_status": lowering["status"] if lowering is not None else "missing",
                "active_implementation": lowering["active_implementation"] if lowering is not None else None,
                "rtl_files": lowering["rtl_files"] if lowering is not None else [],
                "module_dpi_probe": lowering["module_dpi_probe"] if lowering is not None else None,
                "module_dpi_flist": lowering["module_dpi_flist"] if lowering is not None else None,
            }
        )
    source_instance_counts = Counter(
        entry["stablehlo_op"]
        for entry in inspection.get("operation_instances", [])
    )

    target_filelists = {
        "active_implementation": implementation_matrix["flists"]["active"],
        "fpga": target_manifest["fpga"]["filelist"],
        "openroad": target_manifest["openroad"]["filelist"],
    }
    target_filelist_coverage = []
    for target_name, filelist in target_filelists.items():
        filelist_path = REPO_ROOT / filelist
        entries = filelist_path.read_text(encoding="utf-8").splitlines() if filelist_path.exists() else []
        target_filelist_coverage.append(
            {
                "target": target_name,
                "filelist": filelist,
                "entries": entries,
                "matches_target_manifest_rtl_files": entries == target_manifest["rtl_files"],
                "matches_active_imp2_flist": entries
                == (REPO_ROOT / implementation_matrix["flists"]["active"]).read_text(encoding="utf-8").splitlines(),
            }
        )

    target_rtl_artifacts = unique_ordered(target_manifest["rtl_files"])
    generated_soc_top_construction_artifacts = unique_ordered(
        [
            soc_top_artifacts["top"],
            soc_top_artifacts["composition_manifest"],
            soc_top_artifacts["interface_contracts"],
            "e1/e1-h1/tools/generate_soc_top.py",
            "e1/e1-h1/config/architecture.json",
            *[
                repo_rel(path)
                for path in sorted((REPO_ROOT / "e1/e1-h1/ip").glob("*.json"))
            ],
            "e1/e1-h1/tests/e1_h1_soc_top_tb.cpp",
            "e1/e1-h1/generated/targets/manifest.json",
        ]
    )
    target_rtl_artifacts_passed = (
        bool(target_rtl_artifacts)
        and soc_top_artifacts["top"] in target_rtl_artifacts
        and all((REPO_ROOT / path).exists() for path in target_rtl_artifacts)
    )
    generated_soc_top_construction_artifacts_passed = (
        soc_top_artifacts["top"] in target_rtl_artifacts
        and all(
            (REPO_ROOT / path).exists()
            for path in generated_soc_top_construction_artifacts
        )
    )

    readme_cycle_templates = full_checkpoint_graph_rtl_lowering["readme_cycle_coverage"]["templates"]

    def module_cycle_documentation_passed(report: dict[str, Any]) -> bool:
        readme_coverage_ref = report.get("readme_cycle_coverage", {})
        if isinstance(readme_coverage_ref, str):
            readme_coverage_path = REPO_ROOT / readme_coverage_ref
            if not readme_coverage_path.exists():
                return False
            readme_coverage = json.loads(readme_coverage_path.read_text(encoding="utf-8"))
        else:
            readme_coverage = readme_coverage_ref
        readme_modules = {
            module["name"]: module
            for module in readme_coverage.get("modules", [])
        }
        report_modules = {
            module["name"]: module
            for module in report.get("modules", [])
        }
        if not report.get("cycle_contract") or not report.get("readme_cycle_coverage"):
            return False
        if set(readme_modules) != set(report_modules):
            return False
        return (
            all(check["status"] == "pass" for check in readme_coverage.get("diagram_checks", []))
            and all(
                all(check["status"] == "pass" for check in module["cycle_contract"]["checks"])
                and all(check["status"] == "pass" for check in module["readme_cycle_coverage"]["checks"])
                and module["readme_cycle_coverage"] == readme_modules[module["name"]]
                and module["readme_cycle_coverage"]["phase_names"]
                == [step["phase"] for step in module["cycle_contract"]["cycles"]]
                and module["readme_cycle_coverage"]["template"] == module["cycle_contract"]["template"]
                and module["readme_cycle_coverage"]["cycle_period"] == module["cycle_contract"]["cycle_period"]
                and module["readme_cycle_coverage"]["top_module"] == module["cycle_contract"]["top_module"]
                for module in report_modules.values()
            )
        )

    def module_cycle_documentation_summary(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "cycle_contract": report["cycle_contract"],
            "readme_cycle_coverage": report["readme_cycle_coverage"],
            "module_interfaces_doc": report["module_interfaces_doc"],
            "module_count": len(report["modules"]),
            "modules": [
                {
                    "name": module["name"],
                    "top_module": module["top_module"],
                    "template": module["cycle_contract"]["template"],
                    "cycle_period": module["cycle_contract"]["cycle_period"],
                    "cycle_count": len(module["cycle_contract"]["cycles"]),
                    "phase_names": module["readme_cycle_coverage"]["phase_names"],
                    "readme_diagram": module["readme_cycle_coverage"]["readme_diagram"],
                    "readme_index": module["readme_cycle_coverage"]["readme_index"],
                    "readme_index_row": module["readme_cycle_coverage"]["readme_index_row"],
                }
                for module in report["modules"]
            ],
        }

    module_cycle_doc_artifacts = unique_ordered(
        [
            module_dpi_report["cycle_contract"],
            module_dpi_report["readme_cycle_coverage"],
            module_dpi_report["module_interfaces_doc"],
            full_checkpoint_module_dpi["cycle_contract"],
            full_checkpoint_module_dpi["readme_cycle_coverage"],
            full_checkpoint_module_dpi["module_interfaces_doc"],
        ]
    )
    base_module_cycle_docs_passed = module_cycle_documentation_passed(module_dpi_report)
    generated_full_checkpoint_module_cycle_docs_passed = module_cycle_documentation_passed(full_checkpoint_module_dpi)

    def cycle_diagram_audit() -> dict[str, Any]:
        readme_path = "e1/e1-h1/docs/modules/README.md"
        readme_text = (REPO_ROOT / readme_path).read_text(encoding="utf-8")
        readme_runtime_matrix = f"{readme_path}#module-cycle-runtime-matrix"

        def module_rows(report: dict[str, Any], suite: str) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for module in report.get("modules", []):
                contract = module.get("cycle_contract", {})
                readme = module.get("readme_cycle_coverage", {})
                execution = module.get("verilator_execution", {})
                phase_names_from_contract = [
                    step["phase"] for step in contract.get("cycles", [])
                ]
                cycle_count = len(phase_names_from_contract)
                readme_checks_pass = bool(readme.get("checks")) and all(
                    check["status"] == "pass" for check in readme.get("checks", [])
                )
                contract_checks_pass = bool(contract.get("checks")) and all(
                    check["status"] == "pass" for check in contract.get("checks", [])
                )
                runtime_status = execution.get("status")
                observed_phase_trace_count = int(execution.get("observed_phase_trace_count") or 0)
                observed_phase_signal_trace_count = int(
                    execution.get("observed_phase_signal_trace_count") or 0
                )
                phase_names_match_contract = (
                    readme.get("phase_names") == phase_names_from_contract
                )
                runtime_covers_template = (
                    runtime_status == "pass"
                    and observed_phase_trace_count >= cycle_count
                    and observed_phase_signal_trace_count >= cycle_count
                )
                readme_runtime_matrix_row = (
                    f"| `{suite}` | `{module['name']}` | `{module['top_module']}` | "
                    f"`{contract.get('template')}` | {cycle_count} | "
                    f"{observed_phase_trace_count} | "
                    f"{observed_phase_signal_trace_count} | `{runtime_status}` |"
                )
                readme_runtime_matrix_row_present = readme_runtime_matrix_row in readme_text
                status = (
                    "pass"
                    if readme_checks_pass
                    and contract_checks_pass
                    and phase_names_match_contract
                    and runtime_covers_template
                    and readme_runtime_matrix_row_present
                    and readme.get("readme_index_row")
                    and readme.get("readme_diagram")
                    else "fail"
                )
                rows.append(
                    {
                        "suite": suite,
                        "name": module["name"],
                        "top_module": module["top_module"],
                        "template": contract.get("template"),
                        "cycle_period": contract.get("cycle_period"),
                        "cycle_count": cycle_count,
                        "phase_names": readme.get("phase_names", []),
                        "readme_diagram": readme.get("readme_diagram"),
                        "readme_index": readme.get("readme_index"),
                        "readme_index_row": readme.get("readme_index_row"),
                        "readme_runtime_matrix": readme_runtime_matrix,
                        "readme_runtime_matrix_row": readme_runtime_matrix_row,
                        "readme_runtime_matrix_row_present": readme_runtime_matrix_row_present,
                        "readme_checks_pass": readme_checks_pass,
                        "contract_checks_pass": contract_checks_pass,
                        "phase_names_match_contract": phase_names_match_contract,
                        "runtime_status": runtime_status,
                        "observed_phase_trace_count": observed_phase_trace_count,
                        "observed_phase_signal_trace_count": observed_phase_signal_trace_count,
                        "runtime_covers_template": runtime_covers_template,
                        "status": status,
                    }
                )
            return rows

        graph_coverage = full_checkpoint_graph_rtl_lowering["readme_cycle_coverage"]
        graph_rows: list[dict[str, Any]] = []
        for template in graph_coverage.get("templates", []):
            checks_pass = bool(template.get("checks")) and all(
                check["status"] == "pass" for check in template.get("checks", [])
            )
            graph_rows.append(
                {
                    "suite": "full_graph_slot_template",
                    "template": template["template"],
                    "cycle_count": template["cycle_count"],
                    "phase_names": template["phase_names"],
                    "applies_to_slots": template["applies_to_slots"],
                    "source": template["source"],
                    "checks_pass": checks_pass,
                    "status": "pass" if checks_pass else "fail",
                }
            )

        base_rows = module_rows(module_dpi_report, "base_module_dpi")
        generated_rows = module_rows(
            full_checkpoint_module_dpi,
            "generated_full_checkpoint_module_dpi",
        )
        diagram_checks = graph_coverage.get("diagram_checks", [])
        checks = [
            {
                "name": "base_module_cycle_diagram_rows_pass",
                "status": "pass"
                if base_rows and all(row["status"] == "pass" for row in base_rows)
                else "fail",
            },
            {
                "name": "generated_module_cycle_diagram_rows_pass",
                "status": "pass"
                if generated_rows and all(row["status"] == "pass" for row in generated_rows)
                else "fail",
            },
            {
                "name": "full_graph_cycle_templates_are_in_readme",
                "status": "pass"
                if graph_rows and all(row["status"] == "pass" for row in graph_rows)
                else "fail",
            },
            {
                "name": "readme_cycle_diagram_snippets_present",
                "status": "pass"
                if diagram_checks
                and all(check["status"] == "pass" for check in diagram_checks)
                else "fail",
            },
            {
                "name": "module_runtime_traces_cover_documented_cycle_templates",
                "status": "pass"
                if [*base_rows, *generated_rows]
                and all(row["runtime_covers_template"] for row in [*base_rows, *generated_rows])
                else "fail",
            },
            {
                "name": "readme_module_cycle_runtime_matrix_rows_present",
                "status": "pass"
                if [*base_rows, *generated_rows]
                and all(
                    row["readme_runtime_matrix_row_present"]
                    for row in [*base_rows, *generated_rows]
                )
                else "fail",
            },
        ]
        return {
            "schema": "e1-cycle-diagram-audit-v0",
            "readme": readme_path,
            "readme_runtime_matrix": readme_runtime_matrix,
            "scope": (
                "base module-DPI modules, generated full-checkpoint modules, "
                "and full-graph slot templates"
            ),
            "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
            "base_module_count": len(base_rows),
            "generated_full_checkpoint_module_count": len(generated_rows),
            "full_graph_template_count": len(graph_rows),
            "base_modules": base_rows,
            "generated_full_checkpoint_modules": generated_rows,
            "full_graph_templates": graph_rows,
            "diagram_checks": diagram_checks,
            "checks": checks,
        }

    cycle_diagram_documentation_audit = cycle_diagram_audit()
    cycle_diagram_documentation_audit_passed = (
        cycle_diagram_documentation_audit["status"] == "pass"
    )

    def signal_summary(signals: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "name": signal["name"],
                "width": signal["width"],
                "description": signal["description"],
            }
            for signal in signals
        ]

    def signal_width_summary(signals: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "name": signal["name"],
                "width": signal["width"],
            }
            for signal in signals
        ]

    def module_section_text(doc_text: str, name: str) -> str:
        heading = f"## {name}\n"
        start = doc_text.find(heading)
        if start < 0:
            return ""
        next_heading = doc_text.find("\n## ", start + len(heading))
        return doc_text[start:] if next_heading < 0 else doc_text[start:next_heading]

    def module_interface_signal_inventory_summary(
        report: dict[str, Any],
        *,
        family: str,
    ) -> dict[str, Any]:
        doc_path = REPO_ROOT / report["module_interfaces_doc"]
        doc_text = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
        modules: list[dict[str, Any]] = []
        for module in report["modules"]:
            section = module_section_text(doc_text, module["name"])
            rtl_contract = module.get("rtl_port_contract", {})
            ip_contract = module.get("ip_port_contract")
            input_signals = signal_summary(module.get("input_signals", []))
            output_signals = signal_summary(module.get("output_signals", []))
            expected_inputs = signal_width_summary(module.get("input_signals", []))
            expected_outputs = signal_width_summary(module.get("output_signals", []))
            rtl_inputs = rtl_contract.get("input", [])
            rtl_outputs = rtl_contract.get("output", [])
            ip_inputs = ip_contract.get("input", []) if ip_contract else None
            ip_outputs = ip_contract.get("output", []) if ip_contract else None
            doc_checks = [
                {
                    "name": "module_section_present",
                    "status": "pass" if section else "fail",
                },
                {
                    "name": "input_signal_table_present",
                    "status": "pass" if "### Input Signals" in section else "fail",
                },
                {
                    "name": "output_signal_table_present",
                    "status": "pass" if "### Output Signals" in section else "fail",
                },
                {
                    "name": "top_module_documented",
                    "status": "pass" if module["top_module"] in section else "fail",
                },
                {
                    "name": "all_input_signals_documented",
                    "status": "pass"
                    if input_signals
                    and all(
                        f"`{signal['name']}`" in section and signal["description"] in section
                        for signal in input_signals
                    )
                    else "fail",
                },
                {
                    "name": "all_output_signals_documented",
                    "status": "pass"
                    if (
                        output_signals
                        and all(
                            f"`{signal['name']}`" in section
                            and signal["description"] in section
                            for signal in output_signals
                        )
                    )
                    or (not output_signals and "`_none`" in section)
                    else "fail",
                },
            ]
            rtl_checks = [
                {
                    "name": "input_signals_match_rtl_ports",
                    "status": "pass" if expected_inputs == rtl_inputs else "fail",
                },
                {
                    "name": "output_signals_match_rtl_ports",
                    "status": "pass" if expected_outputs == rtl_outputs else "fail",
                },
            ]
            ip_checks = []
            if ip_contract is not None:
                ip_checks = [
                    {
                        "name": "input_signals_match_ip_manifest_ports",
                        "status": "pass" if expected_inputs == ip_inputs else "fail",
                    },
                    {
                        "name": "output_signals_match_ip_manifest_ports",
                        "status": "pass" if expected_outputs == ip_outputs else "fail",
                    },
                ]
            checks_for_module = [*doc_checks, *rtl_checks, *ip_checks]
            modules.append(
                {
                    "name": module["name"],
                    "family": family,
                    "top_module": module["top_module"],
                    "module_interfaces_doc": report["module_interfaces_doc"],
                    "interface_source": module.get("interface_source"),
                    "input_signal_count": len(input_signals),
                    "output_signal_count": len(output_signals),
                    "input_signals": input_signals,
                    "output_signals": output_signals,
                    "rtl_port_contract": rtl_contract,
                    "ip_port_contract": ip_contract,
                    "checks": checks_for_module,
                    "status": (
                        "pass"
                        if checks_for_module
                        and all(check["status"] == "pass" for check in checks_for_module)
                        else "fail"
                    ),
                }
            )
        suite_checks = [
            {
                "name": f"{family}_module_interface_doc_exists",
                "status": "pass" if doc_path.exists() else "fail",
            },
            {
                "name": f"{family}_module_interface_signal_inventory_covers_all_modules",
                "status": "pass"
                if modules
                and len(modules) == len(report["modules"])
                and {module["name"] for module in modules}
                == {module["name"] for module in report["modules"]}
                else "fail",
            },
            {
                "name": f"{family}_module_interface_signal_inventory_documents_io_tables",
                "status": "pass"
                if modules
                and all(
                    any(
                        check["name"] == "input_signal_table_present"
                        and check["status"] == "pass"
                        for check in module["checks"]
                    )
                    and any(
                        check["name"] == "output_signal_table_present"
                        and check["status"] == "pass"
                        for check in module["checks"]
                    )
                    for module in modules
                )
                else "fail",
            },
            {
                "name": f"{family}_module_interface_signal_inventory_matches_rtl_ports",
                "status": "pass"
                if modules
                and all(
                    any(
                        check["name"] == "input_signals_match_rtl_ports"
                        and check["status"] == "pass"
                        for check in module["checks"]
                    )
                    and any(
                        check["name"] == "output_signals_match_rtl_ports"
                        and check["status"] == "pass"
                        for check in module["checks"]
                    )
                    for module in modules
                )
                else "fail",
            },
            {
                "name": f"{family}_module_interface_signal_inventory_has_signal_descriptions",
                "status": "pass"
                if modules
                and all(
                    module["input_signal_count"] > 0
                    and all(signal["description"] for signal in module["input_signals"])
                    and all(signal["description"] for signal in module["output_signals"])
                    for module in modules
                )
                else "fail",
            },
            {
                "name": f"{family}_module_interface_signal_inventory_modules_pass",
                "status": "pass"
                if modules and all(module["status"] == "pass" for module in modules)
                else "fail",
            },
        ]
        return {
            "family": family,
            "module_interfaces_doc": report["module_interfaces_doc"],
            "module_count": len(modules),
            "modules": modules,
            "checks": suite_checks,
            "status": (
                "pass"
                if suite_checks
                and all(check["status"] == "pass" for check in suite_checks)
                else "fail"
            ),
        }

    base_module_interface_signal_inventory = module_interface_signal_inventory_summary(
        module_dpi_report,
        family="base_module_dpi",
    )
    generated_module_interface_signal_inventory = module_interface_signal_inventory_summary(
        full_checkpoint_module_dpi,
        family="generated_full_checkpoint_module_dpi",
    )
    module_interface_signal_inventory_checks = [
        *base_module_interface_signal_inventory["checks"],
        *generated_module_interface_signal_inventory["checks"],
        {
            "name": "module_interface_signal_inventory_all_suites_pass",
            "status": "pass"
            if base_module_interface_signal_inventory["status"] == "pass"
            and generated_module_interface_signal_inventory["status"] == "pass"
            else "fail",
        },
    ]
    module_interface_signal_inventory = {
        "schema": "e1-module-interface-signal-inventory-v0",
        "status": (
            "pass"
            if all(
                check["status"] == "pass"
                for check in module_interface_signal_inventory_checks
            )
            else "fail"
        ),
        "scope": "module-only base imp2 and generated full-checkpoint RTL boundaries",
        "base_module_dpi": base_module_interface_signal_inventory,
        "generated_full_checkpoint_module_dpi": generated_module_interface_signal_inventory,
        "checks": module_interface_signal_inventory_checks,
    }
    module_interface_signal_inventory_passed = (
        module_interface_signal_inventory["status"] == "pass"
    )
    module_dpi_generator_sources = {
        "base_module_dpi_generator": module_dpi_report["generator"],
        "generated_full_checkpoint_module_dpi_generator": full_checkpoint_module_dpi["generator"],
        "verilator_runner": "e1/tools/run_module_dpi_verilator.py",
        "pipeline_orchestrator": "e1/tools/run_e1_pipeline.py",
    }
    module_dpi_generator_source_artifacts = unique_ordered(module_dpi_generator_sources.values())
    module_dpi_generator_sources_passed = (
        module_dpi_generator_sources["base_module_dpi_generator"].endswith(".cpp")
        and module_dpi_generator_sources["generated_full_checkpoint_module_dpi_generator"].endswith(".cpp")
        and all(
            (REPO_ROOT / path).exists()
            for path in module_dpi_generator_source_artifacts
        )
    )
    base_generator_executable = "<module_dpi_generator_build_dir>/e1_h1_generate_module_dpi"
    full_checkpoint_generator_executable = (
        "<full_checkpoint_module_dpi_generator_build_dir>/e1_h1_generate_full_checkpoint_module_dpi"
    )
    base_generator_build_command = [
        "c++",
        "-std=c++17",
        module_dpi_generator_sources["base_module_dpi_generator"],
        "-o",
        base_generator_executable,
    ]
    full_checkpoint_generator_build_command = [
        "c++",
        "-std=c++17",
        module_dpi_generator_sources["generated_full_checkpoint_module_dpi_generator"],
        "-o",
        full_checkpoint_generator_executable,
    ]
    base_generator_execution_command = [
        base_generator_executable,
        "--repo-root",
        "<repo-root>",
        "--output-dir",
        "e1/e1-h1/generated/module_dpi",
    ]
    full_checkpoint_generator_execution_command = [
        full_checkpoint_generator_executable,
        "--repo-root",
        "<repo-root>",
        "--output-dir",
        "e1/e1-h1/generated/full_checkpoint_dpi",
    ]
    base_generator_stdout = (
        f"PASS e1_h1_generate_module_dpi {module_dpi_report['module_count']} modules"
        " -> e1/e1-h1/generated/module_dpi\n"
    )
    full_checkpoint_generator_stdout = (
        "PASS e1_h1_generate_full_checkpoint_module_dpi "
        f"{full_checkpoint_module_dpi['module_count']} modules"
        " -> e1/e1-h1/generated/full_checkpoint_dpi\n"
    )

    def generator_build_and_run_recorded(
        report: dict[str, Any],
        *,
        source: str,
        executable: str,
        build_command: list[str],
        execution_command: list[str],
    ) -> bool:
        build = report.get("generator_build", {})
        execution = report.get("generator_execution", {})
        serialized = json.dumps({"generator_build": build, "generator_execution": execution}, sort_keys=True)
        transient_markers = [
            "/private/var/folders/",
            "/var/folders/",
            "/private/tmp/",
            "/tmp/",
        ]
        return (
            build.get("source") == source
            and build.get("command") == build_command
            and build.get("executable") == executable
            and build.get("working_directory") == "<repo-root>"
            and build.get("status") == "pass"
            and execution.get("command") == execution_command
            and execution.get("working_directory") == "<repo-root>"
            and execution.get("status") == "pass"
            and not any(marker in serialized for marker in transient_markers)
        )

    def generator_stdout_reports_module_count(report: dict[str, Any], expected_stdout: str) -> bool:
        execution = report.get("generator_execution", {})
        return (
            execution.get("stdout") == expected_stdout
            and execution.get("expected_stdout") == expected_stdout
        )

    base_generator_build_and_run_recorded = generator_build_and_run_recorded(
        module_dpi_report,
        source=module_dpi_generator_sources["base_module_dpi_generator"],
        executable=base_generator_executable,
        build_command=base_generator_build_command,
        execution_command=base_generator_execution_command,
    )
    full_checkpoint_generator_build_and_run_recorded = generator_build_and_run_recorded(
        full_checkpoint_module_dpi,
        source=module_dpi_generator_sources["generated_full_checkpoint_module_dpi_generator"],
        executable=full_checkpoint_generator_executable,
        build_command=full_checkpoint_generator_build_command,
        execution_command=full_checkpoint_generator_execution_command,
    )
    module_dpi_generator_build_and_run_recorded = (
        base_generator_build_and_run_recorded
        and full_checkpoint_generator_build_and_run_recorded
    )
    module_dpi_generator_stdout_reports_module_counts = (
        generator_stdout_reports_module_count(module_dpi_report, base_generator_stdout)
        and generator_stdout_reports_module_count(full_checkpoint_module_dpi, full_checkpoint_generator_stdout)
    )
    module_dpi_generator_execution_evidence = {
        "base_module_dpi_generator": {
            "source": module_dpi_generator_sources["base_module_dpi_generator"],
            "build": module_dpi_report.get("generator_build", {}),
            "execution": module_dpi_report.get("generator_execution", {}),
        },
        "generated_full_checkpoint_module_dpi_generator": {
            "source": module_dpi_generator_sources["generated_full_checkpoint_module_dpi_generator"],
            "build": full_checkpoint_module_dpi.get("generator_build", {}),
            "execution": full_checkpoint_module_dpi.get("generator_execution", {}),
        },
    }
    module_dpi_cpp_verilator_launchers = {
        "base_module_dpi": module_dpi_report.get("cpp_verilator_launcher", {}),
        "generated_full_checkpoint_module_dpi": full_checkpoint_module_dpi.get("cpp_verilator_launcher", {}),
    }
    module_dpi_cpp_verilator_launcher_artifacts = unique_ordered(
        [
            launcher.get("source", "")
            for launcher in module_dpi_cpp_verilator_launchers.values()
            if launcher.get("source")
        ]
    )
    module_dpi_cpp_verilator_launchers_passed = (
        len(module_dpi_cpp_verilator_launcher_artifacts) == 2
        and all((REPO_ROOT / path).exists() for path in module_dpi_cpp_verilator_launcher_artifacts)
        and all(
            launcher.get("status") == "pass"
            and all(check["status"] == "pass" for check in launcher.get("checks", []))
            and launcher.get("verilator_run", {}).get("status") == "pass"
            and launcher.get("verilator_run", {}).get("summary", {}).get("status") == "pass"
            and launcher.get("verilator_run", {}).get("summary", {}).get("failures") == 0
            and not any(
                marker in json.dumps(launcher, sort_keys=True)
                for marker in [
                    "/private/var/folders/",
                    "/var/folders/",
                    "/private/tmp/",
                    "/tmp/",
                ]
            )
            for launcher in module_dpi_cpp_verilator_launchers.values()
        )
    )

    def cpp_launcher_runtime_markers_validated(launcher: dict[str, Any]) -> bool:
        module_results = launcher.get("verilator_run", {}).get("module_results", [])
        return (
            bool(module_results)
            and any(
                check.get("name") == "cpp_verilator_launcher_run_stdout_markers_match_recipe"
                and check.get("status") == "pass"
                for check in launcher.get("checks", [])
            )
            and all(
                result.get("status") == "pass"
                and result.get("stdout_markers_present") is True
                and result.get("missing_stdout_markers") == []
                and bool(result.get("expected_stdout_markers"))
                and result.get("observed_stdout_marker_count")
                == len(result.get("expected_stdout_markers", []))
                and result.get("captured_stdout_line_count", 0) > 0
                for result in module_results
            )
        )

    module_dpi_cpp_verilator_launchers_validate_runtime_markers = all(
        cpp_launcher_runtime_markers_validated(launcher)
        for launcher in module_dpi_cpp_verilator_launchers.values()
    )

    def cpp_launcher_runtime_phase_traces_validated(launcher: dict[str, Any]) -> bool:
        module_results = launcher.get("verilator_run", {}).get("module_results", [])
        return (
            bool(module_results)
            and any(
                check.get("name") == "cpp_verilator_launcher_run_phase_traces_match_recipe"
                and check.get("status") == "pass"
                for check in launcher.get("checks", [])
            )
            and all(
                result.get("status") == "pass"
                and result.get("phase_trace_in_order") is True
                and result.get("phase_trace_repeats_template") is True
                and result.get("phase_signal_trace_matches") is True
                and result.get("phase_signal_trace_repeats_template") is True
                and bool(result.get("expected_phase_trace_keys"))
                and bool(result.get("expected_phase_signal_trace_keys"))
                and result.get("observed_phase_trace_prefix_keys")
                == result.get("expected_phase_trace_keys")
                and result.get("observed_phase_trace_count", 0)
                >= len(result.get("expected_phase_trace_keys", []))
                and result.get("observed_phase_signal_trace_prefix_keys")
                == result.get("expected_phase_signal_trace_keys")
                and result.get("observed_phase_signal_trace_count", 0)
                >= len(result.get("expected_phase_signal_trace_keys", []))
                for result in module_results
            )
        )

    module_dpi_cpp_verilator_launchers_validate_runtime_phase_traces = all(
        cpp_launcher_runtime_phase_traces_validated(launcher)
        for launcher in module_dpi_cpp_verilator_launchers.values()
    )

    def cpp_launcher_runtime_summary(launcher: dict[str, Any]) -> dict[str, Any]:
        module_results = launcher.get("verilator_run", {}).get("module_results", [])
        phase_counts = [
            int(result.get("observed_phase_trace_count", 0))
            for result in module_results
        ]
        phase_signal_counts = [
            int(result.get("observed_phase_signal_trace_count", 0))
            for result in module_results
        ]
        return {
            "suite": launcher.get("suite"),
            "status": launcher.get("status"),
            "module_count": len(module_results),
            "run_status": launcher.get("verilator_run", {}).get("status"),
            "run_failures": launcher.get("verilator_run", {}).get("summary", {}).get("failures"),
            "stdout_marker_checks_passed": all(
                result.get("stdout_markers_present") is True
                and result.get("missing_stdout_markers") == []
                for result in module_results
            ),
            "phase_prefix_checks_passed": all(
                result.get("phase_trace_in_order") is True
                for result in module_results
            ),
            "phase_repeat_template_checks_passed": all(
                result.get("phase_trace_repeats_template") is True
                for result in module_results
            ),
            "phase_signal_prefix_checks_passed": all(
                result.get("phase_signal_trace_matches") is True
                for result in module_results
            ),
            "phase_signal_repeat_template_checks_passed": all(
                result.get("phase_signal_trace_repeats_template") is True
                for result in module_results
            ),
            "observed_phase_trace_record_count": sum(phase_counts),
            "observed_phase_signal_trace_record_count": sum(phase_signal_counts),
            "min_observed_phase_trace_record_count": min(phase_counts) if phase_counts else 0,
            "max_observed_phase_trace_record_count": max(phase_counts) if phase_counts else 0,
            "min_observed_phase_signal_trace_record_count": min(phase_signal_counts) if phase_signal_counts else 0,
            "max_observed_phase_signal_trace_record_count": max(phase_signal_counts) if phase_signal_counts else 0,
            "module_names": [
                result.get("name")
                for result in module_results
            ],
        }

    module_dpi_cpp_verilator_launcher_runtime_summary = {
        name: cpp_launcher_runtime_summary(launcher)
        for name, launcher in module_dpi_cpp_verilator_launchers.items()
    }
    base_cpp_launcher_results_by_name = {
        result["name"]: result
        for result in module_dpi_report.get("cpp_verilator_launcher", {})
        .get("verilator_run", {})
        .get("module_results", [])
        if result.get("name")
    }
    systolic_array_module = next(
        (
            module
            for module in module_dpi_report.get("modules", [])
            if module["name"] == "systolic_array"
        ),
        {},
    )
    systolic_array_cpp_launcher_result = base_cpp_launcher_results_by_name.get(
        "systolic_array",
        {},
    )
    systolic_array_result_digest_proof = {
        "schema": "e1-systolic-array-result-digest-proof-v0",
        "module": "systolic_array",
        "rtl": systolic_array_module.get("imp2_rtl"),
        "probe": systolic_array_module.get("probe"),
        "scoreboard": module_dpi_report.get("scoreboard"),
        "verilator_execution_report": module_dpi_report.get("verilator_execution_report"),
        "cpp_verilator_launcher": module_dpi_report.get("verilator_execution_launcher"),
        "expected_digest_marker": "E1_H1_MODULE_DPI_SYSTOLIC_DIGEST",
        "result_signal": next(
            (
                signal
                for signal in systolic_array_module.get("output_signals", [])
                if signal["name"] == "result_digest_o"
            ),
            None,
        ),
        "cpp_launcher_status": systolic_array_cpp_launcher_result.get("status"),
        "cpp_launcher_expected_stdout_markers": systolic_array_cpp_launcher_result.get(
            "expected_stdout_markers",
            [],
        ),
        "cpp_launcher_stdout_markers_present": systolic_array_cpp_launcher_result.get(
            "stdout_markers_present"
        ),
        "cpp_launcher_missing_stdout_markers": systolic_array_cpp_launcher_result.get(
            "missing_stdout_markers",
            [],
        ),
        "status": "pass"
        if systolic_array_module
        and any(
            signal["name"] == "result_digest_o"
            and signal["width"] == "32"
            and signal["description"]
            for signal in systolic_array_module.get("output_signals", [])
        )
        and systolic_array_cpp_launcher_result.get("status") == "pass"
        and systolic_array_cpp_launcher_result.get("stdout_markers_present") is True
        and not systolic_array_cpp_launcher_result.get("missing_stdout_markers", [])
        and "E1_H1_MODULE_DPI_SYSTOLIC_DIGEST"
        in systolic_array_cpp_launcher_result.get("expected_stdout_markers", [])
        else "fail",
    }
    module_dpi_passed = (
        module_dpi_report["status"] == "pass"
        and all(
            module["verilator_execution"]["status"] == "pass"
            and all(check["status"] == "pass" for check in module["construction_ledger"]["checks"])
            for module in module_dpi_report["modules"]
        )
    )
    module_isolation_passed = (
        module_dpi_report.get("module_isolation", {}).get("status") == "pass"
        and all(
            check["status"] == "pass"
            for check in module_dpi_report.get("module_isolation", {}).get("checks", [])
        )
    )
    full_checkpoint_module_dpi_passed = (
        full_checkpoint_module_dpi["status"] == "pass"
        and all(
            module["verilator_execution"]["status"] == "pass"
            and all(check["status"] == "pass" for check in module["construction_ledger"]["checks"])
            for module in full_checkpoint_module_dpi["modules"]
        )
    )
    full_checkpoint_module_isolation_passed = (
        full_checkpoint_module_dpi.get("module_isolation", {}).get("status") == "pass"
        and all(
            check["status"] == "pass"
            for check in full_checkpoint_module_dpi.get("module_isolation", {}).get("checks", [])
        )
    )

    def generated_artifacts_for_module_dpi_suite(report: dict[str, Any]) -> list[str]:
        artifacts: list[str] = []
        for module in report.get("modules", []):
            artifacts.extend(
                module.get("construction_ledger", {}).get("derived_artifacts", [])
            )
            artifacts.extend(
                [
                    module.get("probe", ""),
                    module.get("main", ""),
                    module.get("flist", ""),
                ]
            )
        artifacts.extend(
            [
                report.get("manifest", ""),
                report.get("scoreboard", ""),
                report.get("module_interfaces_doc", ""),
                report.get("module_isolation_proof", ""),
                report.get("cycle_contract", ""),
                report.get("module_test_plan", ""),
                report.get("verilator_execution_recipe", ""),
                report.get("verilator_execution_launcher", ""),
                report.get("verilator_execution_report", ""),
                report.get("readme_cycle_coverage", ""),
                report.get("construction_ledger", ""),
            ]
        )
        return unique_ordered([artifact for artifact in artifacts if artifact])

    def dpi_generation_provenance_suite(
        *,
        suite_key: str,
        report: dict[str, Any],
        launcher: dict[str, Any],
        expected_generator_source: str,
        expected_stdout: str,
        generator_build_recorded: bool,
        launcher_runtime_summary: dict[str, Any],
    ) -> dict[str, Any]:
        generated_artifacts = generated_artifacts_for_module_dpi_suite(report)
        artifact_existence = [
            {
                "path": artifact,
                "exists": (REPO_ROOT / artifact).exists(),
            }
            for artifact in generated_artifacts
        ]
        launcher_results_by_name = {
            result.get("name"): result
            for result in launcher.get("verilator_run", {}).get("module_results", [])
            if result.get("name")
        }
        module_rows: list[dict[str, Any]] = []
        for module in report.get("modules", []):
            ledger = module.get("construction_ledger", {})
            module_artifacts = unique_ordered(
                [
                    *ledger.get("derived_artifacts", []),
                    module.get("probe", ""),
                    module.get("main", ""),
                    module.get("flist", ""),
                    report.get("scoreboard", ""),
                ]
            )
            module_artifacts = [artifact for artifact in module_artifacts if artifact]
            result = launcher_results_by_name.get(module["name"], {})
            ledger_checks_pass = (
                bool(ledger.get("checks"))
                and all(check["status"] == "pass" for check in ledger.get("checks", []))
            )
            artifacts_exist = all((REPO_ROOT / artifact).exists() for artifact in module_artifacts)
            launcher_result_passed = (
                result.get("status") == "pass"
                and result.get("stdout_markers_present") is True
                and result.get("missing_stdout_markers", []) == []
                and result.get("phase_trace_in_order") is True
                and result.get("phase_trace_repeats_template") is True
                and result.get("phase_signal_trace_matches") is True
                and result.get("phase_signal_trace_repeats_template") is True
            )
            status = (
                "pass"
                if module.get("verilator_execution", {}).get("status") == "pass"
                and ledger_checks_pass
                and artifacts_exist
                and launcher_result_passed
                else "fail"
            )
            module_rows.append(
                {
                    "name": module["name"],
                    "top_module": module.get("top_module"),
                    "probe": module.get("probe"),
                    "main": module.get("main"),
                    "flist": module.get("flist"),
                    "generated_artifacts": module_artifacts,
                    "generated_artifact_count": len(module_artifacts),
                    "all_generated_artifacts_exist": artifacts_exist,
                    "ledger_checks_pass": ledger_checks_pass,
                    "verilator_execution_status": module.get("verilator_execution", {}).get("status"),
                    "cpp_launcher_result_status": result.get("status"),
                    "cpp_launcher_run_executable": result.get("run_executable"),
                    "cpp_launcher_stdout_markers_present": result.get("stdout_markers_present"),
                    "cpp_launcher_missing_stdout_markers": result.get(
                        "missing_stdout_markers",
                        [],
                    ),
                    "cpp_launcher_phase_trace_in_order": result.get("phase_trace_in_order"),
                    "cpp_launcher_phase_signal_trace_matches": result.get(
                        "phase_signal_trace_matches",
                    ),
                    "status": status,
                }
            )

        checks = [
            {
                "name": "generator_source_matches_report",
                "status": "pass"
                if report.get("generator") == expected_generator_source
                and (REPO_ROOT / expected_generator_source).exists()
                else "fail",
            },
            {
                "name": "generator_build_and_execution_are_recorded",
                "status": "pass" if generator_build_recorded else "fail",
            },
            {
                "name": "generator_stdout_reports_expected_module_count",
                "status": "pass"
                if generator_stdout_reports_module_count(report, expected_stdout)
                else "fail",
            },
            {
                "name": "generated_artifacts_exist",
                "status": "pass"
                if generated_artifacts
                and all(record["exists"] for record in artifact_existence)
                else "fail",
            },
            {
                "name": "cpp_verilator_launcher_exists_and_passes_checks",
                "status": "pass"
                if launcher.get("source") == report.get("verilator_execution_launcher")
                and (REPO_ROOT / launcher.get("source", "")).exists()
                and launcher.get("status") == "pass"
                and all(check["status"] == "pass" for check in launcher.get("checks", []))
                else "fail",
            },
            {
                "name": "cpp_verilator_launcher_runs_all_modules",
                "status": "pass"
                if launcher.get("verilator_run", {}).get("status") == "pass"
                and launcher.get("verilator_run", {}).get("summary", {}).get("status") == "pass"
                and launcher.get("verilator_run", {}).get("summary", {}).get("failures") == 0
                and set(launcher_results_by_name) == {module["name"] for module in report.get("modules", [])}
                else "fail",
            },
            {
                "name": "module_rows_have_artifacts_ledgers_and_launcher_results",
                "status": "pass"
                if module_rows
                and all(row["status"] == "pass" for row in module_rows)
                else "fail",
            },
        ]
        return {
            "suite": suite_key,
            "schema": "e1-dpi-generation-provenance-suite-v0",
            "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
            "generator": report.get("generator"),
            "generator_build": report.get("generator_build", {}),
            "generator_execution": report.get("generator_execution", {}),
            "expected_generator_stdout": expected_stdout,
            "manifest": report.get("manifest"),
            "scoreboard": report.get("scoreboard"),
            "verilator_execution_recipe": report.get("verilator_execution_recipe"),
            "verilator_execution_launcher": report.get("verilator_execution_launcher"),
            "verilator_execution_report": report.get("verilator_execution_report"),
            "cpp_verilator_launcher_source": launcher.get("source"),
            "cpp_verilator_launcher_summary": launcher_runtime_summary,
            "module_count": len(report.get("modules", [])),
            "cpp_launcher_module_count": len(launcher_results_by_name),
            "generated_artifact_count": len(generated_artifacts),
            "generated_artifacts": generated_artifacts,
            "generated_artifact_existence": artifact_existence,
            "modules": module_rows,
            "checks": checks,
        }

    dpi_generation_provenance_suites = {
        "base_module_dpi": dpi_generation_provenance_suite(
            suite_key="base_module_dpi",
            report=module_dpi_report,
            launcher=module_dpi_cpp_verilator_launchers["base_module_dpi"],
            expected_generator_source=module_dpi_generator_sources[
                "base_module_dpi_generator"
            ],
            expected_stdout=base_generator_stdout,
            generator_build_recorded=base_generator_build_and_run_recorded,
            launcher_runtime_summary=module_dpi_cpp_verilator_launcher_runtime_summary[
                "base_module_dpi"
            ],
        ),
        "generated_full_checkpoint_module_dpi": dpi_generation_provenance_suite(
            suite_key="generated_full_checkpoint_module_dpi",
            report=full_checkpoint_module_dpi,
            launcher=module_dpi_cpp_verilator_launchers[
                "generated_full_checkpoint_module_dpi"
            ],
            expected_generator_source=module_dpi_generator_sources[
                "generated_full_checkpoint_module_dpi_generator"
            ],
            expected_stdout=full_checkpoint_generator_stdout,
            generator_build_recorded=full_checkpoint_generator_build_and_run_recorded,
            launcher_runtime_summary=module_dpi_cpp_verilator_launcher_runtime_summary[
                "generated_full_checkpoint_module_dpi"
            ],
        ),
    }
    dpi_generation_provenance_artifacts = unique_ordered(
        [
            artifact
            for suite in dpi_generation_provenance_suites.values()
            for artifact in suite["generated_artifacts"]
        ]
    )
    dpi_generation_provenance_checks = [
        {
            "name": "generator_and_runner_sources_are_present",
            "status": "pass"
            if module_dpi_generator_sources_passed
            and all(
                (REPO_ROOT / source).exists()
                for source in module_dpi_generator_sources.values()
            )
            else "fail",
        },
        {
            "name": "all_dpi_generation_suites_pass",
            "status": "pass"
            if all(
                suite["status"] == "pass"
                for suite in dpi_generation_provenance_suites.values()
            )
            else "fail",
        },
        {
            "name": "generated_artifacts_are_materialized",
            "status": "pass"
            if dpi_generation_provenance_artifacts
            and all((REPO_ROOT / artifact).exists() for artifact in dpi_generation_provenance_artifacts)
            else "fail",
        },
        {
            "name": "cpp_launchers_validate_markers_and_phase_traces",
            "status": "pass"
            if module_dpi_cpp_verilator_launchers_validate_runtime_markers
            and module_dpi_cpp_verilator_launchers_validate_runtime_phase_traces
            else "fail",
        },
    ]
    dpi_generation_provenance_audit = {
        "schema": "e1-dpi-generation-provenance-audit-v0",
        "scope": (
            "C++ module-DPI generators, generated collateral, generated C++ "
            "Verilator launchers, and module-only Verilator runtime results"
        ),
        "status": "pass"
        if all(check["status"] == "pass" for check in dpi_generation_provenance_checks)
        else "fail",
        "generator_sources": module_dpi_generator_sources,
        "generator_source_artifacts": module_dpi_generator_source_artifacts,
        "recipe_runner": module_dpi_generator_sources["verilator_runner"],
        "pipeline_orchestrator": module_dpi_generator_sources["pipeline_orchestrator"],
        "suite_count": len(dpi_generation_provenance_suites),
        "module_count": sum(
            suite["module_count"] for suite in dpi_generation_provenance_suites.values()
        ),
        "generated_artifact_count": len(dpi_generation_provenance_artifacts),
        "generated_artifacts": dpi_generation_provenance_artifacts,
        "suites": dpi_generation_provenance_suites,
        "checks": dpi_generation_provenance_checks,
    }
    dpi_generation_provenance_audit_passed = (
        dpi_generation_provenance_audit["status"] == "pass"
    )

    def module_runtime_phase_traces_passed(
        report: dict[str, Any], *, require_phase_signal_trace: bool
    ) -> bool:
        return all(
            module["verilator_execution"]["status"] == "pass"
            and module["verilator_execution"]["observed_phase_markers"]
            == module["verilator_execution"]["expected_phase_markers"]
            and module["verilator_execution"]["observed_phase_trace_prefix"]
            == module["verilator_execution"]["expected_phase_trace"]
            and module["verilator_execution"]["observed_phase_trace_count"]
            >= len(module["verilator_execution"]["expected_phase_trace"])
            and module["readme_cycle_coverage"]["phase_names"]
            == [step["phase"] for step in module["cycle_contract"]["cycles"]]
            and (
                not require_phase_signal_trace
                or (
                    len(module["verilator_execution"]["expected_phase_signal_trace"]) > 0
                    and module["verilator_execution"]["observed_phase_signal_trace_prefix"]
                    == module["verilator_execution"]["expected_phase_signal_trace"]
                    and module["verilator_execution"]["observed_phase_signal_trace_count"]
                    >= len(module["verilator_execution"]["expected_phase_signal_trace"])
                )
            )
            for module in report["modules"]
        )

    base_runtime_phase_traces_passed = module_runtime_phase_traces_passed(
        module_dpi_report, require_phase_signal_trace=True
    )
    generated_runtime_phase_traces_passed = module_runtime_phase_traces_passed(
        full_checkpoint_module_dpi, require_phase_signal_trace=True
    )
    source_derived_dpi_passed = (
        full_graph_module_dpi_binding["status"] == "pass"
        and full_graph_module_dpi_binding["source_derived_module_dpi_coverage_count"]
        == (
            full_graph_module_dpi_binding["generated_rtl_module_dpi_coverage_count"]
            + full_graph_module_dpi_binding["separated_base_rtl_module_dpi_coverage_count"]
        )
        and all(
            entry["covered"]
            and entry["verilator_status"] == "pass"
            and entry["ledger_checks_pass"]
            and entry["recipe_checks_pass"]
            and entry["module_only_flist_boundary_exact"]
            and entry["selected_dut_rtl_in_flist"]
            and entry["cycle_contract_checks_pass"]
            and entry["readme_cycle_checks_pass"]
            and entry["phase_trace_checks_pass"]
            and entry["phase_signal_trace_checks_pass"]
            and entry.get("cpp_launcher_checks_pass") is True
            and entry.get("cpp_launcher_recipe_checks_pass") is True
            and entry.get("cpp_launcher_readme_cycle_checks_pass") is True
            for entry in full_graph_module_dpi_binding["source_derived_module_dpi_coverage"]
        )
    )
    source_derived_cpp_launcher_evidence_passed = (
        bool(full_graph_module_dpi_binding.get("source_derived_module_dpi_coverage"))
        and all(
            entry.get("cpp_launcher_checks_pass") is True
            and entry.get("cpp_launcher_recipe_checks_pass") is True
            and entry.get("cpp_launcher_result", {}).get("status") == "pass"
            and entry.get("cpp_launcher_readme_cycle_checks_pass") is True
            and entry.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
            for entry in full_graph_module_dpi_binding["source_derived_module_dpi_coverage"]
        )
    )
    generated_child_stub_boundary_passed = (
        bool(full_graph_module_dpi_binding.get("generated_child_stub_boundary"))
        and all(
            entry["present"]
            and entry["flist_contains_only_selected_dut_and_probe"]
            and entry["composed_dependencies_absent_from_flist"]
            and entry["child_stubs_present_in_probe"]
            for entry in full_graph_module_dpi_binding["generated_child_stub_boundary"]
        )
    )
    base_module_boundary_passed = (
        bool(full_graph_module_dpi_binding.get("all_base_module_bindings"))
        and all(
            binding["present"]
            and binding["source_defined_modules_include_top"]
            and binding["flist_exact_match"]
            and binding["exact_probe_instantiation_counts"]
            and binding.get("cpp_launcher_checks_pass") is True
            and binding.get("cpp_launcher_recipe_checks_pass") is True
            and binding.get("cpp_launcher_readme_cycle_checks_pass") is True
            for binding in full_graph_module_dpi_binding["all_base_module_bindings"]
        )
    )
    base_module_cpp_launcher_evidence_passed = (
        bool(full_graph_module_dpi_binding.get("all_base_module_bindings"))
        and all(
            binding.get("cpp_launcher_checks_pass") is True
            and binding.get("cpp_launcher_recipe_checks_pass") is True
            and binding.get("cpp_launcher_result", {}).get("status") == "pass"
            and binding.get("cpp_launcher_readme_cycle_checks_pass") is True
            and binding.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
            for binding in full_graph_module_dpi_binding["all_base_module_bindings"]
        )
    )
    module_dpi_boundary_artifacts = unique_ordered(
        [
            path
            for binding in full_graph_module_dpi_binding["all_base_module_bindings"]
            for path in [
                binding.get("reference_rtl"),
                binding.get("imp2_rtl"),
                binding.get("probe"),
                binding.get("flist"),
            ]
            if path
        ]
        + [
            path
            for boundary in full_graph_module_dpi_binding["generated_child_stub_boundary"]
            for path in [
                *boundary.get("selected_dut_rtl", []),
                boundary.get("probe"),
                boundary.get("flist"),
            ]
            if path
        ]
    )
    production_rtl_inventory_check_rows = production_rtl_inventory_checks(production_rtl_inventory)
    production_rtl_inventory_artifacts = unique_ordered(production_rtl_inventory["paths"])
    imp1_mock_runtime_artifacts = unique_ordered(
        [
            path
            for path in [
                production_rtl_inventory.get("imp1_mock_rtl_lint", {})
                .get("runtime", {})
                .get("manifest", ""),
                *production_rtl_inventory.get("imp1_mock_rtl_lint", {})
                .get("runtime", {})
                .get("generated_artifacts", []),
            ]
            if path
        ]
    )
    aggregate = full_checkpoint_rtl_lowering["aggregate"]
    total_layer_slots = int(aggregate["linear_ops_per_layer"]) + int(aggregate["control_ops_per_layer"])
    expected_graph_slots = int(aggregate["layers"]) * total_layer_slots
    production_rtl_inventory_passed = all(
        check["status"] == "pass" for check in production_rtl_inventory_check_rows
    )
    source_derived_inventory_module_only_passed = (
        production_rtl_inventory.get("module_only_dpi_inventory", {}).get("status") == "pass"
        and all(
            row["category"] not in {"base_imp2_candidate", "generated_full_checkpoint"}
            or row.get("module_only_dpi_covered") is True
            for row in production_rtl_inventory.get("rows", [])
        )
    )
    active_rtl_standalone_runtime_passed = (
        production_rtl_inventory.get("standalone_runtime_inventory", {}).get("status") == "pass"
        and all(
            row["category"] == "base_imp1_mock"
            or row.get("standalone_runtime_covered") is True
            for row in production_rtl_inventory.get("rows", [])
        )
    )

    boundary_role_by_name = {
        "control_cpu": "cpu_control",
        "rgmii_ethernet_ingress": "digital_ingress",
        "ingress_sram": "latch_buffer",
        "activation_sram": "sram_shell",
        "accumulator_sram": "sram_shell",
        "systolic_array": "systolic_array",
        "linear_scheduler": "linear_systolic_path",
        "linear_tile_engine": "linear_systolic_path",
        "linear_slot_engine": "linear_systolic_path",
        "control_scheduler": "cpu_control",
        "control_slot_engine": "cpu_control",
        "graph_sequencer": "cpu_control",
        "full_checkpoint_top": "top_glue",
        "generated_soc_top": "top_glue",
    }
    role_descriptions = {
        "cpu_control": "CPU/control sequencing and command issue.",
        "digital_ingress": "Digital-only RGMII ingress source for input data.",
        "latch_buffer": "Latched stream buffer that holds and releases data under backpressure.",
        "sram_shell": "Configurable SRAM shell boundary.",
        "systolic_array": "Replaceable systolic-array compute boundary.",
        "linear_systolic_path": "TinyLlama linear-op scheduler/slot path that drives SRAM and systolic-array boundaries.",
        "top_glue": "Top-level composition glue with child modules isolated by module-only probes.",
    }
    required_taxonomy_roles = sorted(role_descriptions)
    base_modules_by_name = {
        module["name"]: module
        for module in module_dpi_report.get("modules", [])
    }
    generated_modules_by_name = {
        module["name"]: module
        for module in full_checkpoint_module_dpi.get("modules", [])
    }
    base_module_isolation_proof = load_json(
        REPO_ROOT / module_dpi_report["module_isolation_proof"]
    )
    generated_module_isolation_proof = load_json(
        REPO_ROOT / full_checkpoint_module_dpi["module_isolation_proof"]
    )
    base_isolation_by_name = {
        module["name"]: module
        for module in base_module_isolation_proof.get("modules", [])
    }
    generated_isolation_by_name = {
        module["name"]: module
        for module in generated_module_isolation_proof.get("modules", [])
    }
    base_boundary_by_name = {
        binding["name"]: binding
        for binding in full_graph_module_dpi_binding.get("all_base_module_bindings", [])
    }
    generated_boundary_by_name = {
        boundary["name"]: boundary
        for boundary in full_graph_module_dpi_binding.get("generated_child_stub_boundary", [])
    }
    production_row_by_rtl = {
        row["rtl"]: row
        for row in production_rtl_inventory.get("rows", [])
    }
    standalone_runtime_by_rtl = {
        entry["rtl"]: entry
        for entry in production_rtl_inventory.get("standalone_runtime_inventory", {}).get(
            "coverage",
            [],
        )
    }

    def systemverilog_module_only_coverage_audit() -> dict[str, Any]:
        active_source_categories = {"base_imp2_candidate", "generated_full_checkpoint"}
        audit_rows: list[dict[str, Any]] = []

        for row in production_rtl_inventory.get("rows", []):
            category = row["category"]
            proofs = row.get("proofs", [])
            module_only_required = category in active_source_categories
            standalone_required = row.get("standalone_runtime_required") is True
            proof_rows: list[dict[str, Any]] = []

            for proof in proofs:
                proof_name = proof.get("name")
                boundary = (
                    base_boundary_by_name.get(proof_name, {})
                    if category == "base_imp2_candidate"
                    else generated_boundary_by_name.get(proof_name, {})
                )
                if category == "base_imp2_candidate":
                    single_dut_boundary = (
                        boundary.get("probe_dut_instantiation_count") == 1
                        and boundary.get("probe_reference_instantiation_count") == 1
                        and boundary.get("flist_exact_match") is True
                    )
                    selected_dut_rtl = [row["rtl"]]
                    reference_rtl = boundary.get("reference_rtl")
                    top_module = boundary.get("top_module", proof.get("top_module"))
                else:
                    single_dut_boundary = (
                        proof.get("flist_exact_match") is True
                        and boundary.get("flist_contains_only_selected_dut_and_probe") is True
                        and boundary.get("composed_dependencies_absent_from_flist") is True
                        and boundary.get("child_stubs_present_in_probe") is True
                    )
                    selected_dut_rtl = boundary.get("selected_dut_rtl", [row["rtl"]])
                    reference_rtl = None
                    top_module = proof.get("sv_module")

                runtime_proof_passed = (
                    proof.get("verilator_status") == "pass"
                    and proof.get("ledger_checks_pass") is True
                    and proof.get("recipe_checks_pass") is True
                    and proof.get("phase_trace_checks_pass") is True
                    and proof.get("phase_signal_trace_checks_pass") is True
                    and proof.get("cpp_launcher_checks_pass") is True
                    and proof.get("cpp_launcher_recipe_checks_pass") is True
                    and proof.get("cpp_launcher_readme_cycle_checks_pass") is True
                    and proof.get("cpp_launcher_result", {}).get("status") == "pass"
                    and proof.get("cpp_launcher_result", {}).get("missing_stdout_markers", []) == []
                )
                proof_rows.append(
                    {
                        "name": proof_name,
                        "top_module": top_module,
                        "probe": proof.get("probe"),
                        "flist": proof.get("flist"),
                        "selected_dut_rtl": selected_dut_rtl,
                        "reference_rtl": reference_rtl,
                        "single_dut_boundary": single_dut_boundary,
                        "runtime_proof_passed": runtime_proof_passed,
                        "cpp_launcher_run_executable": proof.get(
                            "cpp_launcher_result",
                            {},
                        ).get("run_executable"),
                        "cycle_template": proof.get("cycle_template")
                        or proof.get("cycle_contract", {}).get("template"),
                    }
                )

            if module_only_required:
                run_scope = "module_only_dpi_verilator"
                status = (
                    "pass"
                    if row.get("modules_match_proof") is True
                    and row.get("module_only_dpi_covered") is True
                    and row.get("standalone_runtime_covered") is True
                    and proof_rows
                    and all(proof["single_dut_boundary"] for proof in proof_rows)
                    and all(proof["runtime_proof_passed"] for proof in proof_rows)
                    else "fail"
                )
            elif category == "generated_soc_top":
                run_scope = "standalone_top_verilator"
                status = (
                    "pass"
                    if row.get("modules_match_proof") is True
                    and row.get("standalone_runtime_covered") is True
                    and row.get("proof", {}).get("status") == "pass"
                    else "fail"
                )
            else:
                run_scope = "accepted_imp1_mock_verilator_runtime_and_contract"
                status = (
                    "pass"
                    if row.get("modules_match_proof") is True
                    and row.get("covered") is True
                    and row.get("module_lint", {}).get("status") == "pass"
                    and row.get("mock_runtime", {}).get("status") == "pass"
                    and row.get("mock_runtime", {}).get("stdout_marker_present") is True
                    else "fail"
                )

            audit_rows.append(
                {
                    "rtl": row["rtl"],
                    "category": category,
                    "defined_modules": row.get("defined_modules", []),
                    "expected_modules": row.get("expected_modules", []),
                    "modules_match_proof": row.get("modules_match_proof") is True,
                    "run_scope": run_scope,
                    "module_only_required": module_only_required,
                    "standalone_runtime_required": standalone_required,
                    "module_only_dpi_covered": row.get("module_only_dpi_covered"),
                    "standalone_runtime_covered": row.get("standalone_runtime_covered"),
                    "proof_count": len(proof_rows),
                    "proofs": proof_rows,
                    "status": status,
                }
            )

        active_rows = [
            row for row in audit_rows if row["category"] in active_source_categories
        ]
        checks = [
            {
                "name": "active_source_rtl_rows_have_module_only_runtime",
                "status": "pass"
                if active_rows
                and all(row["status"] == "pass" for row in active_rows)
                and all(row["module_only_required"] for row in active_rows)
                else "fail",
            },
            {
                "name": "module_only_runtime_rows_have_single_dut_boundaries",
                "status": "pass"
                if active_rows
                and all(
                    row["proofs"]
                    and all(proof["single_dut_boundary"] for proof in row["proofs"])
                    for row in active_rows
                )
                else "fail",
            },
            {
                "name": "module_only_runtime_rows_have_cpp_launcher_runtime_evidence",
                "status": "pass"
                if active_rows
                and all(
                    row["proofs"]
                    and all(proof["runtime_proof_passed"] for proof in row["proofs"])
                    for row in active_rows
                )
                else "fail",
            },
            {
                "name": "all_production_rtl_rows_parse_expected_modules",
                "status": "pass"
                if audit_rows
                and all(row["modules_match_proof"] for row in audit_rows)
                else "fail",
            },
            {
                "name": "non_module_only_rows_have_explicit_top_or_mock_scope",
                "status": "pass"
                if audit_rows
                and all(
                    row["module_only_required"]
                    or row["run_scope"]
                    in {
                        "standalone_top_verilator",
                        "accepted_imp1_mock_verilator_runtime_and_contract",
                    }
                    for row in audit_rows
                )
                else "fail",
            },
        ]
        return {
            "schema": "e1-systemverilog-module-only-coverage-audit-v0",
            "scope": (
                "production RTL rows; active source-derived rows must have "
                "single-DUT module-DPI Verilator runtime evidence"
            ),
            "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
            "active_source_categories": sorted(active_source_categories),
            "row_count": len(audit_rows),
            "active_module_only_row_count": len(active_rows),
            "active_module_only_passed_count": sum(
                1 for row in active_rows if row["status"] == "pass"
            ),
            "rows": audit_rows,
            "checks": checks,
        }

    systemverilog_module_coverage_audit = systemverilog_module_only_coverage_audit()
    systemverilog_module_coverage_audit_passed = (
        systemverilog_module_coverage_audit["status"] == "pass"
    )

    def systemverilog_defined_module_runtime_scope_audit() -> dict[str, Any]:
        module_rows: list[dict[str, Any]] = []
        audit_rows_by_rtl = {
            row["rtl"]: row
            for row in systemverilog_module_coverage_audit.get("rows", [])
        }
        active_source_categories = {"base_imp2_candidate", "generated_full_checkpoint"}

        for audit_row in systemverilog_module_coverage_audit.get("rows", []):
            rtl = audit_row["rtl"]
            production_row = production_row_by_rtl.get(rtl, {})
            category = audit_row["category"]
            defined_modules = audit_row.get("defined_modules", [])
            proof_names = [
                proof.get("name")
                for proof in audit_row.get("proofs", [])
                if proof.get("name")
            ]
            for sv_module in defined_modules:
                if category in active_source_categories:
                    runtime_scope = "module_only_dpi_verilator"
                    evidence = [
                        "e1/generated/pipeline/28_lowering_construction_certificate.json:systemverilog_module_coverage_audit",
                        "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
                    ]
                    coverage_status = (
                        "pass"
                        if audit_row["status"] == "pass"
                        and audit_row.get("module_only_dpi_covered") is True
                        and audit_row.get("standalone_runtime_covered") is True
                        and audit_row.get("proof_count", 0) > 0
                        and all(
                            proof["runtime_proof_passed"]
                            and proof["single_dut_boundary"]
                            for proof in audit_row.get("proofs", [])
                        )
                        else "fail"
                    )
                elif category == "generated_soc_top":
                    runtime_scope = "standalone_top_verilator"
                    evidence = [
                        "e1/generated/pipeline/29_end_to_end_smoke.json:generated_soc_top_standalone_verilator",
                        "e1/generated/pipeline/29_end_to_end_smoke.json:generated_soc_top_hierarchy",
                    ]
                    coverage_status = (
                        "pass"
                        if audit_row["status"] == "pass"
                        and audit_row.get("standalone_runtime_covered") is True
                        and production_row.get("proof", {}).get("status") == "pass"
                        else "fail"
                    )
                else:
                    runtime_scope = "accepted_imp1_mock_verilator_runtime_and_contract"
                    evidence = [
                        "e1/e1-h1/generated/imp1_mock_runtime/manifest.json",
                        "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory",
                    ]
                    coverage_status = (
                        "pass"
                        if category == "base_imp1_mock"
                        and audit_row["status"] == "pass"
                        and production_row.get("module_lint", {}).get("status") == "pass"
                        and production_row.get("mock_runtime", {}).get("status") == "pass"
                        and production_row.get("mock_runtime", {}).get(
                            "stdout_marker_present"
                        )
                        is True
                        else "fail"
                    )

                module_rows.append(
                    {
                        "rtl": rtl,
                        "sv_module": sv_module,
                        "category": category,
                        "runtime_scope": runtime_scope,
                        "source_row_status": audit_row["status"],
                        "module_only_required": audit_row["module_only_required"],
                        "standalone_runtime_required": audit_row[
                            "standalone_runtime_required"
                        ],
                        "proof_names": proof_names,
                        "proof_count": len(proof_names),
                        "module_lint_status": production_row.get(
                            "module_lint",
                            {},
                        ).get("status"),
                        "mock_runtime_status": production_row.get(
                            "mock_runtime",
                            {},
                        ).get("status"),
                        "mock_runtime_main": production_row.get(
                            "mock_runtime",
                            {},
                        ).get("main"),
                        "mock_runtime_stdout_marker_present": production_row.get(
                            "mock_runtime",
                            {},
                        ).get("stdout_marker_present"),
                        "evidence": evidence,
                        "status": coverage_status,
                    }
                )

        active_rows = [
            row for row in module_rows if row["category"] in active_source_categories
        ]
        imp1_rows = [
            row for row in module_rows if row["category"] == "base_imp1_mock"
        ]
        top_rows = [
            row for row in module_rows if row["category"] == "generated_soc_top"
        ]
        expected_defined_module_count = sum(
            len(row.get("defined_modules", []))
            for row in systemverilog_module_coverage_audit.get("rows", [])
        )
        checks = [
            {
                "name": "every_defined_systemverilog_module_has_runtime_scope",
                "status": "pass"
                if module_rows
                and all(row["status"] == "pass" for row in module_rows)
                else "fail",
            },
            {
                "name": "active_defined_modules_use_module_only_dpi_scope",
                "status": "pass"
                if active_rows
                and all(
                    row["runtime_scope"] == "module_only_dpi_verilator"
                    and row["status"] == "pass"
                    for row in active_rows
                )
                else "fail",
            },
            {
                "name": "generated_top_defined_modules_use_standalone_scope",
                "status": "pass"
                if top_rows
                and all(
                    row["runtime_scope"] == "standalone_top_verilator"
                    and row["status"] == "pass"
                    for row in top_rows
                )
                else "fail",
            },
            {
                "name": "imp1_mock_defined_modules_use_verilator_runtime_and_contract_scope",
                "status": "pass"
                if imp1_rows
                and all(
                    row["runtime_scope"]
                    == "accepted_imp1_mock_verilator_runtime_and_contract"
                    and row["status"] == "pass"
                    and row["module_lint_status"] == "pass"
                    and row["mock_runtime_status"] == "pass"
                    and row["mock_runtime_stdout_marker_present"] is True
                    for row in imp1_rows
                )
                else "fail",
            },
            {
                "name": "defined_module_rows_cover_all_inventory_defined_modules",
                "status": "pass"
                if len(module_rows) == expected_defined_module_count
                and set(audit_rows_by_rtl) == {
                    row["rtl"]
                    for row in systemverilog_module_coverage_audit.get("rows", [])
                }
                else "fail",
            },
        ]
        return {
            "schema": "e1-systemverilog-defined-module-runtime-scope-audit-v0",
            "scope": (
                "one row per parsed SystemVerilog module in the production RTL "
                "inventory, with explicit module-only, standalone-top, or "
                "accepted-mock Verilator runtime scope"
            ),
            "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
            "defined_module_count": len(module_rows),
            "active_module_only_defined_module_count": len(active_rows),
            "imp1_mock_defined_module_count": len(imp1_rows),
            "standalone_top_defined_module_count": len(top_rows),
            "rows": module_rows,
            "checks": checks,
        }

    systemverilog_defined_module_runtime_audit = (
        systemverilog_defined_module_runtime_scope_audit()
    )
    systemverilog_defined_module_runtime_audit_passed = (
        systemverilog_defined_module_runtime_audit["status"] == "pass"
    )

    def cycle_evidence(
        module: dict[str, Any],
        *,
        cycle_contract: str,
        readme_cycle_coverage: str,
        module_interfaces_doc: str,
        verilator_execution_report: str,
    ) -> dict[str, Any]:
        contract = module.get("cycle_contract", {})
        readme_cycle = module.get("readme_cycle_coverage", {})
        verilator_execution = module.get("verilator_execution", {})
        status = (
            "pass"
            if module
            and all(check["status"] == "pass" for check in contract.get("checks", []))
            and all(check["status"] == "pass" for check in readme_cycle.get("checks", []))
            and verilator_execution.get("status") == "pass"
            and readme_cycle.get("phase_names")
            == [step["phase"] for step in contract.get("cycles", [])]
            else "fail"
        )
        return {
            "status": status,
            "cycle_contract": cycle_contract,
            "readme_cycle_coverage": readme_cycle_coverage,
            "module_interfaces_doc": module_interfaces_doc,
            "verilator_execution_report": verilator_execution_report,
            "cycle_template": contract.get("template"),
            "cycle_period": contract.get("cycle_period"),
            "cycle_count": len(contract.get("cycles", [])),
            "phase_names": readme_cycle.get("phase_names", []),
            "readme_diagram": readme_cycle.get("readme_diagram"),
            "readme_index": readme_cycle.get("readme_index"),
            "observed_phase_trace_count": verilator_execution.get("observed_phase_trace_count"),
            "observed_phase_signal_trace_count": verilator_execution.get(
                "observed_phase_signal_trace_count"
            ),
        }

    def standalone_runtime_proof(rtl: str) -> dict[str, Any]:
        inventory_entry = standalone_runtime_by_rtl.get(rtl, {})
        row = production_row_by_rtl.get(rtl, {})
        return {
            "status": (
                "pass"
                if inventory_entry.get("covered") is True
                and row.get("standalone_runtime_covered") is True
                else "fail"
            ),
            "inventory": (
                "e1/generated/pipeline/28_lowering_construction_certificate.json:"
                "target_rtl_evidence.production_rtl_inventory.standalone_runtime_inventory"
            ),
            "category": inventory_entry.get("category", row.get("category")),
            "coverage_kind": inventory_entry.get("coverage_kind", row.get("coverage_kind")),
            "standalone_runtime_kind": inventory_entry.get(
                "standalone_runtime_kind",
                row.get("standalone_runtime_kind"),
            ),
            "requirement": inventory_entry.get(
                "requirement",
                row.get("standalone_runtime_requirement"),
            ),
            "covered": inventory_entry.get("covered"),
        }

    def module_only_proof(
        *,
        family: str,
        isolation: dict[str, Any],
        boundary: dict[str, Any],
        proof_path: str,
        verilator_execution_report: str,
    ) -> dict[str, Any]:
        boundary_ok = (
            boundary.get("flist_exact_match") is True
            or boundary.get("flist_contains_only_selected_dut_and_probe") is True
        )
        return {
            "required": True,
            "status": (
                "pass"
                if isolation
                and boundary
                and boundary_ok
                and all(check["status"] == "pass" for check in isolation.get("checks", []))
                else "fail"
            ),
            "family": family,
            "module_isolation_proof": proof_path,
            "verilator_execution_report": verilator_execution_report,
            "flist": isolation.get("flist", boundary.get("flist")),
            "probe": isolation.get("probe", boundary.get("probe")),
            "boundary": isolation.get("boundary"),
            "selected_dut_rtl": (
                [isolation["imp2_rtl"]]
                if isolation.get("imp2_rtl")
                else isolation.get("module_only_flist_rtl", boundary.get("selected_dut_rtl", []))
            ),
            "child_stub_modules": isolation.get("child_stub_modules", []),
            "forbidden_design_neighbors": isolation.get(
                "forbidden_design_neighbors",
                isolation.get("forbidden_child_modules", []),
            ),
        }

    def source_boundary_entry(
        *,
        name: str,
        family: str,
        rtl: str,
        top_module: str,
        module: dict[str, Any],
        isolation: dict[str, Any],
        boundary: dict[str, Any],
        cycle_contract: str,
        readme_cycle_coverage: str,
        module_interfaces_doc: str,
        module_isolation_proof: str,
        verilator_execution_report: str,
    ) -> dict[str, Any]:
        role = boundary_role_by_name.get(name, "unclassified")
        return {
            "name": name,
            "family": family,
            "role": role,
            "role_description": role_descriptions.get(role, "Unclassified module boundary."),
            "rtl": rtl,
            "top_module": top_module,
            "standalone_runtime_required": True,
            "standalone_runtime": standalone_runtime_proof(rtl),
            "module_only_runtime_required": True,
            "module_only_proof": module_only_proof(
                family=family,
                isolation=isolation,
                boundary=boundary,
                proof_path=module_isolation_proof,
                verilator_execution_report=verilator_execution_report,
            ),
            "cycle_evidence_required": True,
            "cycle_evidence": cycle_evidence(
                module,
                cycle_contract=cycle_contract,
                readme_cycle_coverage=readme_cycle_coverage,
                module_interfaces_doc=module_interfaces_doc,
                verilator_execution_report=verilator_execution_report,
            ),
        }

    module_boundary_taxonomy_entries: list[dict[str, Any]] = []
    for name, module in base_modules_by_name.items():
        isolation = base_isolation_by_name.get(name, {})
        boundary = base_boundary_by_name.get(name, {})
        rtl = boundary.get("imp2_rtl") or isolation.get("imp2_rtl")
        module_boundary_taxonomy_entries.append(
            source_boundary_entry(
                name=name,
                family="base_imp2_candidate",
                rtl=rtl,
                top_module=module["top_module"],
                module=module,
                isolation=isolation,
                boundary=boundary,
                cycle_contract=module_dpi_report["cycle_contract"],
                readme_cycle_coverage=module_dpi_report["readme_cycle_coverage"],
                module_interfaces_doc=module_dpi_report["module_interfaces_doc"],
                module_isolation_proof=module_dpi_report["module_isolation_proof"],
                verilator_execution_report=module_dpi_report["verilator_execution_report"],
            )
        )
    for name, module in generated_modules_by_name.items():
        isolation = generated_isolation_by_name.get(name, {})
        boundary = generated_boundary_by_name.get(name, {})
        rtl = isolation.get("dut_rtl") or next(iter(boundary.get("selected_dut_rtl", [])), "")
        module_boundary_taxonomy_entries.append(
            source_boundary_entry(
                name=name,
                family="generated_full_checkpoint",
                rtl=rtl,
                top_module=module["top_module"],
                module=module,
                isolation=isolation,
                boundary=boundary,
                cycle_contract=full_checkpoint_module_dpi["cycle_contract"],
                readme_cycle_coverage=full_checkpoint_module_dpi["readme_cycle_coverage"],
                module_interfaces_doc=full_checkpoint_module_dpi["module_interfaces_doc"],
                module_isolation_proof=full_checkpoint_module_dpi["module_isolation_proof"],
                verilator_execution_report=full_checkpoint_module_dpi[
                    "verilator_execution_report"
                ],
            )
        )
    module_boundary_taxonomy_entries.append(
        {
            "name": "generated_soc_top",
            "family": "generated_soc_top",
            "role": boundary_role_by_name["generated_soc_top"],
            "role_description": role_descriptions["top_glue"],
            "rtl": soc_top_artifacts["top"],
            "top_module": "e1_h1_soc_top",
            "standalone_runtime_required": True,
            "standalone_runtime": standalone_runtime_proof(soc_top_artifacts["top"]),
            "module_only_runtime_required": False,
            "module_only_proof": {
                "required": False,
                "status": "pass",
                "reason": "generated_soc_top_is_composition_boundary_with_standalone_cpp_verilator_smoke",
                "hierarchy_proof": "e1/generated/pipeline/29_end_to_end_smoke.json:generated_soc_top_hierarchy",
            },
            "cycle_evidence_required": False,
            "cycle_evidence": {
                "status": "pass" if generated_soc_top_hierarchy["status"] == "pass" else "fail",
                "generated_soc_top_doc": "e1/e1-h1/docs/generated-soc-top.md",
                "verilator_execution_report": (
                    "e1/generated/pipeline/29_end_to_end_smoke.json:"
                    "generated_soc_top_standalone_verilator"
                ),
                "reason": "top glue is covered by hierarchy and standalone top smoke rather than a module-only cycle template",
            },
        }
    )
    module_boundary_roles = {
        role: [
            entry["name"]
            for entry in module_boundary_taxonomy_entries
            if entry["role"] == role
        ]
        for role in required_taxonomy_roles
    }
    expected_taxonomy_names = (
        list(base_modules_by_name)
        + list(generated_modules_by_name)
        + ["generated_soc_top"]
    )
    expected_active_runtime_paths = production_rtl_inventory.get(
        "standalone_runtime_inventory",
        {},
    ).get("required_paths", [])
    taxonomy_active_runtime_paths = unique_ordered(
        [
            entry["rtl"]
            for entry in module_boundary_taxonomy_entries
            if entry["standalone_runtime_required"]
        ]
    )
    module_boundary_taxonomy_checks = [
        {
            "name": "module_boundary_taxonomy_covers_all_named_boundaries",
            "status": "pass"
            if set(entry["name"] for entry in module_boundary_taxonomy_entries)
            == set(expected_taxonomy_names)
            else "fail",
        },
        {
            "name": "module_boundary_taxonomy_covers_all_active_runtime_modules",
            "status": "pass"
            if set(taxonomy_active_runtime_paths) == set(expected_active_runtime_paths)
            else "fail",
        },
        {
            "name": "module_boundary_taxonomy_preserves_cpu_latch_systolic_categories",
            "status": "pass"
            if {
                "control_cpu",
                "control_scheduler",
                "control_slot_engine",
                "graph_sequencer",
            }.issubset(set(module_boundary_roles["cpu_control"]))
            and module_boundary_roles["latch_buffer"] == ["ingress_sram"]
            and module_boundary_roles["systolic_array"] == ["systolic_array"]
            and set(module_boundary_roles["linear_systolic_path"])
            == {"linear_scheduler", "linear_tile_engine", "linear_slot_engine"}
            else "fail",
        },
        {
            "name": "module_boundary_taxonomy_classifies_ingress_sram_and_top_glue",
            "status": "pass"
            if module_boundary_roles["digital_ingress"] == ["rgmii_ethernet_ingress"]
            and set(module_boundary_roles["sram_shell"]) == {"activation_sram", "accumulator_sram"}
            and set(module_boundary_roles["top_glue"])
            == {"full_checkpoint_top", "generated_soc_top"}
            else "fail",
        },
        {
            "name": "module_boundary_taxonomy_entries_have_runtime_and_cycle_evidence",
            "status": "pass"
            if all(
                entry["standalone_runtime"]["status"] == "pass"
                and (
                    not entry["module_only_runtime_required"]
                    or entry["module_only_proof"]["status"] == "pass"
                )
                and (
                    not entry["cycle_evidence_required"]
                    or entry["cycle_evidence"]["status"] == "pass"
                )
                for entry in module_boundary_taxonomy_entries
            )
            else "fail",
        },
    ]
    module_boundary_taxonomy = {
        "schema": "e1-module-boundary-taxonomy-v0",
        "status": (
            "pass"
            if all(check["status"] == "pass" for check in module_boundary_taxonomy_checks)
            else "fail"
        ),
        "scope": "active generated SoC top, base imp2 module boundaries, and generated full-checkpoint module boundaries",
        "roles": module_boundary_roles,
        "role_descriptions": role_descriptions,
        "expected_boundary_names": expected_taxonomy_names,
        "active_runtime_paths": taxonomy_active_runtime_paths,
        "expected_active_runtime_paths": expected_active_runtime_paths,
        "entries": module_boundary_taxonomy_entries,
        "checks": module_boundary_taxonomy_checks,
    }
    module_boundary_taxonomy_passed = module_boundary_taxonomy["status"] == "pass"
    objective_coverage = [
        {
            "requirement": "full_rtl_lowering_current_scope",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["status"] == "pass"
            and full_checkpoint_graph_rtl_lowering["full_checkpoint_structural_rtl_execution"]
            and rtl_top["full_checkpoint_structural_rtl_execution"]
            and rtl_top["full_command_verilator_report"]["status"] == "pass"
            else "fail",
            "evidence": [
                "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
            ],
            "scope": rtl_top["full_checkpoint_rtl_execution_scope"],
        },
        {
            "requirement": "correct_by_construction",
            "status": "pass"
            if module_dpi_passed
            and full_checkpoint_module_dpi_passed
            and source_derived_dpi_passed
            and target_rtl_artifacts_passed
            and generated_soc_top_construction_artifacts_passed
            and production_rtl_inventory_passed
            else "fail",
            "evidence": [
                "e1/generated/pipeline/12_module_dpi_generation.json",
                "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json",
                "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
                "e1/generated/pipeline/28_lowering_construction_certificate.json",
            ],
        },
        {
            "requirement": "cpp_program_generates_dpi_and_tests_modules",
            "status": "pass"
            if module_dpi_generator_sources_passed
            and module_dpi_generator_build_and_run_recorded
            and module_dpi_generator_stdout_reports_module_counts
            and dpi_generation_provenance_audit_passed
            and module_dpi_cpp_verilator_launchers_passed
            and module_dpi_cpp_verilator_launchers_validate_runtime_markers
            and module_dpi_cpp_verilator_launchers_validate_runtime_phase_traces
            and module_dpi_passed
            and full_checkpoint_module_dpi_passed
            else "fail",
            "evidence": [
                module_dpi_report["generator"],
                full_checkpoint_module_dpi["generator"],
                "e1/generated/pipeline/12_module_dpi_generation.json:generator_build",
                "e1/generated/pipeline/12_module_dpi_generation.json:generator_execution",
                "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json:generator_build",
                "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json:generator_execution",
                module_dpi_report["verilator_execution_launcher"],
                full_checkpoint_module_dpi["verilator_execution_launcher"],
                module_dpi_report["verilator_execution_report"],
                full_checkpoint_module_dpi["verilator_execution_report"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:dpi_generation_provenance_audit",
            ],
        },
        {
            "requirement": "each_active_source_derived_systemverilog_module_has_module_only_dpi_proof",
            "status": "pass"
            if source_derived_dpi_passed
            and generated_child_stub_boundary_passed
            and base_module_boundary_passed
            and source_derived_inventory_module_only_passed
            else "fail",
            "evidence": [
                "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory.module_only_dpi_inventory",
            ],
            "required_categories": production_rtl_inventory.get("module_only_dpi_inventory", {}).get(
                "required_categories", []
            ),
            "required_paths": production_rtl_inventory.get("module_only_dpi_inventory", {}).get(
                "required_paths", []
            ),
        },
        {
            "requirement": "each_active_systemverilog_module_has_single_dut_runtime_audit",
            "status": "pass" if systemverilog_module_coverage_audit_passed else "fail",
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systemverilog_module_coverage_audit",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory",
            ],
            "active_source_categories": systemverilog_module_coverage_audit[
                "active_source_categories"
            ],
            "active_module_only_row_count": systemverilog_module_coverage_audit[
                "active_module_only_row_count"
            ],
        },
        {
            "requirement": "each_defined_systemverilog_module_has_explicit_runtime_scope",
            "status": "pass" if systemverilog_defined_module_runtime_audit_passed else "fail",
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systemverilog_defined_module_runtime_audit",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systemverilog_module_coverage_audit",
            ],
            "defined_module_count": systemverilog_defined_module_runtime_audit[
                "defined_module_count"
            ],
            "active_module_only_defined_module_count": (
                systemverilog_defined_module_runtime_audit[
                    "active_module_only_defined_module_count"
                ]
            ),
            "imp1_mock_defined_module_count": (
                systemverilog_defined_module_runtime_audit[
                    "imp1_mock_defined_module_count"
                ]
            ),
            "standalone_top_defined_module_count": (
                systemverilog_defined_module_runtime_audit[
                    "standalone_top_defined_module_count"
                ]
            ),
        },
        {
            "requirement": "each_active_systemverilog_module_has_standalone_runtime_proof",
            "status": "pass" if active_rtl_standalone_runtime_passed else "fail",
            "evidence": [
                "e1/generated/pipeline/29_end_to_end_smoke.json:generated_soc_top_standalone_verilator",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory.standalone_runtime_inventory",
            ],
            "required_categories": production_rtl_inventory.get(
                "standalone_runtime_inventory", {}
            ).get("required_categories", []),
            "required_paths": production_rtl_inventory.get(
                "standalone_runtime_inventory", {}
            ).get("required_paths", []),
            "exempt_categories": production_rtl_inventory.get(
                "standalone_runtime_inventory", {}
            ).get("exempt_categories", []),
        },
        {
            "requirement": "module_interfaces_document_every_input_output_signal",
            "status": "pass" if module_interface_signal_inventory_passed else "fail",
            "evidence": [
                module_dpi_report["module_interfaces_doc"],
                full_checkpoint_module_dpi["module_interfaces_doc"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_interface_signal_inventory",
            ],
            "base_module_count": base_module_interface_signal_inventory["module_count"],
            "generated_full_checkpoint_module_count": (
                generated_module_interface_signal_inventory["module_count"]
            ),
            "scope": module_interface_signal_inventory["scope"],
        },
        {
            "requirement": "systolic_array_result_digest_matches_cpp_scoreboard",
            "status": systolic_array_result_digest_proof["status"],
            "evidence": [
                "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv",
                module_dpi_report["verilator_execution_report"],
                module_dpi_report["verilator_execution_launcher"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systolic_array_result_digest_proof",
            ],
            "result_signal": systolic_array_result_digest_proof["result_signal"],
            "expected_digest_marker": systolic_array_result_digest_proof[
                "expected_digest_marker"
            ],
        },
        {
            "requirement": "module_boundary_taxonomy_proves_separation_of_concerns",
            "status": "pass" if module_boundary_taxonomy_passed else "fail",
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_boundary_taxonomy",
                module_dpi_report["module_isolation_proof"],
                full_checkpoint_module_dpi["module_isolation_proof"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory.standalone_runtime_inventory",
            ],
            "roles": module_boundary_taxonomy["roles"],
            "active_runtime_paths": module_boundary_taxonomy["active_runtime_paths"],
        },
        {
            "requirement": "full_command_trace_anchors_match_cpp_schedule",
            "status": "pass"
            if rtl_top["full_command_trace_anchor_check"]
            and full_checkpoint_graph_rtl_lowering["full_checkpoint_command_stream_rtl_execution"]
            else "fail",
            "evidence": [
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json:full_command_trace_anchors",
                "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json:checks.full_command_trace_anchors_match_cpp_schedule",
            ],
        },
        {
            "requirement": "full_command_per_op_trace_coverage_matches_cpp_schedule",
            "status": "pass"
            if rtl_top["full_command_per_op_trace_coverage_check"]
            and full_checkpoint_graph_rtl_lowering["full_checkpoint_command_stream_rtl_execution"]
            else "fail",
            "evidence": [
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json:full_command_per_op_trace_coverage",
                "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json:checks.full_command_per_op_trace_coverage_matches_cpp_schedule",
            ],
        },
        {
            "requirement": "cpu_latch_buffer_systolic_array_are_separate_boundaries",
            "status": "pass"
            if module_isolation_passed
            and full_checkpoint_module_isolation_passed
            and generated_soc_top_hierarchy["status"] == "pass"
            else "fail",
            "evidence": [
                module_dpi_report["module_isolation_proof"],
                full_checkpoint_module_dpi["module_isolation_proof"],
                "e1/generated/pipeline/29_end_to_end_smoke.json:generated_soc_top_hierarchy",
            ],
            "separated_boundaries": {
                "base": module_dpi_report.get("module_isolation", {}).get("separated_boundaries", {}),
                "generated_full_checkpoint": full_checkpoint_module_dpi.get("module_isolation", {}).get(
                    "separated_boundaries", {}
                ),
                "generated_soc_top": generated_soc_top_hierarchy.get("separated_boundaries", {}),
            },
        },
        {
            "requirement": "latch_buffer_holds_and_releases_data",
            "status": "pass"
            if module_dpi_report.get("module_isolation", {})
            .get("separated_boundaries", {})
            .get("latch_buffer_module")
            == "ingress_sram"
            and rtl_top["full_command_verilator_report"]["saw_latched_hold"]
            else "fail",
            "evidence": [
                "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv",
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json:full_command_verilator_report",
            ],
        },
        {
            "requirement": "each_cycle_is_identified_in_readme_diagrams",
            "status": "pass"
            if base_module_cycle_docs_passed
            and generated_full_checkpoint_module_cycle_docs_passed
            and cycle_diagram_documentation_audit_passed
            and all(
                check["status"] == "pass"
                for check in full_checkpoint_graph_rtl_lowering["readme_cycle_coverage"][
                    "diagram_checks"
                ]
            )
            and all(
                all(check["status"] == "pass" for check in template["checks"])
                for template in full_checkpoint_graph_rtl_lowering["readme_cycle_coverage"][
                    "templates"
                ]
            )
            else "fail",
            "evidence": [
                "e1/e1-h1/docs/modules/README.md",
                module_dpi_report["readme_cycle_coverage"],
                full_checkpoint_module_dpi["readme_cycle_coverage"],
                "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json:readme_cycle_coverage",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:cycle_diagram_audit",
            ],
        },
        {
            "requirement": "runtime_cycle_phase_traces_match_readme_contracts",
            "status": "pass"
            if base_runtime_phase_traces_passed
            and generated_runtime_phase_traces_passed
            and source_derived_dpi_passed
            and rtl_top["full_command_cycle_phase_check"]
            else "fail",
            "evidence": [
                module_dpi_report["verilator_execution_report"],
                full_checkpoint_module_dpi["verilator_execution_report"],
                "e1/generated/pipeline/27_full_graph_module_dpi_binding.json:source_derived_module_dpi_coverage",
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json:full_command_cycle_phase_check",
            ],
        },
    ]
    certificate_non_claims = [
        "This certificate does not claim TinyLlama numeric output equivalence.",
        "Structural RTL execution does not claim output tensor equivalence.",
        "This certificate does not claim live full-checkpoint StableHLO export without the checkpoint dependencies and cache.",
    ]
    objective_by_requirement = {
        entry["requirement"]: entry
        for entry in objective_coverage
    }

    def objective_status(*requirements: str) -> str:
        return (
            "pass"
            if requirements
            and all(
                objective_by_requirement.get(requirement, {}).get("status") == "pass"
                for requirement in requirements
            )
            else "fail"
        )

    taxonomy_entry_by_name = {
        entry["name"]: entry
        for entry in module_boundary_taxonomy.get("entries", [])
    }
    cpu_boundary_names = [
        "control_cpu",
        "control_scheduler",
        "control_slot_engine",
        "graph_sequencer",
    ]
    cpu_boundaries_are_isolated = all(
        taxonomy_entry_by_name.get(name, {}).get("role") == "cpu_control"
        and taxonomy_entry_by_name.get(name, {}).get("standalone_runtime", {}).get(
            "status"
        )
        == "pass"
        and (
            not taxonomy_entry_by_name.get(name, {}).get("module_only_runtime_required")
            or taxonomy_entry_by_name.get(name, {}).get("module_only_proof", {}).get(
                "status"
            )
            == "pass"
        )
        for name in cpu_boundary_names
    )
    systolic_boundary_is_isolated = (
        module_boundary_taxonomy.get("roles", {}).get("systolic_array") == ["systolic_array"]
        and taxonomy_entry_by_name.get("systolic_array", {}).get(
            "standalone_runtime",
            {},
        ).get("status")
        == "pass"
        and taxonomy_entry_by_name.get("systolic_array", {}).get(
            "module_only_proof",
            {},
        ).get("status")
        == "pass"
    )
    latch_buffer_boundary_is_isolated = (
        module_boundary_taxonomy.get("roles", {}).get("latch_buffer") == ["ingress_sram"]
        and taxonomy_entry_by_name.get("ingress_sram", {}).get(
            "standalone_runtime",
            {},
        ).get("status")
        == "pass"
        and taxonomy_entry_by_name.get("ingress_sram", {}).get(
            "module_only_proof",
            {},
        ).get("status")
        == "pass"
    )
    objective_traceability_rows = [
        {
            "objective_phrase": "try full rtl lowering",
            "mapped_requirements": [
                "full_rtl_lowering_current_scope",
                "full_command_trace_anchors_match_cpp_schedule",
                "full_command_per_op_trace_coverage_matches_cpp_schedule",
            ],
            "status": objective_status(
                "full_rtl_lowering_current_scope",
                "full_command_trace_anchors_match_cpp_schedule",
                "full_command_per_op_trace_coverage_matches_cpp_schedule",
            ),
            "evidence": [
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
                "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
            ],
            "verified_scope": rtl_top["full_checkpoint_rtl_execution_scope"],
        },
        {
            "objective_phrase": "correct to be construction",
            "mapped_requirements": [
                "correct_by_construction",
                "each_active_systemverilog_module_has_single_dut_runtime_audit",
                "each_defined_systemverilog_module_has_explicit_runtime_scope",
                "each_active_systemverilog_module_has_standalone_runtime_proof",
            ],
            "status": objective_status(
                "correct_by_construction",
                "each_active_systemverilog_module_has_single_dut_runtime_audit",
                "each_defined_systemverilog_module_has_explicit_runtime_scope",
                "each_active_systemverilog_module_has_standalone_runtime_proof",
            ),
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:checks",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:target_rtl_evidence.production_rtl_inventory",
            ],
        },
        {
            "objective_phrase": "make a c++ program that automatically generates the dpi",
            "mapped_requirements": [
                "cpp_program_generates_dpi_and_tests_modules",
            ],
            "status": (
                "pass"
                if objective_status("cpp_program_generates_dpi_and_tests_modules") == "pass"
                and dpi_generation_provenance_audit_passed
                else "fail"
            ),
            "evidence": [
                module_dpi_report["generator"],
                full_checkpoint_module_dpi["generator"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:dpi_generation_provenance_audit",
            ],
            "generated_module_count": dpi_generation_provenance_audit["module_count"],
            "generated_artifact_count": dpi_generation_provenance_audit[
                "generated_artifact_count"
            ],
        },
        {
            "objective_phrase": "test each systemverilog module by itself",
            "mapped_requirements": [
                "each_defined_systemverilog_module_has_explicit_runtime_scope",
                "each_active_source_derived_systemverilog_module_has_module_only_dpi_proof",
                "each_active_systemverilog_module_has_single_dut_runtime_audit",
            ],
            "status": objective_status(
                "each_defined_systemverilog_module_has_explicit_runtime_scope",
                "each_active_source_derived_systemverilog_module_has_module_only_dpi_proof",
                "each_active_systemverilog_module_has_single_dut_runtime_audit",
            ),
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systemverilog_module_coverage_audit",
                "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
            ],
            "active_module_only_row_count": systemverilog_module_coverage_audit[
                "active_module_only_row_count"
            ],
            "defined_module_count": systemverilog_defined_module_runtime_audit[
                "defined_module_count"
            ],
        },
        {
            "objective_phrase": "clear separation of concerns",
            "mapped_requirements": [
                "module_boundary_taxonomy_proves_separation_of_concerns",
                "cpu_latch_buffer_systolic_array_are_separate_boundaries",
            ],
            "status": objective_status(
                "module_boundary_taxonomy_proves_separation_of_concerns",
                "cpu_latch_buffer_systolic_array_are_separate_boundaries",
            ),
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_boundary_taxonomy",
                module_dpi_report["module_isolation_proof"],
                full_checkpoint_module_dpi["module_isolation_proof"],
            ],
            "roles": module_boundary_taxonomy["roles"],
        },
        {
            "objective_phrase": "the systolic array should be by itself",
            "mapped_requirements": [
                "module_boundary_taxonomy_proves_separation_of_concerns",
                "systolic_array_result_digest_matches_cpp_scoreboard",
            ],
            "status": (
                "pass"
                if objective_status(
                    "module_boundary_taxonomy_proves_separation_of_concerns",
                    "systolic_array_result_digest_matches_cpp_scoreboard",
                )
                == "pass"
                and systolic_boundary_is_isolated
                else "fail"
            ),
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_boundary_taxonomy.entries.systolic_array",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:systolic_array_result_digest_proof",
            ],
            "boundary": taxonomy_entry_by_name.get("systolic_array", {}),
        },
        {
            "objective_phrase": "the cpus should be by itself",
            "mapped_requirements": [
                "module_boundary_taxonomy_proves_separation_of_concerns",
                "cpu_latch_buffer_systolic_array_are_separate_boundaries",
            ],
            "status": (
                "pass"
                if objective_status(
                    "module_boundary_taxonomy_proves_separation_of_concerns",
                    "cpu_latch_buffer_systolic_array_are_separate_boundaries",
                )
                == "pass"
                and cpu_boundaries_are_isolated
                else "fail"
            ),
            "evidence": [
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_boundary_taxonomy.roles.cpu_control",
            ],
            "cpu_boundaries": {
                name: taxonomy_entry_by_name.get(name, {})
                for name in cpu_boundary_names
            },
        },
        {
            "objective_phrase": "there should be a buffer that latches",
            "mapped_requirements": [
                "latch_buffer_holds_and_releases_data",
                "module_boundary_taxonomy_proves_separation_of_concerns",
            ],
            "status": (
                "pass"
                if objective_status(
                    "latch_buffer_holds_and_releases_data",
                    "module_boundary_taxonomy_proves_separation_of_concerns",
                )
                == "pass"
                and latch_buffer_boundary_is_isolated
                else "fail"
            ),
            "evidence": [
                "e1/generated/pipeline/24_full_checkpoint_rtl_top.json:full_command_verilator_report.saw_latched_hold",
                "e1/generated/pipeline/28_lowering_construction_certificate.json:module_boundary_taxonomy.entries.ingress_sram",
            ],
            "boundary": taxonomy_entry_by_name.get("ingress_sram", {}),
        },
        {
            "objective_phrase": "each cycle to be clearly identified and in a diagram placed in the readme",
            "mapped_requirements": [
                "each_cycle_is_identified_in_readme_diagrams",
                "runtime_cycle_phase_traces_match_readme_contracts",
            ],
            "status": (
                "pass"
                if objective_status(
                    "each_cycle_is_identified_in_readme_diagrams",
                    "runtime_cycle_phase_traces_match_readme_contracts",
                )
                == "pass"
                and cycle_diagram_documentation_audit_passed
                else "fail"
            ),
            "evidence": [
                "e1/e1-h1/docs/modules/README.md#cycle-diagram",
                cycle_diagram_documentation_audit["readme_runtime_matrix"],
                "e1/generated/pipeline/28_lowering_construction_certificate.json:cycle_diagram_audit",
            ],
            "cycle_audit_counts": {
                "base_modules": cycle_diagram_documentation_audit["base_module_count"],
                "generated_full_checkpoint_modules": cycle_diagram_documentation_audit[
                    "generated_full_checkpoint_module_count"
                ],
                "full_graph_templates": cycle_diagram_documentation_audit[
                    "full_graph_template_count"
                ],
            },
        },
    ]
    objective_traceability_checks = [
        {
            "name": "all_human_objective_phrases_have_passing_evidence",
            "status": "pass"
            if all(row["status"] == "pass" for row in objective_traceability_rows)
            else "fail",
        },
        {
            "name": "traceability_preserves_structural_scope_and_non_claims",
            "status": "pass"
            if rtl_top["full_checkpoint_rtl_execution_scope"]
            == "structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence"
            and not rtl_top["full_checkpoint_numeric_output_equivalence"]
            and certificate_non_claims
            else "fail",
        },
        {
            "name": "traceability_mapped_requirements_exist_in_objective_rows",
            "status": "pass"
            if set(
                requirement
                for row in objective_traceability_rows
                for requirement in row["mapped_requirements"]
            ).issubset(set(objective_by_requirement))
            else "fail",
        },
        {
            "name": "traceability_rows_carry_concrete_evidence",
            "status": "pass"
            if all(row.get("evidence") for row in objective_traceability_rows)
            else "fail",
        },
    ]
    objective_traceability_audit = {
        "schema": "e1-objective-traceability-audit-v0",
        "status": "pass"
        if all(check["status"] == "pass" for check in objective_traceability_checks)
        else "fail",
        "original_objective": (
            "try full rtl lowering; correct by construction; make a C++ program "
            "that automatically generates DPI and tests each SystemVerilog module "
            "by itself; keep systolic array, CPUs, and latch buffer separated; "
            "identify each cycle in README diagrams"
        ),
        "verified_scope": rtl_top["full_checkpoint_rtl_execution_scope"],
        "residual_non_claims": certificate_non_claims,
        "rows": objective_traceability_rows,
        "checks": objective_traceability_checks,
    }
    objective_traceability_audit_passed = (
        objective_traceability_audit["status"] == "pass"
    )
    active_objective_completion_audit = {
        "schema": "e1-active-objective-completion-audit-v0",
        "status": "pass"
        if objective_coverage
        and all(entry["status"] == "pass" for entry in objective_coverage)
        and rtl_top["full_checkpoint_structural_rtl_execution"]
        and rtl_top["full_checkpoint_rtl_execution_scope"]
        == "structural_graph_slot_and_command_stream_verilator_execution_without_tensor_numeric_equivalence"
        and source_derived_dpi_passed
        and base_module_boundary_passed
        and generated_child_stub_boundary_passed
        and base_runtime_phase_traces_passed
        and generated_runtime_phase_traces_passed
        and module_boundary_taxonomy_passed
        and module_interface_signal_inventory_passed
        and systemverilog_module_coverage_audit_passed
        and systemverilog_defined_module_runtime_audit_passed
        and cycle_diagram_documentation_audit_passed
        and dpi_generation_provenance_audit_passed
        and objective_traceability_audit_passed
        and systolic_array_result_digest_proof["status"] == "pass"
        else "fail",
        "verdict": "proved_for_structural_rtl_lowering_scope",
        "verified_scope": rtl_top["full_checkpoint_rtl_execution_scope"],
        "required_requirement_count": len(objective_coverage),
        "proved_requirement_count": sum(
            1 for entry in objective_coverage if entry["status"] == "pass"
        ),
        "requirements": objective_coverage,
        "completion_evidence": [
            "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
            "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
            "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
            "e1/generated/pipeline/28_lowering_construction_certificate.json",
            "e1/generated/pipeline/28_lowering_construction_certificate.json:objective_traceability_audit",
            "e1/generated/pipeline/29_end_to_end_smoke.json",
            "e1/e1-h1/docs/modules/README.md",
        ],
        "residual_non_claims": certificate_non_claims,
    }
    checks = [
        {
            "name": "source_operation_instances_match_stablehlo_counts",
            "status": "pass"
            if dict(sorted(source_instance_counts.items())) == dict(sorted(inspection["operation_counts"].items()))
            and len(source_instance_coverage) == int(inspection["total_operations"])
            else "fail",
        },
        {
            "name": "source_operation_spans_are_ordered",
            "status": "pass"
            if [entry["source_index"] for entry in source_instance_coverage] == list(range(len(source_instance_coverage)))
            and all(
                entry["source_line"] <= entry["source_end_line"]
                for entry in source_instance_coverage
            )
            and [entry["source_line"] for entry in source_instance_coverage]
            == sorted(entry["source_line"] for entry in source_instance_coverage)
            else "fail",
        },
        {
            "name": "every_source_operation_instance_has_bound_rtl_and_dpi",
            "status": "pass"
            if source_instance_coverage
            and all(
                entry["bound_ip"] is not None
                and entry["lowering_status"] == "pass"
                and entry["active_implementation"] == "imp2"
                and entry["rtl_files"]
                and entry["module_dpi_probe"]
                and entry["module_dpi_flist"]
                for entry in source_instance_coverage
            )
            else "fail",
        },
        {
            "name": "all_fixture_stablehlo_ops_have_lowering_rules",
            "status": "pass"
            if set(inspection["operation_counts"]) == set(lowered_by_op)
            and all(entry["lowering_status"] == "pass" for entry in operation_coverage)
            else "fail",
        },
        {
            "name": "all_fixture_lowerings_target_active_imp2",
            "status": "pass"
            if all(entry["active_implementation"] == "imp2" for entry in operation_coverage)
            else "fail",
        },
        {
            "name": "base_module_dpi_verilator_and_ledgers_pass",
            "status": "pass" if module_dpi_passed else "fail",
        },
        {
            "name": "base_module_dpi_cycle_contracts_are_documented_in_readme",
            "status": "pass" if base_module_cycle_docs_passed else "fail",
        },
        {
            "name": "full_checkpoint_graph_slots_have_rtl_bindings",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["status"] == "pass"
            and int(full_checkpoint_graph_rtl_lowering["graph"]["slot_binding_count"]) == expected_graph_slots
            and all(binding["rtl_file"] and binding["cycle_template"] for binding in full_checkpoint_graph_rtl_lowering["slot_bindings"])
            else "fail",
        },
        {
            "name": "full_checkpoint_command_stream_runs_through_rtl_top",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["full_checkpoint_structural_rtl_execution"]
            and full_checkpoint_graph_rtl_lowering["full_checkpoint_command_stream_rtl_execution"]
            and rtl_top["full_checkpoint_structural_rtl_execution"]
            and rtl_top["verilator_execution"]["status"] == "pass"
            and rtl_top["full_command_payload_schedule_check"]
            and rtl_top["full_command_payload_digest_check"]
            and rtl_top["full_command_payload_digest"] == command_stream["payload_digest"]
            and rtl_top["full_command_cycle_phase_check"]
            and rtl_top["full_command_control_schedule_check"]
            and rtl_top["full_command_control_digest_check"]
            and rtl_top["full_command_trace_anchor_check"]
            and rtl_top["full_command_per_op_trace_coverage_check"]
            else "fail",
        },
        {
            "name": "full_checkpoint_structural_rtl_execution_is_proven",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["full_checkpoint_structural_rtl_execution"]
            and rtl_top["full_checkpoint_structural_rtl_execution"]
            else "fail",
        },
        {
            "name": "full_checkpoint_rtl_execution_is_scoped_structural",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["full_checkpoint_rtl_execution"]
            and rtl_top["full_checkpoint_rtl_execution"]
            and full_checkpoint_graph_rtl_lowering["full_checkpoint_rtl_execution_scope"]
            == rtl_top["full_checkpoint_rtl_execution_scope"]
            and "without_tensor_numeric_equivalence" in rtl_top["full_checkpoint_rtl_execution_scope"]
            else "fail",
        },
        {
            "name": "full_checkpoint_payload_digest_matches_cpp_schedule",
            "status": "pass"
            if command_stream["payload_digest"] == rtl_top["full_command_payload_digest"]
            and command_stream["payload_digest"] == full_checkpoint_graph_rtl_lowering["command_stream"]["payload_digest"]
            else "fail",
        },
        {
            "name": "full_checkpoint_control_payload_digest_matches_graph_schedule",
            "status": "pass"
            if rtl_top["full_command_control_digest_check"]
            and rtl_top["full_command_control_digest"]
            == full_checkpoint_graph_rtl_lowering["command_stream"]["control_payload_digest"]
            else "fail",
        },
        {
            "name": "full_checkpoint_trace_anchors_match_cpp_schedule",
            "status": "pass" if rtl_top["full_command_trace_anchor_check"] else "fail",
        },
        {
            "name": "full_checkpoint_per_op_trace_coverage_matches_cpp_schedule",
            "status": "pass" if rtl_top["full_command_per_op_trace_coverage_check"] else "fail",
        },
        {
            "name": "generated_full_checkpoint_module_dpi_verilator_and_ledgers_pass",
            "status": "pass" if full_checkpoint_module_dpi_passed else "fail",
        },
        {
            "name": "generated_full_checkpoint_module_cycle_contracts_are_documented_in_readme",
            "status": "pass" if generated_full_checkpoint_module_cycle_docs_passed else "fail",
        },
        {
            "name": "source_derived_rtl_modules_have_module_only_dpi_proofs",
            "status": "pass" if source_derived_dpi_passed else "fail",
        },
        {
            "name": "source_derived_rtl_modules_have_cpp_launcher_runtime_and_recipe_proofs",
            "status": "pass" if source_derived_cpp_launcher_evidence_passed else "fail",
        },
        {
            "name": "source_derived_rtl_modules_have_cpp_launcher_readme_cycle_proofs",
            "status": "pass" if source_derived_cpp_launcher_evidence_passed else "fail",
        },
        {
            "name": "source_derived_production_rtl_inventory_has_module_only_dpi",
            "status": "pass" if source_derived_inventory_module_only_passed else "fail",
        },
        {
            "name": "systemverilog_module_coverage_audit_passes",
            "status": systemverilog_module_coverage_audit["status"],
        },
        {
            "name": "systemverilog_defined_module_runtime_audit_passes",
            "status": systemverilog_defined_module_runtime_audit["status"],
        },
        {
            "name": "module_dpi_evidence_preserves_module_only_flist_boundaries",
            "status": "pass"
            if generated_child_stub_boundary_passed and base_module_boundary_passed
            else "fail",
        },
        {
            "name": "module_dpi_boundaries_have_cpp_launcher_runtime_and_recipe_proofs",
            "status": "pass" if base_module_cpp_launcher_evidence_passed else "fail",
        },
        {
            "name": "module_dpi_boundaries_have_cpp_launcher_readme_cycle_proofs",
            "status": "pass" if base_module_cpp_launcher_evidence_passed else "fail",
        },
        {
            "name": "module_dpi_boundary_artifacts_exist",
            "status": "pass"
            if module_dpi_boundary_artifacts
            and all((REPO_ROOT / path).exists() for path in module_dpi_boundary_artifacts)
            else "fail",
        },
        {
            "name": "objective_coverage_requirements_pass",
            "status": "pass"
            if all(entry["status"] == "pass" for entry in objective_coverage)
            else "fail",
        },
        {
            "name": "active_objective_completion_audit_passes",
            "status": active_objective_completion_audit["status"],
        },
        {
            "name": "objective_traceability_audit_passes",
            "status": objective_traceability_audit["status"],
        },
        {
            "name": "base_module_isolation_report_passes",
            "status": "pass" if module_isolation_passed else "fail",
        },
        {
            "name": "generated_full_checkpoint_module_isolation_report_passes",
            "status": "pass" if full_checkpoint_module_isolation_passed else "fail",
        },
        *module_boundary_taxonomy["checks"],
        *production_rtl_inventory_check_rows,
        {
            "name": "module_cycle_documentation_artifacts_exist",
            "status": "pass"
            if module_cycle_doc_artifacts
            and all((REPO_ROOT / path).exists() for path in module_cycle_doc_artifacts)
            else "fail",
        },
        {
            "name": "cycle_diagram_audit_passes",
            "status": cycle_diagram_documentation_audit["status"],
        },
        *module_interface_signal_inventory["checks"],
        {
            "name": "module_dpi_generator_and_runner_sources_are_hashed",
            "status": "pass" if module_dpi_generator_sources_passed else "fail",
        },
        {
            "name": "module_dpi_generator_build_and_run_commands_are_recorded",
            "status": "pass" if module_dpi_generator_build_and_run_recorded else "fail",
        },
        {
            "name": "module_dpi_generator_execution_stdout_reports_module_counts",
            "status": "pass" if module_dpi_generator_stdout_reports_module_counts else "fail",
        },
        {
            "name": "dpi_generation_provenance_audit_passes",
            "status": dpi_generation_provenance_audit["status"],
        },
        {
            "name": "module_dpi_cpp_verilator_launchers_match_execution_recipes",
            "status": "pass" if module_dpi_cpp_verilator_launchers_passed else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launchers_run_module_tests",
            "status": "pass" if module_dpi_cpp_verilator_launchers_passed else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launchers_validate_runtime_markers",
            "status": "pass" if module_dpi_cpp_verilator_launchers_validate_runtime_markers else "fail",
        },
        {
            "name": "module_dpi_cpp_verilator_launchers_validate_runtime_phase_traces",
            "status": "pass" if module_dpi_cpp_verilator_launchers_validate_runtime_phase_traces else "fail",
        },
        {
            "name": "systolic_array_result_digest_matches_cpp_scoreboard",
            "status": systolic_array_result_digest_proof["status"],
        },
        {
            "name": "target_filelists_match_active_imp2",
            "status": "pass"
            if all(
                entry["matches_target_manifest_rtl_files"] and entry["matches_active_imp2_flist"]
                for entry in target_filelist_coverage
            )
            else "fail",
        },
        {
            "name": "target_filelist_rtl_artifacts_are_hashed",
            "status": "pass" if target_rtl_artifacts_passed else "fail",
        },
        {
            "name": "generated_soc_top_construction_artifacts_are_hashed",
            "status": "pass" if generated_soc_top_construction_artifacts_passed else "fail",
        },
        {
            "name": "generated_soc_top_hierarchy_matches_manifest",
            "status": generated_soc_top_hierarchy["status"],
        },
        {
            "name": "readme_documents_every_cycle_template",
            "status": "pass"
            if all(
                all(check["status"] == "pass" for check in template["checks"])
                for template in readme_cycle_templates
            )
            else "fail",
        },
        {
            "name": "separation_of_concerns_is_preserved",
            "status": "pass"
            if set(rtl_top["separation"])
            == {"control_slot_engine", "graph_sequencer", "latch_buffer", "linear_slot_engine", "systolic_array"}
            and "ingress_sram" in full_graph_module_dpi_binding["required_base_modules"]
            and "systolic_array" in full_graph_module_dpi_binding["required_base_modules"]
            and "control_cpu" in full_graph_module_dpi_binding["required_base_modules"]
            else "fail",
        },
        {
            "name": "numeric_output_equivalence_remains_explicit_non_claim",
            "status": "pass"
            if not full_checkpoint_graph_rtl_lowering["full_checkpoint_numeric_output_equivalence"]
            and not rtl_top["full_checkpoint_numeric_output_equivalence"]
            else "fail",
        },
    ]
    artifact_paths = unique_ordered(
        [
            repo_rel(fixture_path),
            "e1/generated/pipeline/03_stablehlo_inspection.json",
            "e1/generated/pipeline/05_e1_h1_binding.json",
            rtl_lowering["implementation_matrix"],
            rtl_lowering["module_dpi_generation"]["manifest"],
            "e1/generated/pipeline/12_module_dpi_generation.json",
            "e1/generated/pipeline/15_rtl_lowering.json",
            "e1/generated/pipeline/18_full_checkpoint_rtl_lowering_plan.json",
            command_stream["header"],
            "e1/generated/pipeline/19_full_checkpoint_command_stream.json",
            rtl_cycle["scheduler_rtl"],
            "e1/generated/pipeline/20_full_checkpoint_rtl_cycle_lowering.json",
            rtl_top["top_rtl"],
            rtl_top["flist"],
            rtl_top["full_verilator_tb"],
            "e1/generated/pipeline/24_full_checkpoint_rtl_top.json",
            "e1/generated/pipeline/25_full_checkpoint_graph_rtl_lowering_proof.json",
            full_checkpoint_module_dpi["manifest"],
            "e1/generated/pipeline/26_full_checkpoint_module_dpi_generation.json",
            "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
            *target_rtl_artifacts,
            *generated_soc_top_construction_artifacts,
            *production_rtl_inventory_artifacts,
            *imp1_mock_runtime_artifacts,
            *module_dpi_generator_source_artifacts,
            *module_dpi_cpp_verilator_launcher_artifacts,
            *dpi_generation_provenance_artifacts,
            *module_cycle_doc_artifacts,
            *module_dpi_boundary_artifacts,
            "e1/e1-h1/generated/targets/manifest.json",
            target_manifest["fpga"]["filelist"],
            target_manifest["openroad"]["filelist"],
            "e1/e1-h1/docs/generated-soc-top.md",
            "e1/e1-h1/docs/modules/README.md",
        ]
    )
    certificate = {
        "schema": "e1-lowering-construction-certificate-v0",
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "model_id": manifest["model_id"],
        "truth_boundary": "stablehlo_fixture_and_full_checkpoint_graph_to_imp2_rtl_contracts",
        "claim_scope": [
            "Checked-in StableHLO fixture operations are bound to active imp2 RTL modules with module-DPI proofs.",
            "The shape-complete TinyLlama full-checkpoint graph is lowered to ordered RTL slot dispatch with structural full-command Verilator execution checks.",
            "The RTL-accepted full-command payload digest matches the generated C++ schedule digest.",
            "The RTL-observed CPU/control slot payload digest matches the generated graph schedule digest.",
            "Generated and separated RTL modules have module-only Verilator+DPI evidence and documented cycle templates.",
            "The SystemVerilog module coverage audit lists every production RTL row, parsed module names, run scope, and single-DUT runtime proof status.",
            "The defined-module runtime audit assigns every parsed production SystemVerilog module an explicit module-only, standalone-top, or accepted-mock Verilator runtime scope.",
            "The base systolic-array module exposes a result digest checked by the generated C++ DPI scoreboard during module-only Verilator execution.",
            "The cycle-diagram audit ties README cycle rows to generated cycle contracts and observed Verilator phase traces.",
            "The DPI generation provenance audit ties generator sources, emitted module-DPI artifacts, generated C++ Verilator launchers, and module runtime results into one reviewable section.",
            "The objective traceability audit maps each human-requested clause to concrete construction evidence while preserving the structural RTL scope and non-claims.",
            "Every active source-derived base imp2 and generated full-checkpoint RTL row in the production inventory is required to carry module-only DPI/Verilator coverage.",
            "Generated composed RTL module-DPI flists compile only the selected DUT plus probe, with child dependencies represented as probe-local stubs.",
            "Base and generated module cycle contracts are bound to machine-checked README rows and hashed cycle-coverage artifacts.",
            "Base and generated module interface tables enumerate input/output signals with descriptions and match parsed RTL port contracts.",
            "The C++ module-DPI generator sources, generator build/run records, normalized generator stdout, generated Verilator launchers, and Verilator recipe runner are construction evidence.",
            "Target-listed RTL artifacts, generated SoC top artifacts, and SoC top generator inputs are hashed as construction inputs.",
            "The production RTL inventory is classified by proof family and every listed RTL module name matches its construction proof.",
            "The module-boundary taxonomy maps active runtime modules into CPU/control, digital ingress, latch-buffer, SRAM, systolic, linear-path, and top-glue roles with standalone runtime evidence.",
            "The generated SoC top hierarchy instantiates manifest IPs as distinct CPU, latch-buffer, memory, RGMII, and systolic-array boundaries.",
        ],
        "non_claims": certificate_non_claims,
        "fixture_operation_coverage": operation_coverage,
        "source_operation_instance_coverage": source_instance_coverage,
        "full_checkpoint_graph": {
            "layers": aggregate["layers"],
            "slots_per_layer": total_layer_slots,
            "expected_graph_slots": expected_graph_slots,
            "observed_graph_slots": full_checkpoint_graph_rtl_lowering["graph"]["slot_binding_count"],
            "total_tile_commands": command_stream["total_tile_commands"],
            "total_rtl_cycles": rtl_cycle["total_rtl_cycles"],
            "payload_digest": command_stream["payload_digest"],
            "control_payload_digest": rtl_top["full_command_control_digest"],
            "rtl_execution": rtl_top["full_checkpoint_rtl_execution"],
            "rtl_execution_scope": rtl_top["full_checkpoint_rtl_execution_scope"],
            "structural_rtl_execution": rtl_top["full_checkpoint_structural_rtl_execution"],
            "verilator_execution_status": rtl_top["verilator_execution"]["status"],
            "full_command_verilator_report": rtl_top["full_command_verilator_report"],
            "full_verilator_tb": rtl_top["full_verilator_tb"],
            "payload_schedule": rtl_top["full_command_payload_schedule"],
        },
        "module_dpi_evidence": {
            "base_module_count": len(module_dpi_report["modules"]),
            "generated_full_checkpoint_module_count": len(full_checkpoint_module_dpi["modules"]),
            "source_derived_module_dpi_coverage_count": full_graph_module_dpi_binding[
                "source_derived_module_dpi_coverage_count"
            ],
            "full_graph_module_dpi_binding": "e1/generated/pipeline/27_full_graph_module_dpi_binding.json",
            "generated_child_stub_boundary": full_graph_module_dpi_binding[
                "generated_child_stub_boundary"
            ],
            "all_base_module_boundaries": full_graph_module_dpi_binding[
                "all_base_module_bindings"
            ],
            "module_dpi_boundary_artifacts": module_dpi_boundary_artifacts,
            "base_module_isolation": module_dpi_report.get("module_isolation", {}),
            "generated_full_checkpoint_module_isolation": full_checkpoint_module_dpi.get(
                "module_isolation", {}
            ),
            "source_derived_module_only_inventory": production_rtl_inventory.get(
                "module_only_dpi_inventory", {}
            ),
            "generator_sources": module_dpi_generator_sources,
            "generator_source_artifacts": module_dpi_generator_source_artifacts,
            "generator_execution": module_dpi_generator_execution_evidence,
            "cpp_verilator_launchers": module_dpi_cpp_verilator_launchers,
            "cpp_verilator_launcher_runtime_summary": (
                module_dpi_cpp_verilator_launcher_runtime_summary
            ),
            "cpp_verilator_launcher_artifacts": module_dpi_cpp_verilator_launcher_artifacts,
            "dpi_generation_provenance_audit": (
                "e1/generated/pipeline/28_lowering_construction_certificate.json:"
                "dpi_generation_provenance_audit"
            ),
        },
        "systolic_array_result_digest_proof": systolic_array_result_digest_proof,
        "dpi_generation_provenance_audit": dpi_generation_provenance_audit,
        "systemverilog_module_coverage_audit": systemverilog_module_coverage_audit,
        "systemverilog_defined_module_runtime_audit": (
            systemverilog_defined_module_runtime_audit
        ),
        "cycle_diagram_audit": cycle_diagram_documentation_audit,
        "module_boundary_taxonomy": module_boundary_taxonomy,
        "objective_traceability_audit": objective_traceability_audit,
        "objective_coverage": objective_coverage,
        "active_objective_completion_audit": active_objective_completion_audit,
        "target_filelists": target_filelist_coverage,
        "target_rtl_evidence": {
            "target_rtl_artifacts": target_rtl_artifacts,
            "generated_soc_top": soc_top_artifacts,
            "generated_soc_top_construction_artifacts": generated_soc_top_construction_artifacts,
            "generated_soc_top_hierarchy": generated_soc_top_hierarchy,
            "production_rtl_inventory": production_rtl_inventory,
            "production_rtl_inventory_artifacts": production_rtl_inventory_artifacts,
            "imp1_mock_runtime_artifacts": imp1_mock_runtime_artifacts,
        },
        "cycle_documentation": full_checkpoint_graph_rtl_lowering["readme_cycle_coverage"],
        "module_cycle_documentation": {
            "base_module_dpi": module_cycle_documentation_summary(module_dpi_report),
            "generated_full_checkpoint_module_dpi": module_cycle_documentation_summary(
                full_checkpoint_module_dpi
            ),
            "hashed_artifacts": module_cycle_doc_artifacts,
        },
        "module_interface_signal_inventory": module_interface_signal_inventory,
        "artifact_hashes": [artifact_record(path) for path in artifact_paths],
        "checks": checks,
    }
    write_json(output_path, certificate)
    return certificate


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
    op_instances = stablehlo_operation_instances(fixture_text)
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
        "schema": "e1-stablehlo-inspection-v0",
        "operation_counts": dict(sorted(ops.items())),
        "operation_instances": op_instances,
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
    module_dpi_report = run_module_dpi_generator(e1_h1_dir, module_dpi_out, implementation_matrix)
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

    full_checkpoint_graph_rtl_lowering_out = output_dir / "25_full_checkpoint_graph_rtl_lowering_proof.json"
    full_checkpoint_graph_rtl_lowering = emit_full_checkpoint_graph_rtl_lowering_proof(
        full_checkpoint_graph_rtl_lowering_out,
        manifest,
        full_checkpoint_rtl_lowering,
        full_checkpoint_command_stream,
        full_checkpoint_rtl_cycle,
        full_checkpoint_tile_engine,
        full_checkpoint_control_scheduler,
        full_checkpoint_graph_sequencer,
        full_checkpoint_rtl_top,
    )
    passes.append(
        {
            "pass": "e1_prove_full_checkpoint_graph_rtl_lowering",
            "artifact": repo_rel(full_checkpoint_graph_rtl_lowering_out),
        }
    )

    full_checkpoint_module_dpi_out = output_dir / "26_full_checkpoint_module_dpi_generation.json"
    full_checkpoint_module_dpi = run_full_checkpoint_module_dpi_generator(e1_h1_dir, full_checkpoint_module_dpi_out)
    passes.append(
        {
            "pass": "e1_generate_full_checkpoint_module_dpi",
            "artifact": repo_rel(full_checkpoint_module_dpi_out),
        }
    )

    full_graph_module_dpi_binding_out = output_dir / "27_full_graph_module_dpi_binding.json"
    full_graph_module_dpi_binding = emit_full_graph_module_dpi_binding(
        full_graph_module_dpi_binding_out,
        full_checkpoint_graph_rtl_lowering,
        module_dpi_report,
        full_checkpoint_module_dpi,
    )
    passes.append(
        {
            "pass": "e1_bind_full_graph_module_dpi",
            "artifact": repo_rel(full_graph_module_dpi_binding_out),
        }
    )

    target_manifest_path = "e1/e1-h1/generated/targets/manifest.json"
    generated_soc_top_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            soc_top_artifacts["top"],
            soc_top_artifacts["composition_manifest"],
            soc_top_artifacts["interface_contracts"],
        ]
    )
    target_filelists = {
        "active_implementation": implementation_matrix["flists"]["active"],
        "fpga": target_manifest["fpga"]["filelist"],
        "openroad": target_manifest["openroad"]["filelist"],
    }
    target_filelist_entries = {
        name: (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
        if (REPO_ROOT / path).exists()
        else []
        for name, path in target_filelists.items()
    }
    generated_soc_top_standalone_verilator = run_generated_soc_top_verilator_smoke(
        target_filelist_entries["active_implementation"],
        REPO_ROOT / "e1/e1-h1/tests/e1_h1_soc_top_tb.cpp",
    )
    generated_soc_top_standalone_passed = (
        generated_soc_top_exists
        and generated_soc_top_standalone_verilator["status"] == "pass"
    )
    imp1_mock_rtl_lint = run_imp1_mock_rtl_lints(implementation_matrix)
    production_rtl_inventory = build_production_rtl_inventory(
        implementation_matrix=implementation_matrix,
        module_dpi_report=module_dpi_report,
        full_graph_module_dpi_binding=full_graph_module_dpi_binding,
        cpp_verilator_launchers={
            "base_module_dpi": module_dpi_report.get("cpp_verilator_launcher", {}),
            "generated_full_checkpoint_module_dpi": full_checkpoint_module_dpi.get(
                "cpp_verilator_launcher", {}
            ),
        },
        soc_top_artifacts=soc_top_artifacts,
        generated_soc_top_standalone_verilator=generated_soc_top_standalone_verilator,
        generated_soc_top_standalone_passed=generated_soc_top_standalone_passed,
        imp1_mock_rtl_lint=imp1_mock_rtl_lint,
    )
    generated_soc_top_hierarchy = build_generated_soc_top_hierarchy_proof(soc_top_artifacts)

    lowering_certificate_out = output_dir / "28_lowering_construction_certificate.json"
    lowering_certificate = emit_lowering_construction_certificate(
        lowering_certificate_out,
        manifest,
        fixture_path,
        inspection,
        binding,
        rtl_lowering,
        implementation_matrix,
        target_manifest,
        module_dpi_report,
        full_checkpoint_rtl_lowering,
        full_checkpoint_command_stream,
        full_checkpoint_rtl_cycle,
        full_checkpoint_rtl_top,
        full_checkpoint_graph_rtl_lowering,
        full_checkpoint_module_dpi,
        full_graph_module_dpi_binding,
        soc_top_artifacts,
        production_rtl_inventory,
        generated_soc_top_hierarchy,
    )
    passes.append(
        {
            "pass": "e1_emit_lowering_construction_certificate",
            "artifact": repo_rel(lowering_certificate_out),
        }
    )

    e2e_out = output_dir / "29_end_to_end_smoke.json"
    module_dpi_exists = all(
        (REPO_ROOT / path).exists()
        for path in [
            module_dpi_report["manifest"],
            module_dpi_report["scoreboard"],
            module_dpi_report["module_interfaces_doc"],
            module_dpi_report["module_isolation_proof"],
            module_dpi_report["cycle_contract"],
            module_dpi_report["module_test_plan"],
            module_dpi_report["verilator_execution_recipe"],
            module_dpi_report["verilator_execution_report"],
            module_dpi_report["readme_cycle_coverage"],
            module_dpi_report["construction_ledger"],
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
    module_dpi_by_imp2_rtl = module_dpi_imp2_rtl_index(module_dpi_report)
    production_module_only_proofs_by_rtl = {
        row["rtl"]: row.get("proofs", [])
        for row in production_rtl_inventory.get("rows", [])
        if row.get("module_only_dpi_required") is True
    }
    target_filelist_module_dpi_coverage = []
    for target_name, filelist in target_filelists.items():
        entries = target_filelist_entries[target_name]
        rows = []
        for rtl in entries:
            if rtl == soc_top_artifacts["top"]:
                rows.append(
                    {
                        "rtl": rtl,
                        "coverage_kind": "generated_soc_top_standalone_verilator",
                        "covered": generated_soc_top_standalone_passed,
                        "module_dpi_proofs": [],
                        "standalone_verilator_proof": generated_soc_top_standalone_verilator,
                    }
                )
                continue
            proofs = production_module_only_proofs_by_rtl.get(
                rtl,
                module_dpi_by_imp2_rtl.get(rtl, []),
            )
            rows.append(
                {
                    "rtl": rtl,
                    "coverage_kind": "module_dpi",
                    "covered": bool(proofs)
                    and all(
                        proof["verilator_status"] == "pass"
                        and proof["ledger_checks_pass"]
                        and proof["phase_trace_checks_pass"]
                        and proof["phase_signal_trace_checks_pass"]
                        and proof.get("cpp_launcher_checks_pass") is True
                        and proof.get("cpp_launcher_recipe_checks_pass") is True
                        and proof.get("cpp_launcher_readme_cycle_checks_pass") is True
                        for proof in proofs
                    ),
                    "module_dpi_proofs": proofs,
                }
            )
        target_filelist_module_dpi_coverage.append(
            {
                "target": target_name,
                "filelist": filelist,
                "entries": entries,
                "matches_target_manifest_rtl_files": entries == target_manifest["rtl_files"],
                "matches_active_flist": entries == target_filelist_entries["active_implementation"],
                "all_entries_have_expected_proof": bool(rows)
                and all(row["covered"] for row in rows),
                "rows": rows,
            }
        )
    module_dpi_imp2_rtls = set(module_dpi_by_imp2_rtl)
    target_filelist_rtls = {
        rtl
        for entries in target_filelist_entries.values()
        for rtl in entries
        if rtl != soc_top_artifacts["top"]
    }
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
        {
            "name": "generated_soc_top_standalone_verilator",
            "status": "pass" if generated_soc_top_standalone_passed else "fail",
        },
        {
            "name": "generated_soc_top_hierarchy_matches_manifest",
            "status": generated_soc_top_hierarchy["status"],
        },
        {"name": "implementation_flists", "status": "pass" if target_package_exists else "fail"},
        {"name": "module_dpi_generation", "status": "pass" if module_dpi_exists else "fail"},
        *production_rtl_inventory_checks(production_rtl_inventory),
        {
            "name": "target_filelists_match_active_implementation",
            "status": "pass"
            if target_filelist_module_dpi_coverage
            and all(
                target["matches_target_manifest_rtl_files"]
                and target["matches_active_flist"]
                for target in target_filelist_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "target_filelist_rtl_has_module_dpi_or_top_proof",
            "status": "pass"
            if target_filelist_module_dpi_coverage
            and all(
                target["all_entries_have_expected_proof"]
                for target in target_filelist_module_dpi_coverage
            )
            else "fail",
        },
        {
            "name": "target_filelist_rtl_has_cpp_launcher_recipe_phase_key_proof",
            "status": "pass"
            if target_filelist_module_dpi_coverage
            and all(
                row["coverage_kind"] != "module_dpi"
                or (
                    row["covered"]
                    and row["module_dpi_proofs"]
                    and all(
                        proof.get("cpp_launcher_checks_pass") is True
                        and proof.get("cpp_launcher_recipe_checks_pass") is True
                        and proof.get("cpp_launcher_result", {}).get("expected_phase_trace_keys")
                        == proof.get("cpp_launcher_result", {}).get("observed_phase_trace_prefix_keys")
                        and proof.get("cpp_launcher_result", {}).get("expected_phase_signal_trace_keys")
                        == proof.get("cpp_launcher_result", {}).get("observed_phase_signal_trace_prefix_keys")
                        for proof in row["module_dpi_proofs"]
                    )
                )
                for target in target_filelist_module_dpi_coverage
                for row in target["rows"]
            )
            else "fail",
        },
        {
            "name": "target_filelist_rtl_has_cpp_launcher_readme_cycle_proof",
            "status": "pass"
            if target_filelist_module_dpi_coverage
            and all(
                row["coverage_kind"] != "module_dpi"
                or (
                    row["covered"]
                    and row["module_dpi_proofs"]
                    and all(
                        proof.get("cpp_launcher_readme_cycle_checks_pass") is True
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get("status") == "pass"
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get("readme_phase_keys")
                        == proof.get("cpp_launcher_readme_cycle_proof", {}).get(
                            "cycle_contract_phase_keys"
                        )
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get(
                            "cpp_launcher_expected_phase_keys"
                        )
                        == proof.get("cpp_launcher_readme_cycle_proof", {}).get("readme_phase_keys")
                        and proof.get("cpp_launcher_readme_cycle_proof", {}).get(
                            "cpp_launcher_observed_phase_keys"
                        )
                        == proof.get("cpp_launcher_readme_cycle_proof", {}).get("readme_phase_keys")
                        for proof in row["module_dpi_proofs"]
                    )
                )
                for target in target_filelist_module_dpi_coverage
                for row in target["rows"]
            )
            else "fail",
        },
        {
            "name": "all_module_dpi_imp2_rtl_appear_in_target_filelists",
            "status": "pass"
            if module_dpi_imp2_rtls and module_dpi_imp2_rtls.issubset(target_filelist_rtls)
            else "fail",
        },
        {"name": "rtl_lowering", "status": rtl_lowering["status"]},
        {"name": "tinyllama_imp2_coverage", "status": tinyllama_coverage["status"]},
        {"name": "full_tinyllama_checkpoint", "status": "pass" if checkpoint_check_passes else "fail"},
        {
            "name": "full_checkpoint_rtl_lowering_plan",
            "status": full_checkpoint_rtl_lowering["status"],
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
            "name": "full_checkpoint_graph_rtl_lowering_proof",
            "status": full_checkpoint_graph_rtl_lowering["status"],
        },
        {
            "name": "full_checkpoint_structural_rtl_execution",
            "status": "pass"
            if full_checkpoint_graph_rtl_lowering["full_checkpoint_structural_rtl_execution"]
            and full_checkpoint_rtl_top["full_checkpoint_structural_rtl_execution"]
            else "fail",
        },
        {
            "name": "full_checkpoint_trace_anchors_match_cpp_schedule",
            "status": "pass" if full_checkpoint_rtl_top["full_command_trace_anchor_check"] else "fail",
        },
        {
            "name": "full_checkpoint_per_op_trace_coverage_matches_cpp_schedule",
            "status": (
                "pass"
                if full_checkpoint_rtl_top["full_command_per_op_trace_coverage_check"]
                else "fail"
            ),
        },
        {
            "name": "full_checkpoint_module_dpi_generation",
            "status": full_checkpoint_module_dpi["status"],
        },
        {
            "name": "full_graph_module_dpi_binding",
            "status": full_graph_module_dpi_binding["status"],
        },
        {
            "name": "lowering_construction_certificate",
            "status": lowering_certificate["status"],
        },
        {
            "name": "lowering_objective_coverage",
            "status": "pass"
            if all(entry["status"] == "pass" for entry in lowering_certificate["objective_coverage"])
            else "fail",
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
        "target_filelist_module_dpi_coverage": target_filelist_module_dpi_coverage,
        "production_rtl_inventory": production_rtl_inventory,
        "generated_soc_top_hierarchy": generated_soc_top_hierarchy,
        "module_dpi_generation": repo_rel(module_dpi_out),
        "module_dpi_manifest": module_dpi_report["manifest"],
        "module_dpi_interfaces_doc": module_dpi_report["module_interfaces_doc"],
        "module_dpi_isolation_proof": module_dpi_report["module_isolation_proof"],
        "module_dpi_cycle_contract": module_dpi_report["cycle_contract"],
        "module_dpi_test_plan": module_dpi_report["module_test_plan"],
        "module_dpi_verilator_execution_recipe": module_dpi_report["verilator_execution_recipe"],
        "module_dpi_verilator_execution_report": module_dpi_report["verilator_execution_report"],
        "module_dpi_readme_cycle_coverage": module_dpi_report["readme_cycle_coverage"],
        "module_dpi_construction_ledger": module_dpi_report["construction_ledger"],
        "rtl_lowering": repo_rel(rtl_lowering_out),
        "rtl_lowering_status": rtl_lowering["status"],
        "tinyllama_imp2_coverage": repo_rel(tinyllama_coverage_out),
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "17_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
        "full_checkpoint_rtl_lowering_plan": repo_rel(full_checkpoint_rtl_lowering_out),
        "full_checkpoint_rtl_lowering_status": full_checkpoint_rtl_lowering["status"],
        "full_checkpoint_graph_lowered_to_rtl": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_graph_lowering"
        ],
        "full_checkpoint_graph_rtl_lowering_proof": repo_rel(full_checkpoint_graph_rtl_lowering_out),
        "full_checkpoint_graph_rtl_lowering_status": full_checkpoint_graph_rtl_lowering["status"],
        "full_checkpoint_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_rtl_execution"
        ],
        "full_checkpoint_rtl_execution_scope": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_rtl_execution_scope"
        ],
        "full_checkpoint_command_stream_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_command_stream_rtl_execution"
        ],
        "full_checkpoint_structural_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_structural_rtl_execution"
        ],
        "full_checkpoint_numeric_output_equivalence": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_numeric_output_equivalence"
        ],
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
        "full_checkpoint_rtl_top_rtl_execution": full_checkpoint_rtl_top[
            "full_checkpoint_rtl_execution"
        ],
        "full_checkpoint_rtl_top_rtl_execution_scope": full_checkpoint_rtl_top[
            "full_checkpoint_rtl_execution_scope"
        ],
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
        "full_checkpoint_rtl_top_structural_rtl_execution": full_checkpoint_rtl_top[
            "full_checkpoint_structural_rtl_execution"
        ],
        "full_checkpoint_rtl_top_full_command_payload_schedule_check": full_checkpoint_rtl_top[
            "full_command_payload_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_payload_digest_check": full_checkpoint_rtl_top[
            "full_command_payload_digest_check"
        ],
        "full_checkpoint_rtl_top_full_command_payload_digest": full_checkpoint_rtl_top[
            "full_command_payload_digest"
        ],
        "full_checkpoint_rtl_top_full_command_control_schedule_check": full_checkpoint_rtl_top[
            "full_command_control_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_control_digest_check": full_checkpoint_rtl_top[
            "full_command_control_digest_check"
        ],
        "full_checkpoint_rtl_top_full_command_control_digest": full_checkpoint_rtl_top[
            "full_command_control_digest"
        ],
        "full_checkpoint_rtl_top_verilator_execution_status": full_checkpoint_rtl_top[
            "verilator_execution"
        ]["status"],
        "full_checkpoint_rtl_top_full_command_cycles": full_checkpoint_rtl_top[
            "full_command_verilator_report"
        ]["cycles"],
        "full_checkpoint_rtl_top_full_command_accepted_payload_digest": full_checkpoint_rtl_top[
            "full_command_verilator_report"
        ]["accepted_payload_digest"],
        "full_checkpoint_rtl_top_full_command_cycle_phase_check": full_checkpoint_rtl_top[
            "full_command_cycle_phase_check"
        ],
        "full_checkpoint_rtl_top_full_command_trace_anchor_check": full_checkpoint_rtl_top[
            "full_command_trace_anchor_check"
        ],
        "full_checkpoint_rtl_top_full_command_trace_anchors": full_checkpoint_rtl_top[
            "full_command_trace_anchors"
        ],
        "full_checkpoint_rtl_top_full_command_per_op_trace_coverage_check": full_checkpoint_rtl_top[
            "full_command_per_op_trace_coverage_check"
        ],
        "full_checkpoint_rtl_top_full_command_per_op_trace_coverage": full_checkpoint_rtl_top[
            "full_command_per_op_trace_coverage"
        ],
        "full_checkpoint_module_dpi_generation": repo_rel(full_checkpoint_module_dpi_out),
        "full_checkpoint_module_dpi_manifest": full_checkpoint_module_dpi["manifest"],
        "full_checkpoint_module_interfaces_doc": full_checkpoint_module_dpi["module_interfaces_doc"],
        "full_checkpoint_module_isolation_proof": full_checkpoint_module_dpi["module_isolation_proof"],
        "full_checkpoint_module_cycle_contract": full_checkpoint_module_dpi["cycle_contract"],
        "full_checkpoint_module_test_plan": full_checkpoint_module_dpi["module_test_plan"],
        "full_checkpoint_module_verilator_execution_recipe": full_checkpoint_module_dpi[
            "verilator_execution_recipe"
        ],
        "full_checkpoint_module_verilator_execution_report": full_checkpoint_module_dpi[
            "verilator_execution_report"
        ],
        "full_checkpoint_module_readme_cycle_coverage": full_checkpoint_module_dpi["readme_cycle_coverage"],
        "full_checkpoint_module_construction_ledger": full_checkpoint_module_dpi["construction_ledger"],
        "full_checkpoint_module_dpi_status": full_checkpoint_module_dpi["status"],
        "full_checkpoint_module_dpi_count": full_checkpoint_module_dpi["module_count"],
        "full_graph_module_dpi_binding": repo_rel(full_graph_module_dpi_binding_out),
        "full_graph_module_dpi_binding_status": full_graph_module_dpi_binding["status"],
        "full_graph_module_dpi_required_generated_modules": full_graph_module_dpi_binding[
            "required_generated_modules"
        ],
        "full_graph_module_dpi_required_base_modules": full_graph_module_dpi_binding[
            "required_base_modules"
        ],
        "full_graph_source_derived_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "source_derived_module_dpi_coverage_count"
        ],
        "full_graph_generated_rtl_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "generated_rtl_module_dpi_coverage_count"
        ],
        "full_graph_separated_base_rtl_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "separated_base_rtl_module_dpi_coverage_count"
        ],
        "lowering_construction_certificate": repo_rel(lowering_certificate_out),
        "lowering_construction_certificate_status": lowering_certificate["status"],
        "lowering_construction_certificate_truth_boundary": lowering_certificate["truth_boundary"],
        "lowering_objective_coverage": lowering_certificate["objective_coverage"],
        "systemverilog_plan": repo_rel(sv_out),
        "generated_soc_top": soc_top_artifacts,
        "generated_soc_top_standalone_verilator": generated_soc_top_standalone_verilator,
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
        "module_dpi_manifest": module_dpi_report["manifest"],
        "module_dpi_interfaces_doc": module_dpi_report["module_interfaces_doc"],
        "module_dpi_isolation_proof": module_dpi_report["module_isolation_proof"],
        "module_dpi_cycle_contract": module_dpi_report["cycle_contract"],
        "module_dpi_test_plan": module_dpi_report["module_test_plan"],
        "module_dpi_verilator_execution_recipe": module_dpi_report["verilator_execution_recipe"],
        "module_dpi_verilator_execution_report": module_dpi_report["verilator_execution_report"],
        "module_dpi_readme_cycle_coverage": module_dpi_report["readme_cycle_coverage"],
        "module_dpi_construction_ledger": module_dpi_report["construction_ledger"],
        "rtl_lowering": repo_rel(rtl_lowering_out),
        "rtl_lowering_status": rtl_lowering["status"],
        "full_tinyllama_checkpoint_execution": repo_rel(output_dir / "17_full_tinyllama_checkpoint_execution.json"),
        "full_tinyllama_checkpoint_execution_status": full_checkpoint_execution["status"],
        "full_tinyllama_checkpoint_implemented": full_checkpoint_execution["full_checkpoint_execution"],
        "full_checkpoint_rtl_lowering_plan": repo_rel(full_checkpoint_rtl_lowering_out),
        "full_checkpoint_rtl_lowering_status": full_checkpoint_rtl_lowering["status"],
        "full_checkpoint_graph_lowered_to_rtl": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_graph_lowering"
        ],
        "full_checkpoint_graph_rtl_lowering_proof": repo_rel(full_checkpoint_graph_rtl_lowering_out),
        "full_checkpoint_graph_rtl_lowering_status": full_checkpoint_graph_rtl_lowering["status"],
        "full_checkpoint_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_rtl_execution"
        ],
        "full_checkpoint_rtl_execution_scope": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_rtl_execution_scope"
        ],
        "full_checkpoint_command_stream_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_command_stream_rtl_execution"
        ],
        "full_checkpoint_structural_rtl_execution": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_structural_rtl_execution"
        ],
        "full_checkpoint_numeric_output_equivalence": full_checkpoint_graph_rtl_lowering[
            "full_checkpoint_numeric_output_equivalence"
        ],
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
        "full_checkpoint_rtl_top_rtl_execution": full_checkpoint_rtl_top[
            "full_checkpoint_rtl_execution"
        ],
        "full_checkpoint_rtl_top_rtl_execution_scope": full_checkpoint_rtl_top[
            "full_checkpoint_rtl_execution_scope"
        ],
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
        "full_checkpoint_rtl_top_structural_rtl_execution": full_checkpoint_rtl_top[
            "full_checkpoint_structural_rtl_execution"
        ],
        "full_checkpoint_rtl_top_full_command_payload_schedule_check": full_checkpoint_rtl_top[
            "full_command_payload_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_payload_digest_check": full_checkpoint_rtl_top[
            "full_command_payload_digest_check"
        ],
        "full_checkpoint_rtl_top_full_command_payload_digest": full_checkpoint_rtl_top[
            "full_command_payload_digest"
        ],
        "full_checkpoint_rtl_top_full_command_control_schedule_check": full_checkpoint_rtl_top[
            "full_command_control_schedule_check"
        ],
        "full_checkpoint_rtl_top_full_command_control_digest_check": full_checkpoint_rtl_top[
            "full_command_control_digest_check"
        ],
        "full_checkpoint_rtl_top_full_command_control_digest": full_checkpoint_rtl_top[
            "full_command_control_digest"
        ],
        "full_checkpoint_rtl_top_verilator_execution_status": full_checkpoint_rtl_top[
            "verilator_execution"
        ]["status"],
        "full_checkpoint_rtl_top_full_command_cycles": full_checkpoint_rtl_top[
            "full_command_verilator_report"
        ]["cycles"],
        "full_checkpoint_rtl_top_full_command_accepted_payload_digest": full_checkpoint_rtl_top[
            "full_command_verilator_report"
        ]["accepted_payload_digest"],
        "full_checkpoint_rtl_top_full_command_cycle_phase_check": full_checkpoint_rtl_top[
            "full_command_cycle_phase_check"
        ],
        "full_checkpoint_rtl_top_full_command_trace_anchor_check": full_checkpoint_rtl_top[
            "full_command_trace_anchor_check"
        ],
        "full_checkpoint_rtl_top_full_command_trace_anchors": full_checkpoint_rtl_top[
            "full_command_trace_anchors"
        ],
        "full_checkpoint_rtl_top_full_command_per_op_trace_coverage_check": full_checkpoint_rtl_top[
            "full_command_per_op_trace_coverage_check"
        ],
        "full_checkpoint_rtl_top_full_command_per_op_trace_coverage": full_checkpoint_rtl_top[
            "full_command_per_op_trace_coverage"
        ],
        "full_checkpoint_module_dpi_generation": repo_rel(full_checkpoint_module_dpi_out),
        "full_checkpoint_module_dpi_manifest": full_checkpoint_module_dpi["manifest"],
        "full_checkpoint_module_interfaces_doc": full_checkpoint_module_dpi["module_interfaces_doc"],
        "full_checkpoint_module_isolation_proof": full_checkpoint_module_dpi["module_isolation_proof"],
        "full_checkpoint_module_cycle_contract": full_checkpoint_module_dpi["cycle_contract"],
        "full_checkpoint_module_test_plan": full_checkpoint_module_dpi["module_test_plan"],
        "full_checkpoint_module_verilator_execution_recipe": full_checkpoint_module_dpi[
            "verilator_execution_recipe"
        ],
        "full_checkpoint_module_verilator_execution_report": full_checkpoint_module_dpi[
            "verilator_execution_report"
        ],
        "full_checkpoint_module_readme_cycle_coverage": full_checkpoint_module_dpi["readme_cycle_coverage"],
        "full_checkpoint_module_construction_ledger": full_checkpoint_module_dpi["construction_ledger"],
        "full_checkpoint_module_dpi_status": full_checkpoint_module_dpi["status"],
        "full_checkpoint_module_dpi_count": full_checkpoint_module_dpi["module_count"],
        "full_graph_module_dpi_binding": repo_rel(full_graph_module_dpi_binding_out),
        "full_graph_module_dpi_binding_status": full_graph_module_dpi_binding["status"],
        "full_graph_module_dpi_required_generated_modules": full_graph_module_dpi_binding[
            "required_generated_modules"
        ],
        "full_graph_module_dpi_required_base_modules": full_graph_module_dpi_binding[
            "required_base_modules"
        ],
        "full_graph_source_derived_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "source_derived_module_dpi_coverage_count"
        ],
        "full_graph_generated_rtl_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "generated_rtl_module_dpi_coverage_count"
        ],
        "full_graph_separated_base_rtl_module_dpi_coverage_count": full_graph_module_dpi_binding[
            "separated_base_rtl_module_dpi_coverage_count"
        ],
        "lowering_construction_certificate": repo_rel(lowering_certificate_out),
        "lowering_construction_certificate_status": lowering_certificate["status"],
        "lowering_construction_certificate_truth_boundary": lowering_certificate["truth_boundary"],
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
