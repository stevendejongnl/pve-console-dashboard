# pve-console-dashboard

A status screen for a Proxmox VE host's physical console (the `tty1`
framebuffer). Instead of a login prompt on the monitor plugged into the
server, you get a live dashboard: CPU / memory / temperature, LVM-thin pool
usage, network throughput, cluster quorum, the guests running on that node,
UPS state, and optional per-host power draw from a Shelly plug.

There is no web server and nothing to connect to. Python gathers the data,
writes it into `data.js`, headless Chromium screenshots `index.html`, and that
PNG is blitted straight to `/dev/fb0`. One cycle every few seconds.

## How it works

```
render.py (loop)
  ├── gather metrics  (/proc, pvesh, lvs, upsc, Shelly RPC)
  ├── write data.js
  ├── chromium --headless --screenshot  index.html  ->  /run/pve-dashboard.png
  └── ffmpeg -f fbdev  /run/pve-dashboard.png  ->  /dev/fb0
```

`index.html` + `script.js` + `style.css` are pure presentation; they only
render `window.DASH_DATA`. All data gathering lives in `render.py`.

Chromium is given a fixed `--user-data-dir` (`/run/pve-dashboard-chrome`, on
tmpfs). Headless Chromium leaks a throwaway profile directory on every
invocation if you don't pin one — at a 5 second cycle that is thousands of
directories a day.

## Requirements

- Proxmox VE (uses `pvesh`, `lvs`)
- `chromium` (or `chromium-browser`)
- `ffmpeg` with `fbdev` output support
- A framebuffer console at `/dev/fb0`
- Optional: `nut-client` for the UPS panel, a Shelly Gen2 plug for watts

## Install

```sh
git clone https://github.com/stevendejongnl/pve-console-dashboard /opt/pve-console-dashboard
cd /opt/pve-console-dashboard
cp config.example.py config.py     # edit thresholds / Shelly IPs
cp pve-console-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pve-console-dashboard
```

To actually see it, the framebuffer must not be covered by a getty. Either
stop `getty@tty1` or point the kernel console elsewhere.

## Configuration

Everything is optional. Copy `config.example.py` to `config.py` (git-ignored)
and override:

| Setting            | Default | Meaning                                        |
|--------------------|---------|------------------------------------------------|
| `REFRESH_SECONDS`  | `5`     | Seconds between cycles                          |
| `POOL_WARN_PCT`    | `75.0`  | LVM-thin pool usage warn threshold             |
| `POOL_CRIT_PCT`    | `85.0`  | LVM-thin pool usage critical threshold         |
| `SHELLY_IP`        | `{}`    | `{hostname: plug_ip}`; empty hides watts panel |

## License

MIT
