# Copy to config.py (git-ignored) and adjust. All values are optional;
# anything not set here falls back to the defaults in render.py.

# Seconds between refresh cycles.
REFRESH_SECONDS = 5

# LVM-thin data pool usage thresholds (percent) for the colour coding.
POOL_WARN_PCT = 75.0
POOL_CRIT_PCT = 85.0

# Map short hostname -> Shelly plug IP to show per-host power draw.
# Uses the Gen2 RPC endpoint: http://<ip>/rpc/Switch.GetStatus?id=0
# Leave as {} to hide the watts panel.
SHELLY_IP = {
    # "node1": "10.0.0.11",
    # "node2": "10.0.0.12",
}
