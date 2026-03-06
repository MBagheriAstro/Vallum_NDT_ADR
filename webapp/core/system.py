"""System stats: CPU, RAM, disk (for Key Stats panel)."""

import os
import shutil
from typing import Optional

from .. import config


def read_cpu_percent() -> Optional[float]:
    """Approximate CPU usage as (1-min load / cores) * 100."""
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        return max(0.0, min(100.0, (load1 / cores) * 100.0))
    except OSError:
        return None


def read_ram_percent() -> Optional[float]:
    """RAM usage: 1 - MemAvailable/MemTotal from /proc/meminfo."""
    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val_str = parts[1].strip().split()[0]
                try:
                    meminfo[key] = int(val_str)
                except ValueError:
                    continue
        total = float(meminfo.get("MemTotal", 0))
        avail = float(meminfo.get("MemAvailable", 0))
        if total <= 0:
            return None
        return max(0.0, min(100.0, (1.0 - (avail / total)) * 100.0))
    except Exception:
        return None


def read_disk_percent() -> Optional[float]:
    """Disk usage of the filesystem containing BASE_DIR."""
    try:
        total, used, _ = shutil.disk_usage(str(config.BASE_DIR))
        if total <= 0:
            return None
        return max(0.0, min(100.0, (used / total) * 100.0))
    except Exception:
        return None
