"""Métricas reales del sistema (CPU, RAM, swap, discos, temperatura, uptime)."""
import os
import shutil
import time
from pathlib import Path

import psutil

# Puntos de montaje que nos interesan. Solo se informan los que existen.
_WATCH_MOUNTS = ["/", "/mnt/data", "/data"]


def _cpu_temperature() -> float | None:
    """Lee la temperatura desde thermal_zone o hwmon (ARM suele usar sysfs)."""
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for entries in temps.values():
                for e in entries:
                    if e.current:
                        return round(float(e.current), 1)
    except (AttributeError, NotImplementedError):
        pass

    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            millideg = int((zone / "temp").read_text().strip())
            return round(millideg / 1000.0, 1)
        except (OSError, ValueError):
            continue
    return None


def _cpu_model() -> str | None:
    # ARM: el modelo está en el device-tree. Según el entorno puede estar
    # accesible en /proc/device-tree/model o montado en /etc/board-model.
    for candidate in ("/etc/board-model", "/proc/device-tree/model"):
        try:
            text = Path(candidate).read_bytes().rstrip(b"\x00").decode("utf-8", "ignore").strip()
            if text:
                return text
        except OSError:
            continue

    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in ("model name", "hardware", "cpu model", "processor"):
                # En x86 'processor' es un número de núcleo: lo descartamos.
                if value and not value.isdigit():
                    return value
    except OSError:
        pass
    return None


def _uptime_seconds() -> float:
    try:
        return round(time.time() - psutil.boot_time(), 1)
    except Exception:  # pragma: no cover - defensivo
        return 0.0


def system_stats() -> dict:
    # interval=0.4 toma una muestra real en vez de la media desde el arranque.
    per_cpu = psutil.cpu_percent(interval=0.4, percpu=True)
    load1, load5, load15 = os.getloadavg()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "cpu": {
            "model": _cpu_model(),
            "cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "percent_total": round(sum(per_cpu) / len(per_cpu), 1) if per_cpu else None,
            "percent_per_core": per_cpu,
            "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
            "temperature_c": _cpu_temperature(),
        },
        "memory": {
            "total_bytes": mem.total,
            "used_bytes": mem.used,
            "available_bytes": mem.available,
            "percent": mem.percent,
        },
        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "percent": swap.percent,
        },
        "uptime_seconds": _uptime_seconds(),
        "boot_time": psutil.boot_time(),
    }


def disk_usage() -> dict:
    disks = []
    for mount in _WATCH_MOUNTS:
        if not os.path.ismount(mount) and mount != "/":
            continue
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        disks.append(
            {
                "mount": mount,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
            }
        )
    return {"disks": disks}
