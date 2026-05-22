#!/usr/bin/env python3
"""Generate the E1-H1 SoC top from IP manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
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
    spec: str
    l1_5_harness: str
    perf_counters: list[str]
    parameters: dict[str, int]
    ports: list[Port]


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
                spec=data["spec"],
                l1_5_harness=data["l1_5_harness"],
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
    validate_net_connectivity(ips)
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
    validate_net_connectivity(ips)
    return {
        "schema": "e1-h1-interface-contracts-v0",
        "architecture_id": arch["architecture_id"],
        "source": "e1/e1-h1/ip/*.json",
        "rule": "implementation modules are replaceable only when the interface signature stays constant",
        "interfaces": [
            {
                **interface_payload(ip),
                "implementation_module": ip.module,
                "replaceable": ip.replaceable,
                "spec": ip.spec,
                "l1_5_harness": ip.l1_5_harness,
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
    validate_net_connectivity(ips)
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
                "order": ip.order,
                "replaceable": ip.replaceable,
            }
        )

    net_ports = collect_net_ports(ips)

    return {
        "schema": "e1-h1-soc-top-composition-v0",
        "top": module_name(arch),
        "architecture_id": arch["architecture_id"],
        "style_reference": arch.get("soc_top", {}).get("style_reference", {}),
        "generation": arch.get("soc_top", {}).get("generation", {}),
        "top_ports": [
            {"name": name, "direction": direction, "width": width}
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
