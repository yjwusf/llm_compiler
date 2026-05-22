#!/usr/bin/env python3
"""Generate the E1-H1 SoC top from IP manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Port:
    instance: str
    module: str
    name: str
    direction: str
    width: int
    connect: str


@dataclass(frozen=True)
class Ip:
    name: str
    module: str
    subsystem: str
    order: int
    description: str
    replaceable: bool
    rtl: str
    spec: str
    l1_5_harness: str
    module_vip: str
    perf_counters: list[str]
    parameters: dict[str, int]
    ports: list[Port]


@dataclass(frozen=True)
class RtlPort:
    name: str
    direction: str
    width: int


@dataclass(frozen=True)
class RtlModule:
    name: str
    parameters: set[str]
    ports: dict[str, RtlPort]


VALID_READY_PIPELINES: tuple[dict[str, Any], ...] = (
    {
        "name": "cpu_to_accelerator",
        "depth_key": "cpu_to_accelerator_depth",
        "description": "Control CPU command path into the systolic array.",
        "valid": "accel_cmd_valid",
        "ready": "accel_cmd_ready",
        "payload": (
            ("accel_cmd_input_addr", 32),
            ("accel_cmd_weight_addr", 32),
            ("accel_cmd_output_addr", 32),
            ("accel_cmd_rows", 16),
            ("accel_cmd_cols", 16),
            ("accel_cmd_depth", 16),
        ),
    },
    {
        "name": "array_input",
        "depth_key": "array_input_depth",
        "description": "Ingress SRAM stream path into the systolic array.",
        "valid": "array_input_valid",
        "ready": "array_input_ready",
        "payload": (
            ("array_input_data", 64),
        ),
    },
)

SIMPLE_SIGNAL_PIPELINES: tuple[dict[str, Any], ...] = (
    {
        "name": "array_output",
        "depth_key": "array_output_depth",
        "description": "Systolic-array completion and error path back to the control CPU.",
        "signals": (
            ("array_done", 1),
            ("array_error", 1),
        ),
    },
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_ips(ip_dir: Path) -> list[Ip]:
    ips: list[Ip] = []
    for path in sorted(ip_dir.glob("*.json")):
        data = load_json(path)
        if data.get("schema") != "e1-h1-ip-v0":
            raise ValueError(f"{path}: unsupported schema {data.get('schema')!r}")
        name = data["name"]
        module = data["module"]
        ports = [
            Port(
                instance=name,
                module=module,
                name=port["name"],
                direction=port["direction"],
                width=int(port["width"]),
                connect=port["connect"],
            )
            for port in data["ports"]
        ]
        ips.append(
            Ip(
                name=name,
                module=module,
                subsystem=data.get("subsystem", "ungrouped"),
                order=int(data["order"]),
                description=data.get("description", ""),
                replaceable=bool(data["replaceable"]),
                rtl=data["rtl"],
                spec=data["spec"],
                l1_5_harness=data["l1_5_harness"],
                module_vip=data["module_vip"],
                perf_counters=list(data["perf_counters"]),
                parameters={k: int(v) for k, v in data.get("parameters", {}).items()},
                ports=ports,
            )
        )
    return sorted(ips, key=lambda ip: (ip.order, ip.name))


def sv_width(width: int) -> str:
    if width == 1:
        return ""
    return f"[{width - 1}:0] "


def signal_name(connect: str) -> str:
    prefix, name = connect.split(".", 1)
    if prefix not in {"top", "net"}:
        raise ValueError(f"unsupported connection prefix in {connect!r}")
    return name


def repo_root_from_architecture(architecture_path: Path) -> Path:
    return architecture_path.resolve().parents[3]


def strip_sv_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced SystemVerilog module header")


def split_sv_comma_list(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_sv_width(range_text: str | None) -> int:
    if range_text is None:
        return 1
    match = re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", range_text.strip())
    if match is None:
        raise ValueError(f"unsupported SystemVerilog port range {range_text!r}")
    left = int(match.group(1))
    right = int(match.group(2))
    return abs(left - right) + 1


def parse_rtl_module(path: Path, module_name: str) -> RtlModule:
    text = strip_sv_comments(path.read_text(encoding="utf-8"))
    match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", text)
    if match is None:
        raise ValueError(f"{path}: missing module {module_name}")

    cursor = match.end()
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1

    parameters: set[str] = set()
    if text.startswith("#", cursor):
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            raise ValueError(f"{path}: malformed parameter block for {module_name}")
        end = find_matching_paren(text, cursor)
        parameter_body = text[cursor + 1:end]
        for declaration in split_sv_comma_list(parameter_body):
            param_match = re.search(r"\bparameter\b\s+(?:\w+\s+)*([A-Za-z_][A-Za-z0-9_]*)\s*=", declaration)
            if param_match is not None:
                parameters.add(param_match.group(1))
        cursor = end + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

    if cursor >= len(text) or text[cursor] != "(":
        raise ValueError(f"{path}: missing ANSI port list for {module_name}")
    end = find_matching_paren(text, cursor)
    port_body = text[cursor + 1:end]
    ports: dict[str, RtlPort] = {}
    for declaration in split_sv_comma_list(port_body):
        port_match = re.search(
            r"\b(input|output|inout)\b\s+"
            r"(?:(?:wire|logic|reg)\s+)?"
            r"(?:(?:signed|unsigned)\s+)?"
            r"(\[[^\]]+\])?\s*"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*$",
            declaration,
        )
        if port_match is None:
            raise ValueError(f"{path}: unsupported port declaration {declaration!r}")
        direction = port_match.group(1)
        width = parse_sv_width(port_match.group(2))
        name = port_match.group(3)
        ports[name] = RtlPort(name=name, direction=direction, width=width)

    return RtlModule(name=module_name, parameters=parameters, ports=ports)


def validate_rtl_interfaces(repo_root: Path, ips: list[Ip]) -> dict[str, dict[str, Any]]:
    cache: dict[tuple[str, str], RtlModule] = {}
    validation: dict[str, dict[str, Any]] = {}
    for ip in ips:
        rtl_path = repo_root / ip.rtl
        if not rtl_path.exists():
            raise ValueError(f"{ip.name}: missing RTL file {ip.rtl}")
        cache_key = (ip.rtl, ip.module)
        if cache_key not in cache:
            cache[cache_key] = parse_rtl_module(rtl_path, ip.module)
        rtl_module = cache[cache_key]

        missing_parameters = sorted(set(ip.parameters) - rtl_module.parameters)
        missing_ports = sorted(port.name for port in ip.ports if port.name not in rtl_module.ports)
        mismatched_ports = []
        for port in ip.ports:
            rtl_port = rtl_module.ports.get(port.name)
            if rtl_port is None:
                continue
            if rtl_port.direction != port.direction or rtl_port.width != port.width:
                mismatched_ports.append(
                    {
                        "name": port.name,
                        "manifest": {"direction": port.direction, "width": port.width},
                        "rtl": {"direction": rtl_port.direction, "width": rtl_port.width},
                    }
                )

        if missing_parameters:
            raise ValueError(f"{ip.name}: RTL module {ip.module} missing parameters {missing_parameters}")
        if missing_ports:
            raise ValueError(f"{ip.name}: RTL module {ip.module} missing ports {missing_ports}")
        if mismatched_ports:
            raise ValueError(f"{ip.name}: RTL module {ip.module} has mismatched ports {mismatched_ports}")

        validation[ip.name] = {
            "status": "pass",
            "rtl": ip.rtl,
            "module": ip.module,
            "parameter_count": len(ip.parameters),
            "port_count": len(ip.ports),
        }
    return validation


def require_ip(by_name: dict[str, Ip], name: str, kind: str) -> Ip:
    ip = by_name.get(name)
    if ip is None:
        raise ValueError(f"architecture {kind} block {name!r}: missing IP manifest")
    return ip


def check_architecture_parameters(
    ip: Ip,
    kind: str,
    expected: dict[str, int],
) -> dict[str, Any]:
    actual = {name: ip.parameters.get(name) for name in expected}
    missing = sorted(name for name, value in actual.items() if value is None)
    mismatched = {
        name: {"architecture": expected[name], "manifest": actual[name]}
        for name in expected
        if actual[name] is not None and actual[name] != expected[name]
    }
    if missing or mismatched:
        details: dict[str, Any] = {}
        if missing:
            details["missing"] = missing
        if mismatched:
            details["mismatched"] = mismatched
        raise ValueError(f"{ip.name}: architecture {kind} parameters mismatch {details}")

    return {
        "name": ip.name,
        "kind": kind,
        "status": "pass",
        "parameters": [{"name": name, "value": expected[name]} for name in sorted(expected)],
    }


def validate_architecture_bindings(arch: dict[str, Any], ips: list[Ip]) -> dict[str, Any]:
    by_name = {ip.name: ip for ip in ips}
    checks: list[dict[str, Any]] = []

    for sram in arch.get("memory", {}).get("sram", []):
        ip = require_ip(by_name, sram["name"], "SRAM")
        checks.append(
            check_architecture_parameters(
                ip,
                "sram",
                {
                    "SIZE_BYTES": int(sram["size_bytes"]),
                    "DATA_WIDTH": int(sram["data_width"]),
                    "BANKS": int(sram["banks"]),
                },
            )
        )

    accelerator = arch.get("accelerator", {})
    if accelerator.get("kind") == "systolic_array":
        ip = require_ip(by_name, "systolic_array", "accelerator")
        checks.append(
            check_architecture_parameters(
                ip,
                "systolic_array",
                {
                    "ROWS": int(accelerator["rows"]),
                    "COLS": int(accelerator["cols"]),
                    "DATA_WIDTH": int(accelerator["data_width"]),
                    "ACCUMULATOR_WIDTH": int(accelerator["accumulator_width"]),
                },
            )
        )

    return {
        "schema": "e1-h1-architecture-validation-v0",
        "source": "e1/e1-h1/config/architecture.json",
        "checks": checks,
    }


def pipeline_depths(arch: dict[str, Any]) -> dict[str, int]:
    pipeline = arch.get("pipeline", {})
    default_depth = int(pipeline.get("default_depth", 0))
    if default_depth < 0:
        raise ValueError("architecture pipeline default_depth must be non-negative")

    depths: dict[str, int] = {}
    for item in [*VALID_READY_PIPELINES, *SIMPLE_SIGNAL_PIPELINES]:
        key = item["depth_key"]
        depth = int(pipeline.get(key, default_depth))
        if depth < 0:
            raise ValueError(f"architecture pipeline {key} must be non-negative")
        depths[item["name"]] = depth
    return depths


def validate_pipeline_bindings(arch: dict[str, Any], ips: list[Ip]) -> dict[str, Any]:
    depths = pipeline_depths(arch)
    nets = collect_nets(ips)
    checks: list[dict[str, Any]] = []

    for item in VALID_READY_PIPELINES:
        expected = {
            item["valid"]: 1,
            item["ready"]: 1,
            **{name: width for name, width in item["payload"]},
        }
        missing = sorted(name for name in expected if name not in nets)
        mismatched = {
            name: {"architecture": width, "manifest": nets.get(name)}
            for name, width in expected.items()
            if name in nets and nets[name] != width
        }
        if missing or mismatched:
            details: dict[str, Any] = {}
            if missing:
                details["missing"] = missing
            if mismatched:
                details["mismatched"] = mismatched
            raise ValueError(f"{item['name']}: architecture pipeline nets mismatch {details}")

        checks.append(
            {
                "name": item["name"],
                "kind": "valid_ready_payload",
                "depth": depths[item["name"]],
                "description": item["description"],
                "valid": item["valid"],
                "ready": item["ready"],
                "payload": [{"name": name, "width": width} for name, width in item["payload"]],
                "status": "pass",
            }
        )

    for item in SIMPLE_SIGNAL_PIPELINES:
        expected = {name: width for name, width in item["signals"]}
        missing = sorted(name for name in expected if name not in nets)
        mismatched = {
            name: {"architecture": width, "manifest": nets.get(name)}
            for name, width in expected.items()
            if name in nets and nets[name] != width
        }
        if missing or mismatched:
            details = {}
            if missing:
                details["missing"] = missing
            if mismatched:
                details["mismatched"] = mismatched
            raise ValueError(f"{item['name']}: architecture pipeline nets mismatch {details}")

        checks.append(
            {
                "name": item["name"],
                "kind": "registered_signal_delay",
                "depth": depths[item["name"]],
                "description": item["description"],
                "signals": [{"name": name, "width": width} for name, width in item["signals"]],
                "status": "pass",
            }
        )

    return {
        "schema": "e1-h1-pipeline-validation-v0",
        "source": "e1/e1-h1/config/architecture.json",
        "checks": checks,
    }


def collect_top_ports(ips: list[Ip]) -> dict[str, tuple[str, int]]:
    top_ports: dict[str, tuple[str, int]] = {}
    for ip in ips:
        for port in ip.ports:
            if not port.connect.startswith("top."):
                continue
            name = signal_name(port.connect)
            existing = top_ports.get(name)
            value = (port.direction, port.width)
            if existing is not None and existing != value:
                raise ValueError(f"conflicting top port {name}: {existing} vs {value}")
            top_ports[name] = value
    return top_ports


def collect_top_port_ports(ips: list[Ip]) -> dict[str, list[Port]]:
    top_port_ports: dict[str, list[Port]] = {}
    for ip in ips:
        for port in ip.ports:
            if port.connect.startswith("top."):
                top_port_ports.setdefault(signal_name(port.connect), []).append(port)
    return top_port_ports


def validate_top_port_connectivity(ips: list[Ip]) -> None:
    for name, ports in sorted(collect_top_port_ports(ips).items()):
        drivers = [port for port in ports if port.direction == "output"]
        loads = [port for port in ports if port.direction == "input"]
        inouts = [port for port in ports if port.direction == "inout"]

        if inouts:
            if drivers or loads:
                raise ValueError(f"top port {name}: inout top ports must not mix with input/output ports")
            continue
        if drivers and loads:
            raise ValueError(f"top port {name}: cannot mix input loads and output drivers")
        if drivers and len(drivers) != 1:
            raise ValueError(f"top port {name}: expected exactly one output driver, found {len(drivers)}")
        if not drivers and not loads:
            raise ValueError(f"top port {name}: expected at least one connected endpoint")


def collect_nets(ips: list[Ip]) -> dict[str, int]:
    nets: dict[str, int] = {}
    for ip in ips:
        for port in ip.ports:
            if not port.connect.startswith("net."):
                continue
            name = signal_name(port.connect)
            existing = nets.get(name)
            if existing is not None and existing != port.width:
                raise ValueError(f"conflicting net {name}: {existing} vs {port.width}")
            nets[name] = port.width
    return nets


def collect_net_ports(ips: list[Ip]) -> dict[str, list[Port]]:
    net_ports: dict[str, list[Port]] = {}
    for ip in ips:
        for port in ip.ports:
            if port.connect.startswith("net."):
                net_ports.setdefault(signal_name(port.connect), []).append(port)
    return net_ports


def validate_net_connectivity(ips: list[Ip]) -> None:
    for name, ports in sorted(collect_net_ports(ips).items()):
        drivers = [port for port in ports if port.direction == "output"]
        loads = [port for port in ports if port.direction == "input"]
        inouts = [port for port in ports if port.direction == "inout"]

        if inouts:
            if drivers or loads or len(inouts) < 2:
                raise ValueError(
                    f"net {name}: inout nets must connect at least two inout ports and no input/output ports"
                )
            continue
        if len(drivers) != 1:
            raise ValueError(f"net {name}: expected exactly one output driver, found {len(drivers)}")
        if not loads:
            raise ValueError(f"net {name}: expected at least one input load")


def module_name(arch: dict[str, Any]) -> str:
    return f"{arch['architecture_id'].replace('-', '_')}_soc_top"


def subsystem_descriptions(arch: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: item["description"]
        for item in arch.get("soc_top", {}).get("subsystems", [])
    }


def pipeline_active_for_net(name: str, depths: dict[str, int]) -> bool:
    for item in VALID_READY_PIPELINES:
        if depths[item["name"]] == 0:
            continue
        if name in {item["valid"], item["ready"]} or name in {payload[0] for payload in item["payload"]}:
            return True
    for item in SIMPLE_SIGNAL_PIPELINES:
        if depths[item["name"]] == 0:
            continue
        if name in {signal[0] for signal in item["signals"]}:
            return True
    return False


def physical_signal_name(port: Port, depths: dict[str, int]) -> str:
    if port.connect.startswith("top."):
        return signal_name(port.connect)
    logical = signal_name(port.connect)

    for item in VALID_READY_PIPELINES:
        if depths[item["name"]] == 0:
            continue
        payload_nets = {name for name, _width in item["payload"]}
        if logical == item["valid"] or logical in payload_nets:
            return f"{logical}_src" if port.direction == "output" else f"{logical}_dst"
        if logical == item["ready"]:
            return f"{logical}_dst" if port.direction == "output" else f"{logical}_src"

    for item in SIMPLE_SIGNAL_PIPELINES:
        if depths[item["name"]] == 0:
            continue
        if logical in {name for name, _width in item["signals"]}:
            return f"{logical}_src" if port.direction == "output" else f"{logical}_dst"

    return logical


def emit_net_declarations(nets: dict[str, int], depths: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for name, width in sorted(nets.items()):
        if pipeline_active_for_net(name, depths):
            lines.append(f"  logic {sv_width(width)}{name}_src;")
            lines.append(f"  logic {sv_width(width)}{name}_dst;")
        else:
            lines.append(f"  logic {sv_width(width)}{name};")
    return lines


def emit_valid_ready_pipeline(item: dict[str, Any], depth: int) -> list[str]:
    name = item["name"]
    valid = item["valid"]
    ready = item["ready"]
    payload = list(item["payload"])
    payload_width = sum(width for _net, width in payload)
    payload_names = ", ".join(f"{net}_src" for net, _width in payload)
    payload_dst_names = ", ".join(f"{net}_dst" for net, _width in payload)
    payload_src_expr = f"{{{payload_names}}}" if len(payload) > 1 else f"{payload[0][0]}_src"
    payload_dst_expr = f"{{{payload_dst_names}}}" if len(payload) > 1 else f"{payload[0][0]}_dst"

    lines = [
        f"  // Pipeline: {name}, depth {depth} from architecture.json.",
        f"  logic {sv_width(payload_width)}{name}_payload_src;",
        f"  logic {sv_width(payload_width)}{name}_payload_dst;",
        f"  assign {name}_payload_src = {payload_src_expr};",
        f"  assign {payload_dst_expr} = {name}_payload_dst;",
    ]
    for index in range(depth):
        lines.append(f"  logic {name}_valid_q_{index};")
        lines.append(f"  logic {sv_width(payload_width)}{name}_payload_q_{index};")
    for index in range(depth + 1):
        lines.append(f"  logic {name}_ready_stage_{index};")

    lines.append(f"  assign {name}_ready_stage_{depth} = {ready}_dst;")
    for index in range(depth):
        lines.append(
            f"  assign {name}_ready_stage_{index} = !{name}_valid_q_{index} || {name}_ready_stage_{index + 1};"
        )
    lines.append(f"  assign {ready}_src = {name}_ready_stage_0;")
    lines.append(f"  assign {valid}_dst = {name}_valid_q_{depth - 1};")
    lines.append(f"  assign {name}_payload_dst = {name}_payload_q_{depth - 1};")
    lines.append("")
    lines.append("  always_ff @(posedge clk_i or negedge rst_ni) begin")
    lines.append("    if (!rst_ni) begin")
    for index in range(depth):
        lines.append(f"      {name}_valid_q_{index} <= 1'b0;")
        lines.append(f"      {name}_payload_q_{index} <= '0;")
    lines.append("    end else begin")
    for index in range(depth):
        input_valid = f"{valid}_src" if index == 0 else f"{name}_valid_q_{index - 1}"
        input_payload = f"{name}_payload_src" if index == 0 else f"{name}_payload_q_{index - 1}"
        lines.append(f"      if ({name}_ready_stage_{index}) begin")
        lines.append(f"        {name}_valid_q_{index} <= {input_valid};")
        lines.append(f"        {name}_payload_q_{index} <= {input_payload};")
        lines.append("      end")
    lines.append("    end")
    lines.append("  end")
    lines.append("")
    return lines


def emit_simple_signal_pipeline(item: dict[str, Any], depth: int) -> list[str]:
    name = item["name"]
    signals = list(item["signals"])
    lines = [f"  // Pipeline: {name}, depth {depth} from architecture.json."]
    for signal, width in signals:
        for index in range(depth):
            lines.append(f"  logic {sv_width(width)}{name}_{signal}_q_{index};")
    lines.append("")
    lines.append("  always_ff @(posedge clk_i or negedge rst_ni) begin")
    lines.append("    if (!rst_ni) begin")
    for signal, _width in signals:
        for index in range(depth):
            lines.append(f"      {name}_{signal}_q_{index} <= '0;")
    lines.append("    end else begin")
    for signal, _width in signals:
        for index in range(depth):
            input_signal = f"{signal}_src" if index == 0 else f"{name}_{signal}_q_{index - 1}"
            lines.append(f"      {name}_{signal}_q_{index} <= {input_signal};")
    lines.append("    end")
    lines.append("  end")
    for signal, _width in signals:
        lines.append(f"  assign {signal}_dst = {name}_{signal}_q_{depth - 1};")
    lines.append("")
    return lines


def emit_pipeline_logic(depths: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for item in VALID_READY_PIPELINES:
        depth = depths[item["name"]]
        if depth > 0:
            lines.extend(emit_valid_ready_pipeline(item, depth))
    for item in SIMPLE_SIGNAL_PIPELINES:
        depth = depths[item["name"]]
        if depth > 0:
            lines.extend(emit_simple_signal_pipeline(item, depth))
    return lines


def emit_instance(ip: Ip, depths: dict[str, int]) -> list[str]:
    lines: list[str] = []
    if ip.description:
        lines.append(f"  // {ip.description}")
    if ip.parameters:
        lines.append(f"  {ip.module} #(")
        params = list(ip.parameters.items())
        for index, (name, value) in enumerate(params):
            comma = "," if index < len(params) - 1 else ""
            lines.append(f"    .{name}({value}){comma}")
        lines.append(f"  ) u_{ip.name} (")
    else:
        lines.append(f"  {ip.module} u_{ip.name} (")
    for index, port in enumerate(ip.ports):
        comma = "," if index < len(ip.ports) - 1 else ""
        lines.append(f"    .{port.name}({physical_signal_name(port, depths)}){comma}")
    lines.append("  );")
    lines.append("")
    return lines


def generate(architecture_path: Path, ip_dir: Path) -> str:
    arch = load_json(architecture_path)
    ips = load_ips(ip_dir)
    top_ports = collect_top_ports(ips)
    nets = collect_nets(ips)
    depths = pipeline_depths(arch)
    validate_top_port_connectivity(ips)
    validate_net_connectivity(ips)
    validate_rtl_interfaces(repo_root_from_architecture(architecture_path), ips)
    validate_architecture_bindings(arch, ips)
    validate_pipeline_bindings(arch, ips)
    subsystems = subsystem_descriptions(arch)
    style_reference = arch.get("soc_top", {}).get("style_reference", {})

    lines = [
        "// Generated by e1/e1-h1/tools/generate_soc_top.py",
        f"// Example: {arch['example']}",
        f"// Architecture: {arch['architecture_id']}",
        f"// SoC top style: {style_reference.get('name', 'manifest_driven')}",
        f"// SoC top reference: {style_reference.get('url', 'local')}",
        "// Source of composition: e1/e1-h1/ip/*.json",
        "// Pipeline source: e1/e1-h1/config/architecture.json",
        "`default_nettype none",
        "",
        f"module {module_name(arch)} (",
    ]

    ordered_ports = sorted(top_ports.items())
    for index, (name, (direction, width)) in enumerate(ordered_ports):
        comma = "," if index < len(ordered_ports) - 1 else ""
        lines.append(f"  {direction} logic {sv_width(width)}{name}{comma}")
    lines.append(");")
    lines.append("")

    lines.extend(emit_net_declarations(nets, depths))
    lines.append("")
    lines.extend(emit_pipeline_logic(depths))

    current_subsystem = None
    for ip in ips:
        if ip.subsystem != current_subsystem:
            current_subsystem = ip.subsystem
            lines.append(f"  // Subsystem: {ip.subsystem}")
            lines.append(f"  // {subsystems.get(ip.subsystem, 'Manifest-defined subsystem.')}")
            lines.append("")
        lines.extend(emit_instance(ip, depths))

    lines.append("endmodule")
    lines.append("")
    lines.append("`default_nettype wire")
    lines.append("")
    return "\n".join(lines)


def endpoint(port: Port) -> dict[str, Any]:
    return {
        "instance": port.instance,
        "module": port.module,
        "port": port.name,
        "direction": port.direction,
        "width": port.width,
    }


def endpoint_roles(ports: list[Port]) -> dict[str, list[dict[str, Any]]]:
    return {
        "drivers": sorted(
            [endpoint(port) for port in ports if port.direction == "output"],
            key=lambda item: (item["instance"], item["port"]),
        ),
        "loads": sorted(
            [endpoint(port) for port in ports if port.direction == "input"],
            key=lambda item: (item["instance"], item["port"]),
        ),
        "inouts": sorted(
            [endpoint(port) for port in ports if port.direction == "inout"],
            key=lambda item: (item["instance"], item["port"]),
        ),
    }


def interface_payload(ip: Ip) -> dict[str, Any]:
    return {
        "name": ip.name,
        "subsystem": ip.subsystem,
        "parameters": [
            {"name": name, "value": value}
            for name, value in sorted(ip.parameters.items())
        ],
        "ports": [
            {
                "name": port.name,
                "direction": port.direction,
                "width": port.width,
                "connect": port.connect,
            }
            for port in ip.ports
        ],
        "perf_counters": ip.perf_counters,
    }


def interface_signature(ip: Ip) -> str:
    payload = json.dumps(interface_payload(ip), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_interface_contracts(architecture_path: Path, ip_dir: Path) -> dict[str, Any]:
    arch = load_json(architecture_path)
    ips = load_ips(ip_dir)
    validate_top_port_connectivity(ips)
    validate_net_connectivity(ips)
    rtl_validation = validate_rtl_interfaces(repo_root_from_architecture(architecture_path), ips)
    architecture_validation = validate_architecture_bindings(arch, ips)
    pipeline_validation = validate_pipeline_bindings(arch, ips)
    return {
        "schema": "e1-h1-interface-contracts-v0",
        "architecture_id": arch["architecture_id"],
        "architecture_validation": architecture_validation,
        "pipeline_validation": pipeline_validation,
        "source": "e1/e1-h1/ip/*.json",
        "rule": "implementation modules are replaceable only when the interface signature stays constant",
        "interfaces": [
            {
                **interface_payload(ip),
                "implementation_module": ip.module,
                "rtl": ip.rtl,
                "rtl_validation": rtl_validation[ip.name],
                "replaceable": ip.replaceable,
                "spec": ip.spec,
                "l1_5_harness": ip.l1_5_harness,
                "module_vip": ip.module_vip,
                "signature_sha256": interface_signature(ip),
            }
            for ip in ips
        ],
    }


def generate_composition_manifest(architecture_path: Path, ip_dir: Path) -> dict[str, Any]:
    arch = load_json(architecture_path)
    ips = load_ips(ip_dir)
    top_ports = collect_top_ports(ips)
    nets = collect_nets(ips)
    validate_top_port_connectivity(ips)
    validate_net_connectivity(ips)
    rtl_validation = validate_rtl_interfaces(repo_root_from_architecture(architecture_path), ips)
    architecture_validation = validate_architecture_bindings(arch, ips)
    pipeline_validation = validate_pipeline_bindings(arch, ips)
    descriptions = subsystem_descriptions(arch)
    declared_subsystems = [
        item["name"]
        for item in arch.get("soc_top", {}).get("subsystems", [])
    ]
    ip_by_subsystem = {name: [] for name in declared_subsystems}
    for ip in ips:
        ip_by_subsystem.setdefault(ip.subsystem, []).append(
            {
                "name": ip.name,
                "module": ip.module,
                "rtl": ip.rtl,
                "order": ip.order,
                "replaceable": ip.replaceable,
            }
        )

    net_ports = collect_net_ports(ips)
    top_port_ports = collect_top_port_ports(ips)

    return {
        "schema": "e1-h1-soc-top-composition-v0",
        "top": module_name(arch),
        "architecture_id": arch["architecture_id"],
        "style_reference": arch.get("soc_top", {}).get("style_reference", {}),
        "generation": arch.get("soc_top", {}).get("generation", {}),
        "architecture_validation": architecture_validation,
        "pipeline_validation": pipeline_validation,
        "rtl_validation": [
            {
                "name": name,
                **payload,
            }
            for name, payload in sorted(rtl_validation.items())
        ],
        "top_ports": [
            {
                "name": name,
                "direction": direction,
                "width": width,
                **endpoint_roles(top_port_ports[name]),
                "endpoints": sorted(
                    [endpoint(port) for port in top_port_ports[name]],
                    key=lambda item: (item["instance"], item["port"]),
                ),
                "validation": {
                    "single_output_driver": len(
                        [port for port in top_port_ports[name] if port.direction == "output"]
                    )
                    == 1
                    if direction == "output"
                    else False,
                    "has_input_load": any(port.direction == "input" for port in top_port_ports[name]),
                    "inout_only": all(port.direction == "inout" for port in top_port_ports[name])
                    if any(port.direction == "inout" for port in top_port_ports[name])
                    else False,
                },
            }
            for name, (direction, width) in sorted(top_ports.items())
        ],
        "nets": [
            {
                "name": name,
                "width": width,
                **endpoint_roles(net_ports[name]),
                "endpoints": sorted(
                    [endpoint(port) for port in net_ports[name]],
                    key=lambda item: (item["instance"], item["port"]),
                ),
                "validation": {
                    "single_driver": len([port for port in net_ports[name] if port.direction == "output"]) == 1,
                    "has_load": any(port.direction == "input" for port in net_ports[name]),
                    "inout_only": all(port.direction == "inout" for port in net_ports[name])
                    if any(port.direction == "inout" for port in net_ports[name])
                    else False,
                },
            }
            for name, width in sorted(nets.items())
        ],
        "subsystems": [
            {
                "name": name,
                "description": descriptions.get(name, "Manifest-defined subsystem."),
                "ips": ip_by_subsystem.get(name, []),
            }
            for name in [*declared_subsystems, *sorted(set(ip_by_subsystem) - set(declared_subsystems))]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture", type=Path, required=True)
    parser.add_argument("--ip-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--interfaces-output", type=Path)
    args = parser.parse_args()

    text = generate(args.architecture, args.ip_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    if args.manifest_output is not None:
        manifest = generate_composition_manifest(args.architecture, args.ip_dir)
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.interfaces_output is not None:
        interfaces = generate_interface_contracts(args.architecture, args.ip_dir)
        args.interfaces_output.parent.mkdir(parents=True, exist_ok=True)
        args.interfaces_output.write_text(
            json.dumps(interfaces, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
