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


def emit_instance(ip: Ip) -> list[str]:
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
        lines.append(f"    .{port.name}({signal_name(port.connect)}){comma}")
    lines.append("  );")
    lines.append("")
    return lines


def generate(architecture_path: Path, ip_dir: Path) -> str:
    arch = load_json(architecture_path)
    ips = load_ips(ip_dir)
    top_ports = collect_top_ports(ips)
    nets = collect_nets(ips)
    validate_top_port_connectivity(ips)
    validate_net_connectivity(ips)
    validate_rtl_interfaces(repo_root_from_architecture(architecture_path), ips)
    validate_architecture_bindings(arch, ips)
    subsystems = subsystem_descriptions(arch)
    style_reference = arch.get("soc_top", {}).get("style_reference", {})

    lines = [
        "// Generated by e1/e1-h1/tools/generate_soc_top.py",
        f"// Example: {arch['example']}",
        f"// Architecture: {arch['architecture_id']}",
        f"// SoC top style: {style_reference.get('name', 'manifest_driven')}",
        f"// SoC top reference: {style_reference.get('url', 'local')}",
        "// Source of composition: e1/e1-h1/ip/*.json",
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

    for name, width in sorted(nets.items()):
        lines.append(f"  logic {sv_width(width)}{name};")
    lines.append("")

    current_subsystem = None
    for ip in ips:
        if ip.subsystem != current_subsystem:
            current_subsystem = ip.subsystem
            lines.append(f"  // Subsystem: {ip.subsystem}")
            lines.append(f"  // {subsystems.get(ip.subsystem, 'Manifest-defined subsystem.')}")
            lines.append("")
        lines.extend(emit_instance(ip))

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
    return {
        "schema": "e1-h1-interface-contracts-v0",
        "architecture_id": arch["architecture_id"],
        "architecture_validation": architecture_validation,
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
