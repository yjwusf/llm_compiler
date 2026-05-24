#!/usr/bin/env python3
"""Run generated module-DPI Verilator test plans and write a proof report."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
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


def verilator_command(verilator: str, module: dict[str, Any], obj_dir: Path) -> list[str]:
    plan = module["verilator"]
    return [
        verilator,
        *plan["fixed_args"],
        "--top-module",
        plan["top_module"],
        "-Mdir",
        str(obj_dir),
        "-f",
        plan["flist"],
        plan["scoreboard"],
        plan["main"],
    ]


def command_for_report(module: dict[str, Any], obj_dir_name: str) -> list[str]:
    plan = module["verilator"]
    return [
        "verilator",
        *plan["fixed_args"],
        "--top-module",
        plan["top_module"],
        "-Mdir",
        f"<build-root>/{obj_dir_name}",
        "-f",
        plan["flist"],
        plan["scoreboard"],
        plan["main"],
    ]


def run_executable_for_report(module: dict[str, Any], obj_dir_name: str) -> str:
    return f"<build-root>/{obj_dir_name}/{module['verilator']['run_executable']}"


def validate_recipe(
    test_plan: dict[str, Any],
    test_plan_path: Path,
    report_path: Path,
    suite: str,
    recipe_path: Path | None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]], bool]:
    if recipe_path is None:
        return None, [], True

    recipe = load_json(recipe_path)
    checks: list[dict[str, str]] = [{"name": "cpp_execution_recipe_loaded", "status": "pass"}]
    recipe_modules = {
        module.get("name"): module for module in recipe.get("modules", []) if module.get("name")
    }
    plan_modules = {module["name"]: module for module in test_plan.get("modules", [])}
    metadata_matches = (
        recipe.get("runner") == "e1/tools/run_module_dpi_verilator.py"
        and recipe.get("suite") == suite
        and recipe.get("test_plan") == repo_rel(test_plan_path)
        and recipe.get("report") == repo_rel(report_path)
    )
    module_set_matches = set(recipe_modules) == set(plan_modules)
    fields_match = True
    commands_match = True
    for name, module in plan_modules.items():
        recipe_module = recipe_modules.get(name, {})
        plan = module["verilator"]
        obj_dir_name = f"obj_{suite}_{name}"
        fields_match = fields_match and (
            recipe_module.get("scope") == module["scope"]
            and recipe_module.get("top_module") == plan["top_module"]
            and recipe_module.get("dut_module") == plan["dut_module"]
            and recipe_module.get("flist") == plan["flist"]
            and recipe_module.get("scoreboard") == plan["scoreboard"]
            and recipe_module.get("main") == plan["main"]
            and recipe_module.get("expected_stdout_markers")
            == plan["expected_stdout_markers"]
        )
        commands_match = commands_match and (
            recipe_module.get("build_command") == command_for_report(module, obj_dir_name)
            and recipe_module.get("run_executable")
            == run_executable_for_report(module, obj_dir_name)
        )

    checks.extend(
        [
            {
                "name": "cpp_execution_recipe_metadata_matches_runner",
                "status": "pass" if metadata_matches else "fail",
            },
            {
                "name": "cpp_execution_recipe_modules_match_test_plan",
                "status": "pass" if module_set_matches and fields_match else "fail",
            },
            {
                "name": "cpp_execution_recipe_commands_match_runner",
                "status": "pass" if module_set_matches and commands_match else "fail",
            },
        ]
    )
    return recipe, checks, all(check["status"] == "pass" for check in checks)


def tail_lines(text: str, limit: int = 20) -> list[str]:
    lines = text.splitlines()
    return lines[-limit:]


def run_plan(
    test_plan_path: Path,
    report_path: Path,
    suite: str,
    build_root: Path | None,
    recipe_path: Path | None,
) -> int:
    test_plan = load_json(test_plan_path)
    recipe, recipe_checks, recipe_ok = validate_recipe(
        test_plan, test_plan_path, report_path, suite, recipe_path
    )
    if not recipe_ok:
        report = {
            "schema": "e1-module-dpi-verilator-execution-report-v0",
            "suite": suite,
            "status": "recipe_mismatch",
            "runner": "e1/tools/run_module_dpi_verilator.py",
            "runner_kind": "actual_verilator_build_and_run",
            "test_plan": repo_rel(test_plan_path),
            "execution_recipe": repo_rel(recipe_path) if recipe_path is not None else None,
            "recipe_schema": recipe.get("schema") if recipe is not None else None,
            "module_count": len(test_plan.get("modules", [])),
            "modules": [],
            "checks": recipe_checks,
        }
        write_json(report_path, report)
        return 1

    verilator = shutil.which("verilator")
    modules: list[dict[str, Any]] = []
    checks: list[dict[str, str]] = list(recipe_checks)

    if verilator is None:
        report = {
            "schema": "e1-module-dpi-verilator-execution-report-v0",
            "suite": suite,
            "status": "missing_verilator",
            "runner": "e1/tools/run_module_dpi_verilator.py",
            "test_plan": repo_rel(test_plan_path),
            "execution_recipe": repo_rel(recipe_path) if recipe_path is not None else None,
            "recipe_schema": recipe.get("schema") if recipe is not None else None,
            "module_count": len(test_plan.get("modules", [])),
            "modules": [],
            "checks": [*recipe_checks, {"name": "verilator_available", "status": "fail"}],
        }
        write_json(report_path, report)
        return 1

    with tempfile.TemporaryDirectory(prefix=f"e1_{suite}_module_dpi_") as tmp:
        root = build_root if build_root is not None else Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        for module in test_plan["modules"]:
            obj_dir_name = f"obj_{suite}_{module['name']}"
            obj_dir = root / obj_dir_name
            build = subprocess.run(
                verilator_command(verilator, module, obj_dir),
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            run_result: subprocess.CompletedProcess[str] | None = None
            executable = obj_dir / module["verilator"]["run_executable"]
            if build.returncode == 0:
                run_result = subprocess.run(
                    [str(executable)],
                    cwd=REPO_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )

            run_stdout = run_result.stdout if run_result is not None else ""
            observed_markers = [
                marker
                for marker in module["verilator"]["expected_stdout_markers"]
                if marker in run_stdout
            ]
            module_status = (
                "pass"
                if build.returncode == 0
                and run_result is not None
                and run_result.returncode == 0
                and observed_markers == module["verilator"]["expected_stdout_markers"]
                else "fail"
            )
            modules.append(
                {
                    "name": module["name"],
                    "scope": module["scope"],
                    "status": module_status,
                    "top_module": module["verilator"]["top_module"],
                    "dut_module": module["verilator"]["dut_module"],
                    "flist": module["verilator"]["flist"],
                    "scoreboard": module["verilator"]["scoreboard"],
                    "main": module["verilator"]["main"],
                    "build_command": command_for_report(module, obj_dir_name),
                    "run_executable": run_executable_for_report(module, obj_dir_name),
                    "build_returncode": build.returncode,
                    "run_returncode": run_result.returncode if run_result is not None else None,
                    "expected_stdout_markers": module["verilator"]["expected_stdout_markers"],
                    "observed_stdout_markers": observed_markers,
                    "build_stdout_tail": [] if build.returncode == 0 else tail_lines(build.stdout),
                    "run_stdout_tail": []
                    if run_result is not None and run_result.returncode == 0
                    else tail_lines(run_stdout),
                }
            )

    checks.extend(
        [
            {"name": "verilator_available", "status": "pass"},
            {
                "name": "all_modules_built",
                "status": "pass"
                if all(module["build_returncode"] == 0 for module in modules)
                else "fail",
            },
            {
                "name": "all_modules_ran",
                "status": "pass"
                if all(module["run_returncode"] == 0 for module in modules)
                else "fail",
            },
            {
                "name": "all_expected_stdout_markers_observed",
                "status": "pass"
                if all(
                    module["observed_stdout_markers"] == module["expected_stdout_markers"]
                    for module in modules
                )
                else "fail",
            },
        ]
    )
    report = {
        "schema": "e1-module-dpi-verilator-execution-report-v0",
        "suite": suite,
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "runner": "e1/tools/run_module_dpi_verilator.py",
        "runner_kind": "actual_verilator_build_and_run",
        "test_plan": repo_rel(test_plan_path),
        "execution_recipe": repo_rel(recipe_path) if recipe_path is not None else None,
        "recipe_schema": recipe.get("schema") if recipe is not None else None,
        "module_count": len(modules),
        "build_root": "<build-root>",
        "modules": modules,
        "checks": checks,
    }
    write_json(report_path, report)
    return 0 if report["status"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--recipe", type=Path)
    args = parser.parse_args()

    test_plan = args.test_plan if args.test_plan.is_absolute() else REPO_ROOT / args.test_plan
    report = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    build_root = None
    if args.build_root is not None:
        build_root = args.build_root if args.build_root.is_absolute() else REPO_ROOT / args.build_root
    recipe = None
    if args.recipe is not None:
        recipe = args.recipe if args.recipe.is_absolute() else REPO_ROOT / args.recipe

    return run_plan(test_plan, report, args.suite, build_root, recipe)


if __name__ == "__main__":
    sys.exit(main())
