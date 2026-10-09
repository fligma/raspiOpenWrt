"""System metrics for the dashboard: temperature, load/memory, per-core CPU,
storage usage, and network traffic counters. All readings come from /proc,
/sys, or statvfs and degrade gracefully when a source is unavailable."""

import os
import platform
import time

TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"


def get_temp():
    try:
        with open(TEMP_PATH, "r") as f:
            return round(float(f.read().strip()) / 1000.0, 1)
    except Exception:
        return "N/A"


def get_uptime_seconds():
    try:
        with open("/proc/uptime", "r") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return None


def format_uptime(seconds):
    if seconds is None:
        return "N/A"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_sys():
    load = "N/A"
    try:
        with open("/proc/loadavg", "r") as f:
            load = " ".join(f.read().strip().split()[0:3])
    except Exception:
        pass

    ram = "N/A"
    ram_percent = 0
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].replace("kB", "").strip())
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0))
        used = total - avail
        ram_percent = round((used / total) * 100, 1)
        ram = f"{round(used / 1024, 1)} MB / {round(total / 1024, 1)} MB ({ram_percent}%)"
    except Exception:
        ram = "N/A"

    uptime_seconds = get_uptime_seconds()
    return {
        "load": load,
        "ram": ram,
        "ram_percent": ram_percent,
        "uptime": format_uptime(uptime_seconds),
        "uptime_seconds": uptime_seconds,
        "hostname": platform.node() or "N/A",
        "cores": os.cpu_count() or 0,
    }


def read_proc_stat():
    """Return {label: [jiffies...]} for every 'cpu' line in /proc/stat."""
    result = {}
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                try:
                    result[parts[0]] = [int(x) for x in parts[1:]]
                except ValueError:
                    continue
    except Exception:
        return {}
    return result


def cpu_percent(prev, cur):
    if not prev or not cur:
        return 0.0
    prev_idle = prev[3] + (prev[4] if len(prev) > 4 else 0)
    cur_idle = cur[3] + (cur[4] if len(cur) > 4 else 0)
    d_total = sum(cur) - sum(prev)
    d_idle = cur_idle - prev_idle
    if d_total <= 0:
        return 0.0
    return round((d_total - d_idle) / d_total * 100.0, 1)


def get_cpu(sample_seconds=0.25):
    """Sample /proc/stat twice to derive overall and per-core utilisation."""
    first = read_proc_stat()
    if not first:
        return {"error": "unavailable", "overall": 0.0, "cores": []}
    time.sleep(sample_seconds)
    second = read_proc_stat()
    if not second:
        return {"error": "unavailable", "overall": 0.0, "cores": []}

    cores = []
    index = 0
    while f"cpu{index}" in first and f"cpu{index}" in second:
        cores.append(cpu_percent(first[f"cpu{index}"], second[f"cpu{index}"]))
        index += 1
    overall = cpu_percent(first.get("cpu", []), second.get("cpu", []))
    return {"overall": overall, "cores": cores}


def get_storage(path="/"):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        percent = round((used / total) * 100, 1) if total else 0
        return {"total": total, "used": used, "free": free, "percent": percent}
    except Exception as error:
        return {"error": str(error)}


def get_traffic():
    """Cumulative RX/TX byte counters per interface (loopback excluded)."""
    interfaces = []
    total_rx = total_tx = 0
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f.readlines()[2:]:
                if ":" not in line:
                    continue
                name, stats = line.split(":", 1)
                fields = stats.split()
                name = name.strip()
                if name == "lo" or len(fields) < 9:
                    continue
                rx = int(fields[0])
                tx = int(fields[8])
                total_rx += rx
                total_tx += tx
                interfaces.append({"name": name, "rx": rx, "tx": tx})
        return {"interfaces": interfaces, "rx": total_rx, "tx": total_tx}
    except Exception as error:
        return {"error": str(error)}
