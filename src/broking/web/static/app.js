"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || res.statusText));
  return data;
}

// --- Tabs -------------------------------------------------------------------------------
function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  if (name === "journal") loadJournal();
  if (name === "settings") loadSettings();
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// --- Status & Jobs ----------------------------------------------------------------------
let polling = null;
let lastFinished = null;

async function loadStatus() {
  const s = await api("/api/status");
  const badge = $("#paper-badge");
  badge.textContent = s.paper_trading ? "Paper-Trading" : "LIVE";
  badge.classList.toggle("live", !s.paper_trading);
  $("#t-last").textContent = fmtDate(s.last_screen);
  $("#t-next").textContent = s.next_run ? fmtDate(s.next_run) : "aus";
  $("#t-theses").textContent = `${s.open_theses} / ${s.due_theses}`;

  const j = s.job;
  const busy = j.running;
  $("#btn-run").disabled = $("#btn-screen").disabled = busy;
  const step = { fetch: "lade Kurse …", screen: "rechne Screen …" }[j.step] || "läuft …";
  if (busy) $("#job-status").textContent = `${j.trigger === "zeitplan" ? "Zeitplan: " : ""}${step}`;
  else if (j.error) $("#job-status").textContent = `Fehler: ${j.error}`;
  else if (j.finished) {
    const f = j.result?.fetch;
    const failed = f ? Object.keys(f.failed || {}) : [];
    $("#job-status").textContent = `Fertig ${fmtDate(j.finished)}` +
      (f ? ` · ${f.fetched.length} geladen, ${f.cached.length} aus Cache` : "") +
      (failed.length ? ` · fehlgeschlagen: ${failed.join(", ")}` : "");
  }
  if (j.finished && j.finished !== lastFinished) {
    if (lastFinished !== null) loadScreen();
    lastFinished = j.finished;
  }
  if (busy && !polling) polling = setInterval(loadStatus, 1500);
  if (!busy && polling) { clearInterval(polling); polling = null; }
}

async function runJob(steps) {
  try {
    await api("/api/run", { method: "POST", body: { steps, force: $("#chk-force").checked } });
  } catch (e) {
    $("#job-status").textContent = e.message;
  }
  loadStatus();
}
$("#btn-run").addEventListener("click", () => runJob(["fetch", "screen"]));
$("#btn-screen").addEventListener("click", () => runJob(["screen"]));

// --- Screen-Tabelle ---------------------------------------------------------------------
let selected = null;

async function loadScreen() {
  const s = await api("/api/screen");
  $("#t-cands").textContent = s.created ? `${s.candidates.length} von ${s.screened}` : "–";
  const tbody = $("#cand-table tbody");
  tbody.innerHTML = s.candidates.length
    ? s.candidates.map((c, i) => `
      <tr data-ticker="${esc(c.ticker)}" tabindex="0">
        <td class="muted">${i + 1}</td><td><strong>${esc(c.ticker)}</strong></td>
        <td class="num">${fmt(c.close)}</td><td class="num">${fmt(c.score)}</td>
        <td class="num">${fmt(c.rsi14, 1)}</td><td class="num">${fmt(c.mom20_pct, 1)} %</td>
        <td class="num">${fmt(c.atr_pct)} %</td>
      </tr>`).join("")
    : `<tr><td colspan="7" class="muted">Noch kein Screen – „Kurse laden + Screen“ starten.</td></tr>`;
  $$("tr[data-ticker]", tbody).forEach((tr) => {
    const open = () => selectTicker(tr.dataset.ticker);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => e.key === "Enter" && open());
  });

  const rej = Object.entries(s.rejected || {});
  $("#rejects").innerHTML =
    (rej.length ? `<ul>${rej.map(([t, why]) => `<li><button class="link" data-ticker="${esc(t)}">${esc(t)}</button>: ${esc(why.join(", "))}</li>`).join("")}</ul>` : "") +
    (s.missing_data?.length ? `<p>Fehlende Daten: ${esc(s.missing_data.join(", "))}</p>` : "") ||
    `<p class="muted">Nichts aussortiert.</p>`;
  $$("#rejects button[data-ticker]").forEach((b) => b.addEventListener("click", () => selectTicker(b.dataset.ticker)));

  if (!selected && s.candidates.length) selectTicker(s.candidates[0].ticker);
  else if (selected) selectTicker(selected);
}

// --- Chart ------------------------------------------------------------------------------
let chartDays = 250;
const SERIES = [
  { key: "close", label: "Schlusskurs", color: "var(--series-1)" },
  { key: "sma50", label: "SMA 50", color: "var(--series-2)" },
  { key: "sma200", label: "SMA 200", color: "var(--series-3)" },
];
$("#legend").innerHTML = SERIES.map((s) => `<span style="--c:${s.color}">${s.label}</span>`).join("");

$$(".seg button").forEach((b) => b.addEventListener("click", () => {
  chartDays = Number(b.dataset.days);
  $$(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (selected) selectTicker(selected);
}));

async function selectTicker(ticker) {
  selected = ticker;
  $$("#cand-table tbody tr").forEach((tr) => tr.classList.toggle("active", tr.dataset.ticker === ticker));
  $("#chart-title").textContent = ticker;
  try {
    const d = await api(`/api/prices/${encodeURIComponent(ticker)}?days=${chartDays}`);
    drawChart(d);
    renderMetrics(d.metrics);
    $("#btn-thesis-from-chart").hidden = false;
  } catch (e) {
    $("#chart").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    $("#metrics").innerHTML = "";
  }
}

function niceTicks(min, max, count = 5) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function drawChart(d) {
  const el = $("#chart");
  const W = el.clientWidth || 600, H = el.clientHeight || 300;
  const m = { t: 10, r: 64, b: 24, l: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = d.dates.length;
  const vals = SERIES.flatMap((s) => d[s.key]).filter((v) => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.06 || 1;
  lo -= pad; hi += pad;
  const x = (i) => m.l + (n <= 1 ? 0 : (i / (n - 1)) * iw);
  const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

  const path = (arr) => {
    let s = "", pen = false;
    arr.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      s += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return s;
  };

  const yTicks = niceTicks(lo, hi);
  const xIdx = [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  const monthFmt = (iso) => new Date(iso).toLocaleDateString("de-DE", { month: "short", year: "2-digit" });

  // Endbeschriftungen rechts, gegen Überlappung auseinanderschieben
  const ends = SERIES.map((s) => ({ s, v: d[s.key][n - 1] })).filter((e) => e.v != null)
    .map((e) => ({ ...e, y: y(e.v) })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;

  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kursverlauf ${esc(d.ticker)} mit SMA 50 und SMA 200">
      <g class="grid">${yTicks.map((t) => `<line x1="${m.l}" x2="${m.l + iw}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>
      <g class="axis">
        ${yTicks.map((t) => `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${fmt(t, t % 1 ? 1 : 0)}</text>`).join("")}
        ${xIdx.map((i, k) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === 3 ? "end" : "middle"}">${monthFmt(d.dates[i])}</text>`).join("")}
      </g>
      ${SERIES.slice().reverse().map((s) => `<path class="line" d="${path(d[s.key])}" style="stroke:${s.color}"/>`).join("")}
      ${ends.map((e) => `<text class="end-label" x="${m.l + iw + 6}" y="${e.y + 4}">${fmt(e.v)}</text>`).join("")}
      <g class="hover" visibility="hidden">
        <line class="crosshair" y1="${m.t}" y2="${m.t + ih}"/>
        ${SERIES.map((s) => `<circle class="dot" r="4" style="fill:${s.color}" data-key="${s.key}"/>`).join("")}
      </g>
      <rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" class="hit"/>
    </svg>`;

  const svg = $("svg", el), hover = $(".hover", svg), tip = $("#tooltip");
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    hover.setAttribute("visibility", "visible");
    $(".crosshair", hover).setAttribute("x1", x(i));
    $(".crosshair", hover).setAttribute("x2", x(i));
    $$(".dot", hover).forEach((c) => {
      const v = d[c.dataset.key][i];
      c.setAttribute("visibility", v == null ? "hidden" : "visible");
      if (v != null) { c.setAttribute("cx", x(i)); c.setAttribute("cy", y(v)); }
    });
    tip.innerHTML = `<div class="tt-date">${new Date(d.dates[i]).toLocaleDateString("de-DE")}</div>` +
      SERIES.map((s) => `<div class="tt-row"><span style="--c:${s.color}">${s.label}</span><strong>${fmt(d[s.key][i])}</strong></div>`).join("");
    tip.hidden = false;
    const tx = ev.clientX + 16 + tip.offsetWidth > window.innerWidth ? ev.clientX - tip.offsetWidth - 16 : ev.clientX + 16;
    tip.style.left = `${tx}px`;
    tip.style.top = `${Math.max(8, ev.clientY - tip.offsetHeight - 12)}px`;
  };
  const leave = () => { hover.setAttribute("visibility", "hidden"); tip.hidden = true; };
  const hit = $(".hit", svg);
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", leave);
}

function renderMetrics(mt) {
  if (!mt) { $("#metrics").innerHTML = `<p class="muted small">Zu wenig Historie für Kennzahlen.</p>`; return; }
  const items = [
    ["Stand", new Date(mt.date).toLocaleDateString("de-DE")], ["Kurs", fmt(mt.close)],
    ["RSI 14", fmt(mt.rsi14, 1)], ["ATR 14", `${fmt(mt.atr14)} (${fmt(mt.atr_pct)} %)`],
    ["Mom. 20T", `${fmt(mt.mom20_pct, 1)} %`], ["Mom. 60T", `${fmt(mt.mom60_pct, 1)} %`],
    ["Volumen/Ø", fmt(mt.vol_ratio)], ["Max. DD 1J", `${fmt(mt.dd_252_pct, 1)} %`],
  ];
  $("#metrics").innerHTML = items.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#metrics").dataset.close = mt.close;
  $("#metrics").dataset.atr = mt.atr14;
}

$("#btn-thesis-from-chart").addEventListener("click", () => {
  const f = $("#thesis-form");
  const close = Number($("#metrics").dataset.close), atr = Number($("#metrics").dataset.atr);
  f.ticker.value = selected;
  f.direction.value = "up";
  if (close) {
    f.entry.value = close.toFixed(2);
    if (atr) { f.target.value = (close + 2 * atr).toFixed(2); f.stop.value = (close - 1.5 * atr).toFixed(2); }
  }
  showTab("journal");
  f.rationale.focus();
});
window.addEventListener("resize", () => { if (selected && !$("#tab-overview").hidden) selectTicker(selected); });

// --- Journal ----------------------------------------------------------------------------
const STATUS_LABEL = { open: "offen", hit: "Treffer", miss: "Fehlschlag", cancelled: "storniert" };

async function loadJournal() {
  const filter = $("#thesis-filter").value;
  const [list, all, acts] = await Promise.all([
    api(`/api/theses${filter ? `?status=${filter}` : ""}`), api("/api/theses"), api("/api/actions?limit=50"),
  ]);
  const hits = all.filter((t) => t.status === "hit").length, misses = all.filter((t) => t.status === "miss").length;
  $("#hit-rate").textContent = hits + misses
    ? `Trefferquote: ${Math.round((hits / (hits + misses)) * 100)} % (${hits} von ${hits + misses} ausgewertet)`
    : "Noch keine ausgewerteten Thesen.";

  const today = new Date().toISOString().slice(0, 10);
  $("#thesis-list").innerHTML = list.length ? list.map((t) => {
    const due = t.status === "open" && t.deadline <= today;
    const cls = t.status === "hit" ? "ok" : t.status === "miss" ? "bad" : due ? "due" : "open";
    const label = due ? "fällig" : STATUS_LABEL[t.status];
    return `
      <div class="thesis" data-id="${esc(t.id)}">
        <div class="thesis-head">
          <strong>${esc(t.ticker)} ${t.direction === "up" ? "↑" : "↓"} ${fmt(t.target)}</strong>
          <span class="status ${cls}">${label}</span>
        </div>
        <div class="small muted">Einstieg ${fmt(t.entry)}${t.stop ? ` · Stop ${fmt(t.stop)}` : ""} · Frist ${esc(t.deadline)} · ${esc(t.model || "–")}</div>
        <p>${esc(t.rationale)}</p>
        ${t.status === "open" ? `
          <div class="actions">
            <input type="number" step="any" placeholder="Kurs" aria-label="Kurs bei Auswertung">
            <button data-status="hit">Treffer</button><button data-status="miss">Fehlschlag</button>
            <button data-status="cancelled">Stornieren</button>
          </div>` : t.resolved_price != null ? `<div class="small muted">Ausgewertet bei ${fmt(t.resolved_price)} am ${fmtDate(t.resolved_at)}</div>` : ""}
      </div>`;
  }).join("") : `<p class="muted">Keine Thesen in dieser Ansicht.</p>`;

  $$("#thesis-list .actions button").forEach((b) => b.addEventListener("click", async () => {
    const card = b.closest(".thesis");
    const price = $("input", card).value;
    try {
      await api(`/api/theses/${encodeURIComponent(card.dataset.id)}/resolve`, {
        method: "POST", body: { status: b.dataset.status, price: price ? Number(price) : null },
      });
      loadJournal(); loadStatus();
    } catch (e) { alert(e.message); }
  }));

  $("#action-table tbody").innerHTML = acts.length
    ? acts.map((a) => { const { ts, ...rest } = a; return `<tr><td>${fmtDate(ts)}</td><td><code>${esc(JSON.stringify(rest))}</code></td></tr>`; }).join("")
    : `<tr><td colspan="2" class="muted">Noch keine Aktionen protokolliert.</td></tr>`;
}
$("#thesis-filter").addEventListener("change", loadJournal);

$("#thesis-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#thesis-msg");
  const body = {
    ticker: f.ticker.value.trim(), direction: f.direction.value, entry: Number(f.entry.value),
    target: Number(f.target.value), stop: f.stop.value ? Number(f.stop.value) : null,
    horizon_days: Number(f.horizon_days.value), rationale: f.rationale.value, model: f.model.value,
  };
  try {
    const t = await api("/api/theses", { method: "POST", body });
    msg.className = "small status ok"; msg.textContent = `Gespeichert, Frist ${t.deadline}`;
    f.rationale.value = "";
    $("#thesis-filter").value = "open";
    loadJournal(); loadStatus();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

// --- Risiko -----------------------------------------------------------------------------
function riskBody(f) {
  let positions;
  try { positions = JSON.parse(f.positions.value || "{}"); } catch { throw new Error("Positionen: ungültiges JSON"); }
  return {
    order: {
      ticker: f.ticker.value.trim(), side: f.side.value, qty: Number(f.qty.value),
      price: Number(f.price.value), stop: f.stop.value ? Number(f.stop.value) : null,
    },
    portfolio: { equity: Number(f.equity.value), cash: Number(f.cash.value), day_pnl: Number(f.day_pnl.value || 0), positions },
  };
}

$("#risk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#risk-result");
  try {
    const r = await api("/api/risk/check", { method: "POST", body: riskBody(e.target) });
    out.className = "";
    out.innerHTML = `
      <div class="status ${r.approved ? "ok" : "bad"}">${r.approved ? "Freigegeben" : "Blockiert"}</div>
      ${r.violations.length ? `<ul class="violations">${r.violations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
      ${r.suggested_qty != null ? `<p>Empfohlene Stückzahl nach Limits: <strong>${r.suggested_qty}</strong></p>` : ""}`;
  } catch (err) { out.className = "status bad"; out.textContent = err.message; }
});

$("#btn-size").addEventListener("click", async () => {
  const f = $("#risk-form"), out = $("#risk-result");
  if (!f.price.value || !f.stop.value) { out.className = "muted"; out.textContent = "Für die Stückzahl Preis und Stop eintragen."; return; }
  const q = new URLSearchParams({ price: f.price.value, stop: f.stop.value, equity: f.equity.value });
  const r = await api(`/api/risk/size?${q}`);
  f.qty.value = r.qty;
  out.className = "";
  out.innerHTML = `<p>Maximale Stückzahl nach Risiko- und Größenlimit: <strong>${r.qty}</strong></p>`;
});

// --- Einstellungen ----------------------------------------------------------------------
let config = null;

async function loadSettings() {
  config = await api("/api/config");
  const f = $("#settings-form");
  f.universe.value = config.universe.join("\n");
  f.benchmark.value = config.benchmark || "";
  for (const k of ["top_n", "min_history_days", "min_avg_volume", "rsi_min", "rsi_max"]) f[k].value = config.screen[k];
  for (const k of ["max_position_pct", "max_risk_per_trade_pct", "max_daily_loss_pct", "max_open_positions"]) f[k].value = config.risk[k];
  f.enforce_market_hours.checked = config.risk.enforce_market_hours;
  const sched = config.schedule || { enabled: false, times: [], tz: "Europe/Berlin" };
  f.schedule_enabled.checked = sched.enabled;
  f.schedule_times.value = sched.times.join(", ");
  $("#sched-tz").textContent = sched.tz;
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#settings-msg");
  const body = {
    ...config,
    universe: f.universe.value.split(/[\s,;]+/).map((t) => t.trim().toUpperCase()).filter(Boolean),
    benchmark: f.benchmark.value.trim().toUpperCase(),
    screen: Object.fromEntries(["top_n", "min_history_days", "min_avg_volume", "rsi_min", "rsi_max"].map((k) => [k, Number(f[k].value)])),
    risk: {
      ...config.risk,
      ...Object.fromEntries(["max_position_pct", "max_risk_per_trade_pct", "max_daily_loss_pct", "max_open_positions"].map((k) => [k, Number(f[k].value)])),
      enforce_market_hours: f.enforce_market_hours.checked,
    },
    schedule: { ...config.schedule, enabled: f.schedule_enabled.checked, times: f.schedule_times.value.split(/[\s,;]+/).filter(Boolean) },
  };
  try {
    await api("/api/config", { method: "PUT", body });
    msg.className = "small status ok"; msg.textContent = "Gespeichert";
    loadSettings(); loadStatus();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

// --- Start ------------------------------------------------------------------------------
loadStatus().then(loadScreen).catch((e) => { $("#job-status").textContent = e.message; });
setInterval(() => { if (!polling) loadStatus().catch(() => {}); }, 30000);
