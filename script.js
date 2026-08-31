"use strict";
// Renders window.DASH_DATA (written by data.js, regenerated each refresh cycle by render.py)
// into the DOM. Pure presentation logic - Python owns all data gathering.
function levelFor(value, warn, crit) {
    if (value === null || value === undefined)
        return "dim";
    if (value >= crit)
        return "crit";
    if (value >= warn)
        return "warn";
    return "ok";
}
function fmtPct(v) {
    return v === null || v === undefined ? "--" : `${Math.round(v)}%`;
}
function fmtRuntime(seconds) {
    if (seconds === null || seconds === undefined)
        return "--";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}m ${s.toString().padStart(2, "0")}s`;
}
function statBlock(label, valueText, level, sub) {
    return `
    <div class="stat">
      <div class="label">${label}</div>
      <div class="value ${level}">${valueText}</div>
      <div class="sub">${sub}</div>
    </div>`;
}
function fmtAgeShort(iso) {
    if (!iso)
        return "never";
    const t = new Date(iso).getTime();
    if (isNaN(t))
        return "never";
    const mins = Math.floor((Date.now() - t) / 60000);
    if (mins < 1)
        return "now";
    if (mins < 60)
        return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 48)
        return `${hrs}h`;
    return `${Math.floor(hrs / 24)}d`;
}
function ramStatBlock(rh) {
    var _a, _b, _c;
    var _d;
    const mceCount = (_d = (_a = rh.mce) === null || _a === void 0 ? void 0 : _a.mce_count) !== null && _d !== void 0 ? _d : 0;
    const result = (_b = rh.memtest) === null || _b === void 0 ? void 0 : _b.result;
    let level = "dim";
    let value = "--";
    if (mceCount > 0 || result === "fail") {
        level = "crit";
        value = "FAIL";
    }
    else if (result === "pass") {
        level = "ok";
        value = "OK";
    }
    else if (result === "skipped_low_mem") {
        level = "warn";
        value = "SKIP";
    }
    const sub = `mtest ${fmtAgeShort((_c = rh.memtest) === null || _c === void 0 ? void 0 : _c.tested_at)} ago &middot; mce ${mceCount}`;
    return statBlock("RAM", value, level, sub);
}
function renderStats(d) {
    const el = document.getElementById("stats");
    el.innerHTML = [
        statBlock("CPU", fmtPct(d.cpu_pct), levelFor(d.cpu_pct, 70, 90), ""),
        statBlock("MEM", fmtPct(d.mem_pct), levelFor(d.mem_pct, 75, 90), ""),
        statBlock("TEMP", d.temp_c !== null ? `${Math.round(d.temp_c)}°C` : "--", levelFor(d.temp_c, 80, 95), ""),
        statBlock("POOL", fmtPct(d.pool_pct), levelFor(d.pool_pct, 75, 85), "LVM thin pool"),
        statBlock("POWER", d.watts !== null ? `${Math.round(d.watts)}W` : "--", "dim", "Shelly plug"),
        ramStatBlock(d.ram_health),
    ].join("");
}
function renderMidline(d) {
    const el = document.getElementById("midline");
    const net = `net &darr;${d.net_rx_kbps.toFixed(0)} KB/s &uarr;${d.net_tx_kbps.toFixed(0)} KB/s`;
    el.innerHTML = `<span>load ${d.load_avg}</span><span>${net}</span>`;
}
function guestRowHtml(g) {
    const dotClass = g.running ? "on" : "off";
    const rowClass = g.running ? "" : "off";
    const metrics = g.running ? `${g.cpu_pct.toFixed(0)}% cpu &middot; ${g.mem_pct.toFixed(0)}% mem` : "stopped";
    return `<div class="guest-row ${rowClass}">
    <span class="dot ${dotClass}">&#9679;</span>
    <span class="vmid">${g.vmid}</span>
    <span class="name">${g.name}</span>
    <span class="metrics">${metrics}</span>
  </div>`;
}
function renderGuests(d) {
    const grid = document.getElementById("guest-grid");
    const note = document.getElementById("overflow-note");
    const sorted = [...d.guests].sort((a, b) => {
        if (a.running !== b.running)
            return a.running ? 1 : -1;
        return a.vmid - b.vmid;
    });
    grid.innerHTML = sorted.map(guestRowHtml).join("");
    // Measure and drop rows that don't actually fit rather than silently clipping via overflow:hidden.
    let hiddenCount = 0;
    while (grid.scrollHeight > grid.clientHeight && grid.lastElementChild) {
        grid.removeChild(grid.lastElementChild);
        hiddenCount++;
    }
    const runningCt = d.guests.filter(g => g.running).length;
    const others = Object.entries(d.other_nodes)
        .map(([node, [run, tot]]) => `${node}: ${run}/${tot} running`)
        .join("  ");
    const overflowTxt = hiddenCount > 0 ? `  (+${hiddenCount} more not shown)` : "";
    note.textContent = `this host: ${runningCt}/${d.guests.length} running   ${others}${overflowTxt}`;
}
function renderUps(d) {
    var _a;
    const panel = document.getElementById("ups-panel");
    if (!d.ups || !d.ups.present) {
        panel.style.display = "none";
        return;
    }
    panel.style.display = "flex";
    const statusEl = document.getElementById("ups-status");
    statusEl.textContent = d.ups.online ? "ON LINE" : "ON BATTERY";
    statusEl.className = "status-line " + (d.ups.online ? "online" : "battery");
    const charge = (_a = d.ups.charge_pct) !== null && _a !== void 0 ? _a : 0;
    const fill = document.getElementById("ups-battery-fill");
    fill.style.width = `${Math.max(0, Math.min(100, charge))}%`;
    fill.className = "battery-fill " + (charge < 30 ? "crit" : charge < 60 ? "warn" : "");
    document.getElementById("ups-charge").textContent = fmtPct(d.ups.charge_pct);
    document.getElementById("ups-runtime").textContent = fmtRuntime(d.ups.runtime_s);
    document.getElementById("ups-load").textContent = fmtPct(d.ups.load_pct);
    document.getElementById("ups-input").textContent =
        d.ups.input_voltage !== null ? `${d.ups.input_voltage.toFixed(0)}V` : "--";
}
function renderHeader(d) {
    document.getElementById("h-hostname").textContent = d.hostname.toUpperCase();
    document.getElementById("h-time").textContent = d.time;
    const q = document.getElementById("h-quorum");
    if (d.quorate === null) {
        q.textContent = "quorum ?";
        q.className = "quorum";
    }
    else {
        q.textContent = d.quorate ? "quorate" : "NO QUORUM";
        q.className = "quorum " + (d.quorate ? "ok" : "bad");
    }
}
function render() {
    const d = DASH_DATA;
    if (!d)
        return;
    renderHeader(d);
    renderStats(d);
    renderMidline(d);
    renderGuests(d);
    renderUps(d);
    document.getElementById("h-footer").textContent =
        `pve-dashboard-web · refreshed ${d.time}`;
}
render();
