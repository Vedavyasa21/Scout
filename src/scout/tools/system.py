"""Read-only system inspection tools."""

from __future__ import annotations

import platform
import socket
import sys
from datetime import UTC, datetime
from typing import Any

import psutil

from scout.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)


def _bytes_to_gib(value: int) -> float:
    return round(value / (1024**3), 2)


def get_os_info(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        boot = datetime.fromtimestamp(psutil.boot_time(), tz=UTC)
        uptime_hours = round((datetime.now(UTC) - boot).total_seconds() / 3600, 2)
        data = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "hostname": socket.gethostname(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "cpu_count_logical": psutil.cpu_count(logical=True),
            "cpu_count_physical": psutil.cpu_count(logical=False),
            "total_memory_gib": _bytes_to_gib(psutil.virtual_memory().total),
            "boot_time_utc": boot.isoformat(),
            "uptime_hours": uptime_hours,
        }
    except Exception as exc:
        return ToolResult(ok=False, error=f"Could not read OS information: {exc}")
    return ToolResult(ok=True, data=data)


def get_resource_usage(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    disk_path = args.get("disk_path")
    sample_seconds = float(args.get("sample_seconds", 0.3))
    try:
        cpu = {
            "percent_total": psutil.cpu_percent(interval=sample_seconds),
            "percent_per_core": psutil.cpu_percent(interval=0, percpu=True),
            "load_average": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
        }
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        disks: list[dict[str, Any]] = []
        targets = [disk_path] if disk_path else []
        if not targets:
            for partition in psutil.disk_partitions(all=False):
                targets.append(partition.mountpoint)
        for target in targets:
            try:
                usage = psutil.disk_usage(target)
            except (OSError, PermissionError):
                continue
            disks.append(
                {
                    "path": target,
                    "total_gib": _bytes_to_gib(usage.total),
                    "used_gib": _bytes_to_gib(usage.used),
                    "free_gib": _bytes_to_gib(usage.free),
                    "percent_used": usage.percent,
                }
            )

        net = psutil.net_io_counters()
        interfaces = {
            name: {"up": bool(stats.isup), "speed_mbps": stats.speed or None}
            for name, stats in psutil.net_if_stats().items()
        }

        data = {
            "cpu": cpu,
            "memory": {
                "total_gib": _bytes_to_gib(memory.total),
                "available_gib": _bytes_to_gib(memory.available),
                "percent_used": memory.percent,
            },
            "swap": {
                "total_gib": _bytes_to_gib(swap.total),
                "used_gib": _bytes_to_gib(swap.used),
                "percent_used": swap.percent,
            },
            "disks": disks,
            "network": {
                "bytes_sent_mib": round(net.bytes_sent / (1024**2), 2),
                "bytes_received_mib": round(net.bytes_recv / (1024**2), 2),
                "interfaces": interfaces,
            },
            "battery": _battery_status(),
        }
    except Exception as exc:
        return ToolResult(ok=False, error=f"Could not read resource usage: {exc}")
    return ToolResult(ok=True, data=data)


def _battery_status() -> dict[str, Any]:
    battery = psutil.sensors_battery()
    if battery is None:
        return {"present": False}
    seconds_left = battery.secsleft
    return {
        "present": True,
        "percent": battery.percent,
        "power_plugged": battery.power_plugged,
        "seconds_left": None if seconds_left < 0 else seconds_left,
    }


def list_processes(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    limit = int(args.get("limit", 15))
    sort_by = args.get("sort_by", "memory")
    try:
        processes: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            info = proc.info
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name") or "unknown",
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                    "status": info.get("status"),
                }
            )
    except Exception as exc:
        return ToolResult(ok=False, error=f"Could not list processes: {exc}")

    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    processes.sort(key=lambda item: item.get(key) or 0.0, reverse=True)
    top = processes[:limit]
    return ToolResult(
        ok=True,
        data={"total_processes": len(processes), "sort_by": sort_by, "processes": top},
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="get_os_info",
            description="Get operating system, hardware and Python environment information.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission=PermissionLevel.READ,
            handler=get_os_info,
            tags=("system",),
        )
    )
    registry.register(
        Tool(
            name="get_resource_usage",
            description=(
                "Get current CPU, memory, swap, disk, network and battery usage. "
                "Use this to check system performance or health."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "disk_path": {
                        "type": "string",
                        "description": "Optional disk or mount point to report, e.g. 'C:\\'.",
                    },
                    "sample_seconds": {
                        "type": "number",
                        "description": "CPU sampling window in seconds (default 0.3).",
                        "minimum": 0,
                        "maximum": 5,
                    },
                },
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=get_resource_usage,
            tags=("system", "performance"),
        )
    )
    registry.register(
        Tool(
            name="list_processes",
            description="List running processes sorted by CPU or memory usage.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum processes to return (default 15, max 100).",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["cpu", "memory"],
                        "description": "Sort key (default 'memory').",
                    },
                },
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=list_processes,
            tags=("system",),
        )
    )
