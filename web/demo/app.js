/* EmberLedger demo page. Plain JS, no build step; the only requests go to the API (or the backups).
 *
 * API base: ?api=https://your-service.onrender.com, else the same origin when the API serves this page
 * (/web/demo/), else http://localhost:8000. If the API is unreachable, the page reads ../backup/*.json
 * (written by scripts/export_backups.py) and says so.
 * Links can preselect: ?area=bizerte&month=2008-07&theme=dark
 * Everything from the API is inserted as text (textContent), never as HTML.
 */
"use strict";

const params = new URLSearchParams(location.search);
const API = (params.get("api") ?? (location.pathname.startsWith("/web/") ? "" : "http://localhost:8000")).replace(/\/+$/, "");
const BACKUP = "../backup/";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];
const FLAG = {
  high: { glyph: "▲", label: "Unusually high (z ≥ 3)" },
  low: { glyph: "▼", label: "Unusually low (z ≤ −3)" },
  low_confidence: { glyph: "?", label: "Low confidence (pre-2012 ratio on < 30 MODIS detections)" },
  normal: { glyph: "", label: "Within the usual range" },
};
// Sequential blue ramp, steps 100..700 of the reference palette. Dark mode runs it the other way,
// so "more" is always further from the surface.
const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
  "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];

const $ = (id) => document.getElementById(id);
const state = { offline: false, areas: [], validation: null, cal: null, q: null, sel: null, cells: null, annual: null, logged: 0 };

// ---------------------------------------------------------------- DOM helpers

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
}
function build(el, attrs, kids) {
  setAttrs(el, attrs);
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}
const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
const svg = (tag, attrs, ...kids) => build(document.createElementNS("http://www.w3.org/2000/svg", tag), attrs, kids);

const nf1 = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const nfc = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const fmt = (v) => (v == null || Number.isNaN(v) ? "–" : nf1.format(v));
const sum = (a) => a.reduce((s, x) => s + (x || 0), 0);
const range = (a, b) => Array.from({ length: Math.max(0, b - a + 1) }, (_, i) => a + i);
const median = (a) => {
  const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
  return s.length ? (s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2) : null;
};
function niceTicks(max, n = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  return range(0, Math.ceil(max / step)).map((i) => +(i * step).toFixed(6));
}
const tickFmt = (top) => (v) => (top >= 10000 ? nfc.format(v) : nf1.format(v));

// ---------------------------------------------------------------- API + log

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; } // status 0 = network error
}

async function api(method, path, body) {
  const t0 = performance.now();
  let status = 0, data;
  try {
    const res = await fetch(API + path, {
      method,
      body: body ? JSON.stringify(body) : undefined,
      headers: body ? { "Content-Type": "application/json" } : undefined,
    });
    status = res.status;
    const text = await res.text();
    try { data = JSON.parse(text); } catch { data = text; }
  } catch (e) {
    data = `network error: ${e.message}`;
  }
  addLog(method, path, status, performance.now() - t0, data);
  if (status < 200 || status >= 300) {
    const d = data && typeof data === "object" && "detail" in data ? data.detail : data;
    const msg = typeof d === "string" ? d : JSON.stringify(d);
    throw new ApiError(status, status ? `${status}: ${msg}` : msg);
  }
  return data;
}

async function backupFile(name) {
  const t0 = performance.now();
  let status = 0, data;
  try {
    const r = await fetch(BACKUP + name);
    status = r.status;
    data = r.ok ? await r.json() : await r.text();
  } catch (e) {
    data = `network error: ${e.message}`;
  }
  addLog("GET", `(backup) ${name}`, status, performance.now() - t0, data);
  if (status !== 200) throw new ApiError(status, `backup ${name} not available (${status || "network error"})`);
  return data;
}

function addLog(method, path, status, ms, data) {
  const ok = status >= 200 && status < 300;
  let text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  if (text.length > 20000) text = `${text.slice(0, 20000)}\n… (truncated)`;
  $("log").prepend(h("li", null, h("details", null,
    h("summary", null, h("code", null, `${method} ${path}`), " ",
      h("span", { class: ok ? "ok" : "bad" }, status || "network error"),
      h("span", { class: "muted" }, ` · ${Math.round(ms)} ms`)),
    h("pre", null, text))));
  $("log-count").textContent = `(${++state.logged})`;
}

// ---------------------------------------------------------------- status, banners, theme

function setStatus(kind, text, title) {
  const el = $("status");
  el.className = `status ${kind}`;
  el.querySelector(".txt").textContent = text;
  if (title) el.title = title;
}
const showBanner = (kind, title, text) => $("banners").append(h("div", { class: `banner ${kind}` }, h("b", null, title), text));

function syntheticBanner() {
  const v = state.validation;
  if (!v?.synthetic) return;
  const planted = (v.planted_anomalies || [])
    .map((p) => `${p.area} ${MONTHS[p.month - 1]} ${p.year} ${p.factor > 1 ? "▲" : "▼"}`).join(", ");
  const text = [v.note || "Fake data for testing", planted && `Planted anomalies: ${planted}`].filter(Boolean).join(". ");
  showBanner("warning", "Synthetic data.", ` ${text}.`);
}

const THEMES = ["auto", "light", "dark"];
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function setupTheme() {
  let i = 0;
  try { i = Math.max(0, THEMES.indexOf(localStorage.getItem("ember-theme") || "auto")); } catch { /* storage blocked */ }
  if (THEMES.includes(params.get("theme"))) i = THEMES.indexOf(params.get("theme"));
  const apply = () => {
    const t = THEMES[i];
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    $("theme").textContent = `Theme: ${t}`;
    if (state.cal) renderCharts();
  };
  $("theme").addEventListener("click", () => {
    i = (i + 1) % THEMES.length;
    try { localStorage.setItem("ember-theme", THEMES[i]); } catch { /* storage blocked */ }
    apply();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.cal && renderCharts());
  apply();
}

// ---------------------------------------------------------------- tooltip

function showTip(e, t) {
  const tip = $("tip");
  tip.replaceChildren(h("div", null, // h() drops null/false parts and flattens the rows
    h("div", { class: "muted" }, t.title),
    t.lead != null && h("div", null, h("span", { class: "lead" }, t.lead), " ", h("span", { class: "muted" }, t.leadNote || "")),
    t.flag && t.flag !== "normal" && h("div", null, h("b", null, `${FLAG[t.flag]?.glyph || ""} `), FLAG[t.flag]?.label || t.flag),
    (t.rows || []).map(([k, v, key]) => h("div", { class: "row" }, h("span", { class: "k" }, key || null, k), h("b", null, v))),
  ));
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = `${e.clientX + 14 + r.width > innerWidth ? e.clientX - r.width - 14 : e.clientX + 14}px`;
  tip.style.top = `${e.clientY + 14 + r.height > innerHeight ? e.clientY - r.height - 14 : e.clientY + 14}px`;
}
const hideTip = () => { $("tip").hidden = true; };

// ---------------------------------------------------------------- start-up and loading

async function init() {
  setupTheme();
  $("docs-link").href = `${API}/docs`;
  $("filters").addEventListener("submit", (e) => { e.preventDefault(); load(); });
  $("area").addEventListener("change", () => {
    const custom = $("area").value === "_bbox";
    $("bbox-wrap").hidden = !custom;
    if (!custom) load();
  });
  for (const id of ["start", "end"]) $(id).addEventListener("change", load);

  try {
    const hl = await api("GET", "/health");
    setStatus("good", `API OK · ${nf1.format(hl.rows)} rows`, `${API || location.origin} · ${hl.data_file}`);
  } catch (e) {
    if (e.status === 0) return goOffline();
    setStatus("critical", `API up, no data (${e.status})`, e.message);
    showBanner("critical", "No data loaded.", ` ${e.message}. Locally: python scripts/make_fake_data.py, then `
      + "EMBER_DATA=data/fake/harmonized.parquet uvicorn api.main:app --port 8000");
  }
  try { state.areas = (await api("GET", "/areas")).areas; } catch { state.areas = []; }
  try { state.validation = await api("GET", "/validation"); } catch (e) { state.validation = { error: e.message }; }
  start();
}

async function goOffline() {
  state.offline = true;
  try {
    state.areas = (await backupFile("areas.json")).areas;
    state.validation = await backupFile("validation.json").catch((e) => ({ error: e.message }));
  } catch {
    setStatus("critical", "API unreachable");
    showBanner("critical", "Cannot reach the API.", ` Tried ${API || location.origin}. Start it with `
      + "uvicorn api.main:app --port 8000, or open this page with ?api=https://your-service.onrender.com. "
      + "No offline backups were found either.");
    return;
  }
  setStatus("warning", "Offline: saved backups");
  showBanner("warning", "Offline mode.", " The API is unreachable, so this page shows the backups in web/backup/ "
    + "(named areas, 2001–2025). The Evidence Passport and weather need the live API.");
  $("start").value = 2001; $("end").value = 2025;
  for (const id of ["start", "end"]) $(id).disabled = true;
  start();
}

function start() {
  const sel = $("area");
  sel.replaceChildren(...state.areas.map((a) => h("option", { value: a.id }, `${a.name} (${a.id})`)));
  if (!state.offline) sel.append(h("option", { value: "_bbox" }, "Custom bbox…"));
  if (!state.areas.length && !state.offline) { sel.value = "_bbox"; $("bbox-wrap").hidden = false; }
  if (state.areas.some((a) => a.id === params.get("area"))) sel.value = params.get("area");
  if (params.get("bbox") && !state.offline) { // ?bbox=minLon,minLat,maxLon,maxLat: any place
    sel.value = "_bbox"; $("bbox").value = params.get("bbox"); $("bbox-wrap").hidden = false;
  }
  syntheticBanner();
  renderValidation();
  load();
}

function readQuery() {
  const start = Number($("start").value), end = Number($("end").value), v = $("area").value;
  if (v === "_bbox") {
    const bbox = $("bbox").value.replace(/\s+/g, "");
    return { bbox, start, end, label: `bbox ${bbox}` };
  }
  const area = state.areas.find((a) => a.id === v);
  return { area: v, start, end, label: area ? area.name : v };
}

let loadSeq = 0;
async function load() {
  const seq = ++loadSeq, q = readQuery(), msg = $("filter-msg");
  msg.textContent = "";
  msg.className = "muted";
  $("charts").classList.add("loading"); // keep the previous render, dimmed, while loading
  try {
    let cal;
    if (state.offline) cal = await backupFile(`${q.area}.json`);
    else {
      const where = q.area ? `area=${encodeURIComponent(q.area)}` : `bbox=${encodeURIComponent(q.bbox)}`;
      cal = await api("GET", `/calendar?${where}&start=${q.start}&end=${q.end}`);
    }
    if (seq !== loadSeq) return; // a newer request has started
    Object.assign(state, { cal, q, sel: null });
    renderCharts();
    renderMonth();
    loadWeather();
    const pre = params.get("month"); // ?month=YYYY-MM, applied once on the first load
    if (pre && seq === 1) {
      const [y, mo] = pre.split("-").map(Number);
      const m = (cal.months || []).find((r) => r.year === y && r.month === mo);
      if (m) selectMonth(m);
    }
  } catch (e) {
    if (seq !== loadSeq) return;
    msg.textContent = `Request failed: ${e.message}`;
    msg.className = "bad";
    if (!state.cal) $("heatmap").replaceChildren(h("div", { class: "error" }, e.message));
  } finally {
    if (seq === loadSeq) $("charts").classList.remove("loading");
  }
}

// ---------------------------------------------------------------- charts

function renderCharts() {
  const { months = [], meta = {} } = state.cal;
  renderKpis(months, meta);
  if (!months.length) {
    for (const id of ["heatmap", "annual", "clim"]) $(id).replaceChildren(h("p", { class: "muted" }, meta.note || "No data for this selection."));
    for (const id of ["heatmap-legend", "annual-legend", "clim-legend", "tables"]) $(id).replaceChildren();
    state.cells = null;
    return;
  }
  renderHeatmap(months);
  renderAnnual(months);
  renderClim(months, meta);
  renderTables(months);
}

function renderKpis(months, meta) {
  const count = (f) => months.filter((m) => m.flag === f).length;
  const span = months.length ? `${months[0].year}–${months.at(-1).year}` : "no data";
  const tiles = [
    ["Harmonized detections", nfc.format(sum(months.map((m) => m.harmonized))), `${state.q.label}, ${span}`],
    ["Grid cells with detections", meta.cells ?? 0, "0.5° × 0.5° cells"],
    ["Unusual months", `${count("high")} ▲  ${count("low")} ▼`, "vs the same month in other years"],
    ["Low-confidence months", count("low_confidence"), "pre-2012, thin ratio support"],
    ["Critical months", (meta.critical_months || []).map((m) => MONTHS[m - 1]).join(", ") || "none", "top quarter of the year"],
  ];
  $("kpis").replaceChildren(...tiles.map(([label, value, note]) => h("div", { class: "kpi" },
    h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "note" }, note))));
}

function rampColor(t) {
  const steps = isDark() ? [...RAMP].reverse() : RAMP;
  return steps[Math.min(steps.length - 1, Math.max(0, Math.round(t * (steps.length - 1))))];
}
function textOn(hex) { // white or near-black, by the fill's luminance
  const lin = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] > 0.35 ? "#0b0b0b" : "#ffffff";
}

function monthTip(m) {
  const rows = [
    ["MODIS raw", fmt(m.modis_raw)],
    ["VIIRS raw", m.year < 2012 ? "– (from 2012)" : fmt(m.viirs_raw)],
    ["Ratio support", fmt(m.support)],
    ["z-score", fmt(m.z)],
    ["Source", m.year < 2012 ? "MODIS × k" : "VIIRS observed"],
  ];
  for (const k of ["low", "high", "MODIS_days", "VIIRS_days"]) if (k in m) rows.push([k, fmt(m[k])]);
  return { title: `${MONTH_NAMES[m.month - 1]} ${m.year}`, lead: fmt(m.harmonized), leadNote: "harmonized", flag: m.flag, rows };
}

function renderHeatmap(months) {
  const y0 = months[0].year, y1 = months.at(-1).year, years = range(y0, y1);
  const cw = years.length > 30 ? 22 : 34, ch = 22, gap = 2, L = 34, T = 24, B = 22;
  const W = L + years.length * cw, H = T + 12 * ch + B;
  const max = Math.max(0, ...months.map((m) => m.harmonized || 0));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Calendar of harmonized detections, ${y0}–${y1}. Every value is in the table view.` });

  MONTHS.forEach((mo, i) => root.append(svg("text", { x: L - 6, y: T + i * ch + (ch - gap) / 2, class: "tick",
    "text-anchor": "end", "dominant-baseline": "central" }, mo)));
  const every = years.length > 30 ? 5 : 1;
  years.forEach((y, i) => {
    if ((y - y0) % every === 0) root.append(svg("text", { x: L + i * cw + (cw - gap) / 2, y: H - 6, class: "tick", "text-anchor": "middle" }, y));
  });

  state.cells = new Map();
  for (const m of months) {
    const x = L + (m.year - y0) * cw, y = T + (m.month - 1) * ch, v = m.harmonized || 0;
    const fill = v > 0 && max > 0 ? rampColor(Math.sqrt(v / max)) : null; // sqrt: summer peaks would flatten the rest
    const rect = svg("rect", { x, y, width: cw - gap, height: ch - gap, rx: 3, class: "cell", style: `fill:${fill || "var(--empty)"}`,
      onpointermove: (e) => showTip(e, monthTip(m)), onpointerleave: hideTip, onclick: () => selectMonth(m) });
    root.append(rect);
    state.cells.set(`${m.year}-${m.month}`, rect);
    const glyph = FLAG[m.flag]?.glyph;
    if (glyph) root.append(svg("text", { x: x + (cw - gap) / 2, y: y + (ch - gap) / 2, class: "glyph",
      style: `fill:${fill ? textOn(fill) : "var(--ink-2)"}` }, glyph));
  }
  if (y0 < 2012 && y1 >= 2012) { // the sensor switch, in the 2px gap between 2011 and 2012
    const x = L + (2012 - y0) * cw - gap / 2;
    root.append(svg("line", { x1: x, x2: x, y1: T - 18, y2: T + 12 * ch - gap, class: "marker" }),
      svg("text", { x: x - 6, y: T - 8, class: "tick", "text-anchor": "end" }, "← MODIS × k"),
      svg("text", { x: x + 6, y: T - 8, class: "tick" }, "VIIRS observed →"));
  }
  $("heatmap").replaceChildren(root);
  if (state.sel) state.cells.get(`${state.sel.year}-${state.sel.month}`)?.classList.add("sel");

  const steps = isDark() ? [...RAMP].reverse() : RAMP;
  $("heatmap-legend").replaceChildren(
    h("span", { class: "key" }, h("span", { class: "swatch", style: "background:var(--empty)" }), "0"),
    h("span", { class: "key" }, h("span", { class: "ramp" }, steps.filter((_, i) => i % 2 === 0).map((c) => h("span", { style: `background:${c}` }))),
      `1 → ${fmt(max)} (square-root scale)`),
    ...["high", "low", "low_confidence"].map((f) => h("span", { class: "key" }, h("b", null, FLAG[f].glyph), FLAG[f].label)),
  );
}

function renderAnnual(months) {
  const years = [...new Set(months.map((m) => m.year))];
  const rows = years.map((year) => {
    const ms = months.filter((m) => m.year === year);
    return { year, modis: sum(ms.map((m) => m.modis_raw)), viirs: year >= 2012 ? sum(ms.map((m) => m.viirs_raw)) : null,
      harm: sum(ms.map((m) => m.harmonized)) };
  });
  state.annual = rows;
  const W = 880, H = 280, L = 56, R = 110, T = 14, B = 28, pw = W - L - R, ph = H - T - B;
  const ticks = niceTicks(Math.max(...rows.flatMap((r) => [r.modis, r.viirs ?? 0, r.harm]))), top = ticks.at(-1), tf = tickFmt(top);
  const span = Math.max(1, years.at(-1) - years[0]);
  const x = (y) => (years.length === 1 ? L + pw / 2 : L + ((y - years[0]) / span) * pw);
  const yv = (v) => T + ph - (v / top) * ph;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Yearly totals of MODIS raw, VIIRS raw and harmonized detections" });

  for (const t of ticks) root.append(
    svg("line", { x1: L, x2: L + pw, y1: yv(t), y2: yv(t), class: t === 0 ? "baseline" : "gridline" }),
    svg("text", { x: L - 8, y: yv(t), class: "tick", "text-anchor": "end", "dominant-baseline": "central" }, tf(t)));
  const step = years.length > 15 ? 4 : years.length > 8 ? 2 : 1;
  years.forEach((y, i) => { if (i % step === 0) root.append(svg("text", { x: x(y), y: H - 8, class: "tick", "text-anchor": "middle" }, y)); });
  if (years[0] < 2012 && years.at(-1) >= 2012) {
    const xs = x(2011.5);
    root.append(svg("line", { x1: xs, x2: xs, y1: T, y2: T + ph, class: "marker" }),
      svg("text", { x: xs + 6, y: T + 4, class: "tick", "dominant-baseline": "hanging" }, "VIIRS starts (2012)"));
  }
  const path = (k) => `M${rows.filter((r) => r[k] != null).map((r) => `${x(r.year).toFixed(1)},${yv(r[k]).toFixed(1)}`).join("L")}`;
  root.append(svg("path", { d: path("modis"), class: "line s2" }), svg("path", { d: path("harm"), class: "line s1" }));
  for (const r of rows) if (r.viirs != null) root.append(svg("circle", { cx: x(r.year), cy: yv(r.viirs), r: 4, class: "dot s3" }));

  const last = rows.at(-1), ya = yv(last.harm), yb = yv(last.modis);
  if (Math.abs(ya - yb) > 14) { // direct end labels only when they don't collide; the legend always names the series
    root.append(svg("text", { x: x(last.year) + 10, y: ya, class: "axis-strong", "dominant-baseline": "central" }, "Harmonized"),
      svg("text", { x: x(last.year) + 10, y: yb, class: "axis-strong", "dominant-baseline": "central" }, "MODIS raw"));
  }
  const cross = svg("line", { y1: T, y2: T + ph, class: "crosshair", visibility: "hidden" });
  const hit = svg("rect", { x: L, y: T, width: pw, height: ph, fill: "transparent" });
  hit.addEventListener("pointermove", (e) => {
    const b = root.getBoundingClientRect(), px = ((e.clientX - b.left) / b.width) * W;
    const r = rows.reduce((best, c) => (Math.abs(x(c.year) - px) < Math.abs(x(best.year) - px) ? c : best));
    setAttrs(cross, { x1: x(r.year), x2: x(r.year), visibility: "visible" });
    showTip(e, { title: String(r.year), rows: [
      ["Harmonized", fmt(r.harm), h("span", { class: "linekey", style: "background:var(--s1)" })],
      ["MODIS raw", fmt(r.modis), h("span", { class: "linekey", style: "background:var(--s2)" })],
      ["VIIRS raw", r.viirs == null ? "– (from 2012)" : fmt(r.viirs), h("span", { class: "dotkey", style: "background:var(--s3)" })],
    ] });
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  root.append(cross, hit);
  $("annual").replaceChildren(root);
  $("annual-legend").replaceChildren(
    h("span", { class: "key" }, h("span", { class: "linekey", style: "background:var(--s1)" }), "Harmonized (VIIRS terms)"),
    h("span", { class: "key" }, h("span", { class: "linekey", style: "background:var(--s2)" }), "MODIS raw"),
    h("span", { class: "key" }, h("span", { class: "dotkey", style: "background:var(--s3)" }), "VIIRS raw (from 2012)"));
}

function colPath(x, y, w, hgt) { // 4px rounded top, square at the baseline
  const r = Math.min(4, hgt, w / 2);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}
function hbarPath(x, y, w, hgt) { // 4px rounded end, square at the baseline
  const r = Math.min(4, w, hgt / 2);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt - r}Q${x + w},${y + hgt} ${x + w - r},${y + hgt}H${x}Z`;
}

function renderClim(months, meta) {
  const crit = new Set(meta.critical_months || []);
  const med = range(1, 12).map((mo) => median(months.filter((m) => m.month === mo).map((m) => m.harmonized)) ?? 0);
  const W = 460, H = 240, L = 44, R = 8, T = 18, B = 24, pw = W - L - R, ph = H - T - B, band = pw / 12, bw = Math.min(24, band - 8);
  const ticks = niceTicks(Math.max(...med)), top = ticks.at(-1), tf = tickFmt(top), yv = (v) => T + ph - (v / top) * ph;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Median harmonized detections per calendar month" });
  for (const t of ticks) root.append(
    svg("line", { x1: L, x2: L + pw, y1: yv(t), y2: yv(t), class: t === 0 ? "baseline" : "gridline" }),
    svg("text", { x: L - 8, y: yv(t), class: "tick", "text-anchor": "end", "dominant-baseline": "central" }, tf(t)));
  med.forEach((v, i) => {
    const x = L + i * band + (band - bw) / 2, y = yv(v), isCrit = crit.has(i + 1);
    const tip = (e) => showTip(e, { title: MONTH_NAMES[i], lead: fmt(v), leadNote: "median harmonized",
      rows: [["Critical month", isCrit ? "yes" : "no"]] });
    root.append(svg("g", { onpointermove: tip, onpointerleave: hideTip }, // hit target: the whole band, not just the bar
      svg("rect", { x: L + i * band, y: T, width: band, height: ph, fill: "transparent" }),
      svg("path", { d: colPath(x, y, bw, T + ph - y), class: `bar ${isCrit ? "s1" : "deemph"}` })));
    if (isCrit) root.append(svg("text", { x: x + bw / 2, y: y - 5, class: "value-label", "text-anchor": "middle" }, fmt(v)));
    root.append(svg("text", { x: x + bw / 2, y: H - 8, class: "tick", "text-anchor": "middle" }, MONTHS[i]));
  });
  $("clim").replaceChildren(root);
  $("clim-legend").replaceChildren(
    h("span", { class: "key" }, h("span", { class: "swatch", style: "background:var(--s1)" }), "Critical month"),
    h("span", { class: "key" }, h("span", { class: "swatch", style: "background:var(--deemph)" }), "Other month"));
}

function renderValidation() {
  const v = state.validation, el = $("validation");
  if (!v || v.error) {
    el.replaceChildren(h("div", { class: "error" }, `Validation report unavailable: ${v?.error || "no data"}`));
    return;
  }
  const rows = [
    ["No correction (raw MODIS)", v.error_pct_uncorrected, false],
    [`One global ratio (k = ${fmt(v.k_global)})`, v.error_pct_global_ratio, false],
    ["Ratio per cell and month (what the API uses)", v.error_pct_cell_month_ratio, true],
  ];
  const W = 460, rowH = 46, L = 2, R = 60, H = rows.length * rowH, pw = W - L - R;
  const max = Math.max(1, ...rows.map((r) => r[1] || 0));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Validation error by method; values are printed on the bars" });
  rows.forEach(([label, val, used], i) => {
    const y = i * rowH, w = Math.max(2, ((val || 0) / max) * pw);
    root.append(svg("text", { x: L, y: y + 13, class: used ? "axis-strong" : "tick" }, label),
      svg("path", { d: hbarPath(L, y + 20, w, 18), class: `bar ${used ? "s1" : "deemph"}` }),
      svg("text", { x: L + w + 6, y: y + 29, class: "value-label", "dominant-baseline": "central" }, `${fmt(val)}%`));
  });
  el.replaceChildren(
    h("p", { class: "muted" }, "Error predicting held-out VIIRS months from MODIS: mean absolute monthly error as a % of the mean observed count. Lower is better."),
    root,
    h("p", { class: "muted" }, `Fit on ${(v.train_years || []).join("–")}, tested on ${(v.test_years || []).join("–")} (${v.test_months ?? "?"} months). `,
      v.series ? "A per-month series is included in GET /validation. " : "",
      v.synthetic ? h("b", null, "Synthetic data: these numbers describe the fake dataset, not FIRMS.") : ""));
}

function renderTables(months) {
  const table = (title, head, rows) => h("div", { class: "table-wrap" }, h("h3", null, title),
    h("table", null, h("thead", null, h("tr", null, head.map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((c) => h("td", null, c)))))));
  $("tables").replaceChildren(
    table("Monthly calendar", ["Month", "MODIS raw", "VIIRS raw", "Harmonized", "Support", "z", "Flag"],
      months.map((m) => [`${m.year}-${String(m.month).padStart(2, "0")}`, fmt(m.modis_raw), m.year < 2012 ? "–" : fmt(m.viirs_raw),
        fmt(m.harmonized), fmt(m.support), fmt(m.z), m.flag])),
    table("Yearly totals", ["Year", "MODIS raw", "VIIRS raw", "Harmonized"],
      state.annual.map((r) => [r.year, fmt(r.modis), r.viirs == null ? "–" : fmt(r.viirs), fmt(r.harm)])),
    table("Typical year (median per month)", ["Month", "Median harmonized"],
      range(1, 12).map((mo) => [MONTH_NAMES[mo - 1], fmt(median(months.filter((m) => m.month === mo).map((m) => m.harmonized)))])));
}

// ---------------------------------------------------------------- selected month, passport, weather

function selectMonth(m) {
  state.sel = m;
  state.cells?.forEach((r) => r.classList.remove("sel"));
  state.cells?.get(`${m.year}-${m.month}`)?.classList.add("sel");
  renderMonth();
  if (innerWidth < 900) $("month-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderMonth() {
  const m = state.sel, el = $("month");
  if (!m) { el.replaceChildren(h("p", { class: "muted" }, "Click a cell in the calendar.")); return; }
  const f = FLAG[m.flag] || { glyph: "", label: m.flag };
  const facts = [
    ["Harmonized", `${fmt(m.harmonized)} detections (VIIRS terms)`],
    ["How", m.year < 2012 ? "MODIS × k (no VIIRS yet)" : "Observed VIIRS count"],
    ["MODIS raw", fmt(m.modis_raw)],
    ["VIIRS raw", m.year < 2012 ? "– (from 2012)" : fmt(m.viirs_raw)],
    ["Ratio support", `${fmt(m.support)} MODIS training detections`],
    ["z-score", `${fmt(m.z)} vs the same month in other years`],
    ["Flag", `${f.glyph} ${f.label}`.trim()],
  ];
  for (const k of ["low", "high", "MODIS_days", "VIIRS_days"]) if (k in m) facts.push([k, fmt(m[k])]);
  const out = h("div");
  const btn = h("button", { class: "btn", type: "button", disabled: state.offline, title: state.offline ? "Needs the live API" : null },
    "Evidence Passport (POST /investigate)");
  btn.addEventListener("click", () => investigate(m, out, btn));
  el.replaceChildren(
    h("h3", null, `${MONTH_NAMES[m.month - 1]} ${m.year} · ${state.q.label}`),
    h("dl", { class: "facts" }, facts.map(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    btn, out);
}

async function investigate(m, out, btn) {
  const q = state.q;
  const where = q.area ? { area: q.area } : { bbox: q.bbox.split(",").map(Number) };
  btn.disabled = true;
  out.replaceChildren(h("p", { class: "muted" }, "Asking the API…"));
  try {
    const p = await api("POST", "/investigate", { ...where, year: m.year, month: m.month });
    const list = (items) => h("ul", null, (items || []).map((x) => h("li", null, x)));
    out.replaceChildren(h("div", { class: "passport" },
      h("h3", null, p.question),
      h("p", null, p.summary_template),
      h("p", null, h("b", null, "Confidence: "), p.confidence_statement),
      h("b", null, "Quality warnings"), list(p.quality_warnings),
      h("b", null, "Sources"), list(p.sources),
      h("p", { class: "muted" }, p.ai_checked ? ["AI wording (numbers checked): ", p.ai_text] : `AI wording: ${p.ai_reason || "none"}`)));
  } catch (e) {
    out.replaceChildren(h("div", { class: "error" }, h("b", null, "POST /investigate failed. "), e.message,
      e.status >= 500 ? " Check the API terminal for the traceback." : ""));
  } finally {
    btn.disabled = false;
  }
}

async function loadWeather() {
  const el = $("weather"), q = state.q;
  if (state.offline) return el.replaceChildren(h("p", { class: "muted" }, "Needs the live API."));
  if (!q.area) return el.replaceChildren(h("p", { class: "muted" }, "Weather needs a named area (GET /weather?area=<id>)."));
  el.replaceChildren(h("p", { class: "muted" }, "Loading the forecast…"));
  try {
    const w = await api("GET", `/weather?area=${encodeURIComponent(q.area)}`);
    if (state.q !== q) return;
    const hc = w.historical_context || {};
    el.replaceChildren(
      h("p", { class: "muted" }, `${w.label}. ${w.source}, point ${w.point?.latitude}, ${w.point?.longitude}, fetched ${w.fetched_at}.`),
      h("table", null,
        h("thead", null, h("tr", null, ["Date", "Max °C", "Min RH %", "Max gust km/h", "Rain mm"].map((c) => h("th", { scope: "col" }, c)))),
        h("tbody", null, (w.days || []).map((d) => h("tr", null,
          [d.date, fmt(d.temp_max_c), fmt(d.humidity_min_pct), fmt(d.gust_max_kmh), fmt(d.rain_total_mm)].map((c) => h("td", null, c)))))),
      hc.available
        ? h("p", null, `${MONTH_NAMES[hc.month - 1]} usually has ${fmt(hc.median_harmonized_same_month)} harmonized detections here`
          + `${hc.is_critical_month ? " and is a critical month" : ""}.`)
        : h("div", { class: "error" }, `Historical context unavailable: ${hc.reason || "unknown"}`));
  } catch (e) {
    if (state.q === q) el.replaceChildren(h("div", { class: "error" }, h("b", null, "GET /weather failed. "), e.message));
  }
}

init();
