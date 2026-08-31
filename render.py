#!/usr/bin/env python3
"""
Web-rendered status dashboard for a Proxmox host's physical console (tty1 framebuffer).
Python gathers data -> writes data.js -> headless Chromium screenshots index.html -> that PNG
is written straight to /dev/fb0 with a single synchronous `ffmpeg -f fbdev` call each cycle.

(Earlier version used `fbi` with a persistent process + SIGUSR1 to reload - fbi turned out to
re-fork/detach in a way that made the tracked PID go stale almost immediately, so every cycle
spawned a brand new fbi process that was never cleaned up. `ffmpeg -f fbdev` writing one frame
per invocation and exiting is simpler and has no daemon/PID-tracking surface to get wrong.)

No Node/TypeScript toolchain on this host: script.ts is compiled elsewhere, only the built
script.js is shipped here alongside this file.
"""
import glob
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time

ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
SHOT_PATH = "/run/pve-dashboard.png"
SHOT_TMP_PATH = "/run/pve-dashboard-tmp.png"
# Fixed profile dir: headless chromium leaks a fresh scoped_dir under
# ~/.cache/chromium-headless/ every invocation if none is given. Reuse one.
CHROME_PROFILE = "/run/pve-dashboard-chrome"
LOG_PATH = "/var/log/pve-dashboard.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("pve-dashboard")

# --- Configuration -----------------------------------------------------------
# Override any of these via config.py (same directory, git-ignored). See
# config.example.py.
REFRESH_SECONDS = 5
POOL_WARN_PCT = 75.0
POOL_CRIT_PCT = 85.0
# Optional: map hostname -> Shelly plug IP for per-host power draw. Leave empty
# to disable the watts panel.
SHELLY_IP = {}

try:
    from config import *  # noqa: F401,F403
except ImportError:
    pass


def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def fb_size():
    raw = read_file("/sys/class/graphics/fb0/virtual_size").strip()
    if "," in raw:
        w, h = raw.split(",")
        return int(w), int(h)
    return 800, 480


class Metrics:
    def __init__(self, hostname, net_iface):
        self.hostname = hostname
        self.net_iface = net_iface
        self._prev_cpu = self._read_cpu_times()
        self._prev_net = self._read_net_bytes()
        self._prev_net_t = time.monotonic()
        self.temp_path = self._find_temp_path()
        self.has_ups = self._probe_ups()

    def _read_cpu_times(self):
        line = read_file("/proc/stat").splitlines()[0]
        fields = [int(x) for x in line.split()[1:]]
        idle = fields[3] + fields[4]
        return idle, sum(fields)

    def cpu_percent(self):
        idle, total = self._read_cpu_times()
        prev_idle, prev_total = self._prev_cpu
        self._prev_cpu = (idle, total)
        dt_total = total - prev_total
        dt_idle = idle - prev_idle
        if dt_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (dt_total - dt_idle) / dt_total))

    def mem_percent(self):
        info = {}
        for line in read_file("/proc/meminfo").splitlines():
            m = re.match(r"(\w+):\s+(\d+)", line)
            if m:
                info[m.group(1)] = int(m.group(2))
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        return 100.0 * (total - avail) / total if total else 0.0

    def load_avg(self):
        parts = read_file("/proc/loadavg").split()
        return parts[0] if parts else "?"

    def _find_temp_path(self):
        for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
            if read_file(name_path).strip() in ("k10temp", "coretemp"):
                base = name_path.rsplit("/", 1)[0]
                cands = sorted(glob.glob(base + "/temp*_input"))
                if cands:
                    return cands[0]
        return None

    def temp_c(self):
        if not self.temp_path:
            return None
        try:
            return int(read_file(self.temp_path).strip()) / 1000.0
        except ValueError:
            return None

    def _read_net_bytes(self):
        rx = tx = 0
        for line in read_file("/proc/net/dev").splitlines():
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            if iface.strip() != self.net_iface:
                continue
            fields = rest.split()
            rx, tx = int(fields[0]), int(fields[8])
        return rx, tx

    def net_kbps(self):
        rx, tx = self._read_net_bytes()
        now = time.monotonic()
        prev_rx, prev_tx = self._prev_net
        dt = now - self._prev_net_t
        self._prev_net, self._prev_net_t = (rx, tx), now
        if dt <= 0:
            return 0.0, 0.0
        return (rx - prev_rx) / dt / 1024, (tx - prev_tx) / dt / 1024

    def pool_pct(self):
        try:
            out = subprocess.run(
                ["lvs", "pve/data", "-o", "data_percent", "--noheadings"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return float(out) if out else None
        except Exception:
            return None

    def cluster(self):
        try:
            resources = json.loads(subprocess.run(
                ["pvesh", "get", "/cluster/resources", "--output-format", "json"],
                capture_output=True, text=True, timeout=8,
            ).stdout)
        except Exception:
            resources = None
        try:
            status = json.loads(subprocess.run(
                ["pvesh", "get", "/cluster/status", "--output-format", "json"],
                capture_output=True, text=True, timeout=8,
            ).stdout)
            quorate = any(i.get("type") == "cluster" and i.get("quorate") for i in status)
        except Exception:
            quorate = None

        guests, others = [], {}
        if resources is not None:
            for r in resources:
                if r.get("type") not in ("qemu", "lxc"):
                    continue
                running = r.get("status") == "running"
                if r.get("node") == self.hostname:
                    guests.append({
                        "vmid": r.get("vmid"),
                        "name": r.get("name") or f"vmid-{r.get('vmid')}",
                        "running": running,
                        "cpu_pct": (r.get("cpu") or 0) * 100,
                        "mem_pct": (100.0 * r["mem"] / r["maxmem"]) if r.get("maxmem") else 0,
                    })
                else:
                    node = r.get("node")
                    run, tot = others.get(node, (0, 0))
                    others[node] = (run + (1 if running else 0), tot + 1)
        return guests, others, quorate

    def watts(self):
        ip = SHELLY_IP.get(self.hostname)
        if not ip:
            return None
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://{ip}/rpc/Switch.GetStatus?id=0", timeout=3) as resp:
                return json.loads(resp.read()).get("apower")
        except Exception:
            return None

    def ram_health(self):
        mce = json.loads(read_file("/var/lib/ram-health/mce.json") or "{}") or {}
        memtest = json.loads(read_file("/var/lib/ram-health/memtest.json") or "{}") or {}
        return {"mce": mce, "memtest": memtest}

    def _probe_ups(self):
        try:
            r = subprocess.run(["upsc", "-l"], capture_output=True, text=True, timeout=3)
            return bool(r.stdout.strip())
        except Exception:
            return False

    def ups(self):
        if not self.has_ups:
            return None
        try:
            r = subprocess.run(["upsc", "-l"], capture_output=True, text=True, timeout=3)
            ups_name = r.stdout.strip().splitlines()[0]
            out = subprocess.run(["upsc", ups_name], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return {"present": False}
        vals = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                vals[k.strip()] = v.strip()
        status = vals.get("ups.status", "")
        return {
            "present": True,
            "status": status,
            "online": "OL" in status.split() if status else True,
            "charge_pct": float(vals["battery.charge"]) if "battery.charge" in vals else None,
            "runtime_s": float(vals["battery.runtime"]) if "battery.runtime" in vals else None,
            "load_pct": float(vals["ups.load"]) if "ups.load" in vals else None,
            "input_voltage": float(vals["input.voltage"]) if "input.voltage" in vals else None,
        }


def default_iface():
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"dev (\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "vmbr0"


def write_data_js(data):
    path = os.path.join(ASSET_DIR, "data.js")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("const DASH_DATA = ")
        json.dump(data, f)
        f.write(";\n")
    os.replace(tmp, path)


def take_screenshot(width, height):
    cmd = [
        "chromium",
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        f"--window-size={width},{height}",
        f"--user-data-dir={CHROME_PROFILE}",
        f"--crash-dumps-dir={CHROME_PROFILE}",
        f"--screenshot={SHOT_TMP_PATH}",
        "file://" + os.path.join(ASSET_DIR, "index.html"),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if not os.path.exists(SHOT_TMP_PATH):
        log.error("chromium screenshot failed: %s", result.stderr.decode(errors="replace"))
        return False
    os.replace(SHOT_TMP_PATH, SHOT_PATH)
    return True


def write_to_framebuffer():
    """Single synchronous frame write, no persistent process to track between cycles."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", SHOT_PATH,
        "-vframes", "1",
        "-pix_fmt", "bgra",
        "-f", "fbdev", "/dev/fb0",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=15)
    if result.returncode != 0:
        log.error("ffmpeg fbdev write failed: %s", result.stderr.decode(errors="replace")[-2000:])


def main():
    hostname = socket.gethostname().split(".")[0]
    width, height = fb_size()
    metrics = Metrics(hostname, default_iface())
    log.info("starting: hostname=%s size=%dx%d", hostname, width, height)

    consecutive_failures = 0
    while True:
        try:
            ok = run_cycle(metrics, hostname, width, height)
            consecutive_failures = 0 if ok else consecutive_failures + 1
        except Exception:
            log.exception("run_cycle failed")
            consecutive_failures += 1
        if consecutive_failures >= 10:
            log.error("giving up after %d consecutive cycle failures", consecutive_failures)
            sys.exit(1)
        time.sleep(REFRESH_SECONDS)


def run_cycle(metrics, hostname, width, height):
    guests, others, quorate = metrics.cluster()
    net_rx_kbps, net_tx_kbps = metrics.net_kbps()
    data = {
        "hostname": hostname,
        "time": time.strftime("%H:%M:%S"),
        "quorate": quorate,
        "cpu_pct": metrics.cpu_percent(),
        "mem_pct": metrics.mem_percent(),
        "temp_c": metrics.temp_c(),
        "pool_pct": metrics.pool_pct(),
        "load_avg": metrics.load_avg(),
        "net_rx_kbps": net_rx_kbps,
        "net_tx_kbps": net_tx_kbps,
        "watts": metrics.watts(),
        "guests": guests,
        "other_nodes": others,
        "ups": metrics.ups(),
        "ram_health": metrics.ram_health(),
    }
    write_data_js(data)
    if not take_screenshot(width, height):
        return False
    write_to_framebuffer()
    return True


if __name__ == "__main__":
    main()
