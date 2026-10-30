"use strict";

// --- Helfer -----------------------------------------------------------------------------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (v == null || v === "" ? null : Number(v));
const fmt = (v, d = 2) => (num(v) == null || Number.isNaN(num(v)) ? "–" : num(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v, cur = "USD", d = 2) => (num(v) == null ? "–" : num(v).toLocaleString("de-DE", { style: "currency", currency: cur, minimumFractionDigits: d, maximumFractionDigits: d }));
const signed = (v, d = 2, suffix = "") => (num(v) == null ? "–" : (num(v) > 0 ? "+" : "") + fmt(v, d) + suffix);
const cls = (v) => (num(v) > 0 ? "pos" : num(v) < 0 ? "neg" : "");
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");
const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString("de-DE") : "–");

class ApiError extends Error {
  constructor(msg, status, data) { super(msg); this.status = status; this.data = data; }
}

async function api(path, opts = {}) {
  const headers = { "X-Broking": "1" };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ") : res.statusText;
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

function confirmDialog(title, html, okLabel = "Bestätigen") {
  const d = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").innerHTML = html;
  $("#confirm-ok").textContent = okLabel;
  d.returnValue = "";
  d.showModal();
  return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }));
}

function segmented(el, onChange) {
  $$("button", el).forEach((b) => b.addEventListener("click", () => {
    $$("button", el).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    onChange(b.dataset.v);
  }));
  return () => $("button[aria-pressed=true]", el)?.dataset.v;
}

// --- Navigation -------------------------------------------------------------------------
const PAGES = ["dashboard", "markt", "handel", "journal", "einstellungen"];
const loaders = {};

function route() {
  const page = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard";
  $$(".nav a").forEach((a) => (a.dataset.page === page ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  $$(".page").forEach((p) => (p.hidden = p.id !== `page-${page}`));
  loaders[page]?.();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

// --- Liniendiagramm (eine Achse, Crosshair + Tooltip) -----------------------------------
function niceTicks(min, max, count = 5) {
  const span = max - min || 1;
  const mag = 10 ** Math.floor(Math.log10(span / count));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function lineChart(el, { dates, series, valueFmt = (v) => fmt(v), baseline = null, label = "Diagramm" }) {
  const W = el.clientWidth || 600, H = el.clientHeight || 300;
  const m = { t: 10, r: 66, b: 24, l: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b, n = dates.length;
  const vals = series.flatMap((s) => s.values).filter((v) => v != null);
  if (!n || !vals.length) { el.innerHTML = `<p class="empty">Keine Daten.</p>`; return; }
  if (baseline != null) vals.push(baseline);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.01 || 1;
  lo -= pad; hi += pad;
  const x = (i) => m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
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
  const xIdx = [...new Set(iw < 420 ? [0, Math.round((n - 1) / 2), n - 1] : [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1])];
  const span = (new Date(dates[n - 1]) - new Date(dates[0])) / 864e5;
  const xFmt = (iso) => new Date(iso).toLocaleDateString("de-DE", span > 120 ? { month: "short", year: "2-digit" } : { day: "2-digit", month: "short" });
  const tickFmt = (t) => fmt(t, Math.abs(hi - lo) < 10 ? 1 : 0);

  const ends = series.map((s) => {
    const i = s.values.findLastIndex((v) => v != null);
    return i < 0 ? null : { s, v: s.values[i], y: y(s.values[i]) };
  }).filter(Boolean).sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;

  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
      <g class="grid">${yTicks.map((t) => `<line x1="${m.l}" x2="${m.l + iw}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>
      ${baseline != null ? `<line class="base" x1="${m.l}" x2="${m.l + iw}" y1="${y(baseline)}" y2="${y(baseline)}"/>` : ""}
      <g class="axis">
        ${yTicks.map((t) => `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${tickFmt(t)}</text>`).join("")}
        ${xIdx.map((i, k) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === xIdx.length - 1 ? "end" : "middle"}">${xFmt(dates[i])}</text>`).join("")}
      </g>
      ${series.slice().reverse().map((s) => `<path class="line" d="${path(s.values)}" style="stroke:${s.color}"/>`).join("")}
      ${ends.map((e) => `<text class="end-label" x="${m.l + iw + 6}" y="${e.y + 4}">${valueFmt(e.v)}</text>`).join("")}
      <g class="hover" visibility="hidden">
        <line class="crosshair" y1="${m.t}" y2="${m.t + ih}"/>
        ${series.map((s, k) => `<circle class="dot" r="4" style="fill:${s.color}" data-k="${k}"/>`).join("")}
      </g>
      <rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/>
    </svg>`;

  const svg = $("svg", el), hover = $(".hover", svg), tip = $("#tooltip"), cross = $(".crosshair", hover);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    hover.setAttribute("visibility", "visible");
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    $$(".dot", hover).forEach((c) => {
      const v = series[c.dataset.k].values[i];
      c.setAttribute("visibility", v == null ? "hidden" : "visible");
      if (v != null) { c.setAttribute("cx", x(i)); c.setAttribute("cy", y(v)); }
    });
    tip.innerHTML = `<div class="tt-date">${fmtDay(dates[i])}</div>` +
      series.map((s) => `<div class="tt-row"><span style="--c:${s.color}">${esc(s.label)}</span><strong>${valueFmt(s.values[i])}</strong></div>`).join("");
    tip.hidden = false;
    const tx = ev.clientX + 16 + tip.offsetWidth > innerWidth ? ev.clientX - tip.offsetWidth - 16 : ev.clientX + 16;
    tip.style.left = `${tx}px`;
    tip.style.top = `${Math.max(8, ev.clientY - tip.offsetHeight - 12)}px`;
  };
  const leave = () => { hover.setAttribute("visibility", "hidden"); tip.hidden = true; };
  const hit = $(".hit", svg);
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", leave);
}

const legend = (el, series) => (el.innerHTML = series.map((s) => `<span style="--c:${s.color}">${esc(s.label)}</span>`).join(""));

// --- Status, Jobs, Broker-Grunddaten ----------------------------------------------------
const state = { status: null, broker: null, screen: null, config: null };
let polling = null, lastFinished = null;

async function loadStatus() {
  const s = await api("/api/status");
  state.status = s;
  const j = s.job;
  $("#s-last").textContent = fmtDate(s.last_screen);
  $("#s-next").textContent = s.next_run ? fmtDate(s.next_run) : "aus";
  $("#s-open").textContent = s.open_theses;
  $("#s-due").textContent = s.due_theses;
  $("#nav-due").hidden = !s.due_theses;
  $("#nav-due").textContent = s.due_theses;
  $("#mode-pill").textContent = s.paper_trading ? "Paper-Trading" : "LIVE";
  $("#mode-pill").className = `pill ${s.paper_trading ? "good" : "bad"}`;

  const steps = { fetch: "lade Kurse", screen: "rechne Screen", evaluate: "werte Thesen aus", sync: "gleiche Depot ab" };
  let line = "";
  if (j.running) line = `${j.trigger === "zeitplan" ? "Zeitplan: " : ""}${steps[j.step] || "startet"} …`;
  else if (j.error) line = `Fehler: ${j.error}`;
  else if (j.finished) {
    const f = j.result?.fetch, failed = f ? Object.keys(f.failed || {}) : [];
    line = `Fertig ${fmtDate(j.finished)}` + (f ? ` · ${f.fetched.length} geladen, ${f.cached.length} aus Cache` : "") +
      (j.result?.evaluate ? ` · ${j.result.evaluate.resolved} Thesen ausgewertet` : "") +
      (failed.length ? ` · fehlgeschlagen: ${failed.join(", ")}` : "");
  }
  $("#job-line").textContent = line;
  $("#s-job").textContent = j.running ? "läuft" : j.error ? "Fehler" : j.finished ? "fertig" : "–";
  $$("[data-run]").forEach((b) => (b.disabled = j.running));

  if (j.finished && j.finished !== lastFinished) {
    if (lastFinished !== null) { refreshAfterJob(); toast(j.error ? `Job-Fehler: ${j.error}` : "Aktualisierung fertig"); }
    lastFinished = j.finished;
  }
  if (j.running && !polling) polling = setInterval(() => loadStatus().catch(() => {}), 1500);
  if (!j.running && polling) { clearInterval(polling); polling = null; }
  return s;
}

function refreshAfterJob() {
  loadScreen().catch(() => {});
  const page = location.hash.slice(1) || "dashboard";
  loaders[page]?.();
}

async function runJob(kind) {
  const steps = { all: ["fetch", "screen", "evaluate", "sync"], screen: ["screen"], "fetch-screen": ["fetch", "screen"] }[kind];
  try {
    await api("/api/run", { method: "POST", body: { steps, force: $("#chk-force").checked } });
  } catch (e) { toast(e.message); }
  loadStatus();
}
$$("[data-run]").forEach((b) => b.addEventListener("click", () => runJob(b.dataset.run)));

async function loadBroker() {
  const b = await api("/api/broker");
  state.broker = b;
  const ok = b.configured && !b.error;
  $("#connect-notice").hidden = b.configured;
  $("#broker-line").textContent = !b.configured ? "Broker: nicht verbunden" : b.error ? "Broker: Fehler" : `Alpaca ${b.account?.account_number || ""}`;
  $("#clock-line").textContent = ok && b.clock ? (b.clock.is_open ? `US-Börse offen bis ${fmtDate(b.clock.next_close)}` : `US-Börse zu · öffnet ${fmtDate(b.clock.next_open)}`) : "";
  const pill = $("#broker-pill");
  pill.textContent = !b.configured ? "nicht verbunden" : b.error ? "Fehler" : "verbunden";
  pill.className = `pill ${!b.configured ? "" : b.error ? "bad" : "good"}`;
  $("#broker-info").innerHTML = !b.configured ? "" : b.error ? `<span class="neg">${esc(b.error)}</span>` :
    `Konto ${esc(b.account.account_number)} · Key ${esc(b.key_id)} · Status ${esc(b.account.status)}${b.account.trading_blocked ? ' · <span class="neg">Handel gesperrt</span>' : ""}`;
  $("#btn-disconnect").hidden = !b.configured;
  return b;
}

// --- Dashboard --------------------------------------------------------------------------
const histSeg = segmented($("#hist-seg"), () => loadHistory());

loaders.dashboard = async () => {
  const [b] = await Promise.all([loadBroker().catch(() => null), loadStatus().catch(() => null), loadScreen().catch(() => null), loadStatsKpi()]);
  const a = b?.configured && !b.error ? b.account : null;
  if (a) {
    const eq = num(a.equity), last = num(a.last_equity), day = eq - last;
    $("#k-equity").textContent = money(eq);
    $("#k-equity-sub").textContent = `Kaufkraft ${money(a.buying_power, "USD", 0)}`;
    $("#k-day").innerHTML = `<span class="${cls(day)}">${signed(day, 2)} $</span>`;
    $("#k-day-sub").innerHTML = `<span class="${cls(day)}">${signed(last ? (day / last) * 100 : 0, 2, " %")}</span>`;
    $("#k-cash").textContent = money(a.cash);
    $("#k-cash-sub").textContent = eq ? `${fmt((num(a.cash) / eq) * 100, 1)} % des Depots` : "";
    loadHistory();
    loadPositions($("#dash-pos tbody"), true).catch(() => {});
  } else {
    ["#k-equity", "#k-day", "#k-cash"].forEach((s) => ($(s).textContent = "–"));
    ["#k-equity-sub", "#k-day-sub", "#k-cash-sub"].forEach((s) => ($(s).textContent = b?.error ? "Broker-Fehler" : "kein Broker"));
    $("#eq-chart").innerHTML = `<p class="empty">${b?.error ? esc(b.error) : "Verfügbar, sobald Alpaca verbunden ist."}</p>`;
    $("#dash-pos tbody").innerHTML = `<tr><td colspan="4" class="empty">Kein Broker verbunden.</td></tr>`;
  }
  const c = state.screen?.candidates || [];
  $("#dash-cands tbody").innerHTML = c.length ? c.slice(0, 6).map((x) => `
    <tr><td class="sym"><a href="#markt" data-pick="${esc(x.ticker)}">${esc(x.ticker)}</a></td><td class="num">${fmt(x.close)}</td>
    <td class="num">${fmt(x.score)}</td><td class="num">${fmt(x.rsi14, 1)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">Noch kein Screen.</td></tr>`;
  $$("#dash-cands [data-pick]").forEach((a) => a.addEventListener("click", () => (selected = a.dataset.pick)));
};

async function loadStatsKpi() {
  const st = await api("/api/stats");
  const t = st.total;
  $("#k-hit").textContent = t.hit_rate == null ? "–" : `${Math.round(t.hit_rate * 100)} %`;
  $("#k-hit-sub").textContent = t.hit + t.miss ? `${t.hit} von ${t.hit + t.miss} Thesen · ${st.open} offen` : `${st.open} offen, noch keine ausgewertet`;
  return st;
}

async function loadHistory() {
  const el = $("#eq-chart");
  try {
    const h = await api(`/api/broker/history?period=${histSeg()}`);
    const series = [{ label: "Depot", color: "var(--series-1)", values: h.equity_idx }];
    if (h.benchmark && h.benchmark_idx.some((v) => v != null)) series.push({ label: `Benchmark ${h.benchmark}`, color: "var(--series-2)", values: h.benchmark_idx });
    legend($("#eq-legend"), series);
    if (h.dates.length < 2) { el.innerHTML = `<p class="empty">Noch zu wenig Depot-Historie – die Kurve wächst mit jedem Handelstag.</p>`; return; }
    lineChart(el, { dates: h.dates, series, baseline: 100, valueFmt: (v) => fmt(v, 1), label: "Depot gegen Benchmark, indexiert" });
  } catch (e) { el.innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

// --- Markt ------------------------------------------------------------------------------
let selected = null, pxData = null;
const daysSeg = segmented($("#days-seg"), () => selected && selectTicker(selected));
const PX = [
  { key: "close", label: "Schlusskurs", color: "var(--series-1)" },
  { key: "sma50", label: "SMA 50", color: "var(--series-2)" },
  { key: "sma200", label: "SMA 200", color: "var(--series-3)" },
];
legend($("#px-legend"), PX);

async function loadScreen() {
  const s = await api("/api/screen");
  state.screen = s;
  $("#s-cands").textContent = s.created ? `${s.candidates.length} von ${s.screened}` : "–";
  $("#screen-sub").textContent = s.created ? `Stand ${fmtDate(s.created)} · ${s.passed} von ${s.screened} Titeln bestehen den Filter` : "Noch kein Screen gelaufen";
  const tb = $("#cand-table tbody");
  tb.innerHTML = s.candidates.length ? s.candidates.map((c, i) => `
    <tr data-ticker="${esc(c.ticker)}" tabindex="0">
      <td class="muted">${i + 1}</td><td class="sym">${esc(c.ticker)}</td><td class="num">${fmt(c.close)}</td>
      <td class="num">${fmt(c.score)}</td><td class="num">${fmt(c.rsi14, 1)}</td>
      <td class="num ${cls(c.mom20_pct)}">${signed(c.mom20_pct, 1, " %")}</td><td class="num">${fmt(c.atr_pct)} %</td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty">Noch kein Screen – „Kurse laden + Screen“ starten.</td></tr>`;
  $$("tr[data-ticker]", tb).forEach((tr) => {
    tr.addEventListener("click", () => selectTicker(tr.dataset.ticker));
    tr.addEventListener("keydown", (e) => e.key === "Enter" && selectTicker(tr.dataset.ticker));
  });
  const rej = Object.entries(s.rejected || {});
  $("#rejects").innerHTML = (rej.length ? `<ul>${rej.map(([t, why]) => `<li><button data-ticker="${esc(t)}">${esc(t)}</button>: ${esc(why.join(", "))}</li>`).join("")}</ul>` : "") +
    (s.missing_data?.length ? `<p>Fehlende Daten: ${esc(s.missing_data.join(", "))}</p>` : "") || `<p class="muted">Nichts aussortiert.</p>`;
  $$("#rejects button").forEach((b) => b.addEventListener("click", () => selectTicker(b.dataset.ticker)));
  return s;
}

loaders.markt = async () => {
  await loadScreen().catch(() => {});
  const first = selected || state.screen?.candidates?.[0]?.ticker;
  if (first) selectTicker(first);
  loadStatus().catch(() => {});
};

async function selectTicker(t) {
  selected = t;
  $$("#cand-table tbody tr").forEach((tr) => tr.classList.toggle("active", tr.dataset.ticker === t));
  $("#chart-title").textContent = t;
  try {
    const d = await api(`/api/prices/${encodeURIComponent(t)}?days=${daysSeg()}`);
    pxData = d;
    lineChart($("#px-chart"), { dates: d.dates, series: PX.map((s) => ({ ...s, values: d[s.key] })), label: `Kursverlauf ${t}` });
    const mt = d.metrics;
    $("#metrics").innerHTML = mt ? [
      ["Stand", fmtDay(mt.date)], ["Kurs", fmt(mt.close)], ["RSI 14", fmt(mt.rsi14, 1)], ["ATR 14", `${fmt(mt.atr14)} (${fmt(mt.atr_pct)} %)`],
      ["Mom. 20T", signed(mt.mom20_pct, 1, " %")], ["Mom. 60T", signed(mt.mom60_pct, 1, " %")], ["Volumen/Ø", fmt(mt.vol_ratio)], ["Max. DD 1J", `${fmt(mt.dd_252_pct, 1)} %`],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("") : "";
    const tradable = /^[A-Z]{1,5}(\.[A-Z])?$/.test(t) && !t.endsWith(".DE");
    $("#chart-actions").hidden = false;
    $("#btn-trade").disabled = !tradable;
    $("#trade-hint").textContent = tradable ? "" : "Über Alpaca nicht handelbar (nur US-Werte)";
  } catch (e) {
    $("#px-chart").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    $("#metrics").innerHTML = "";
    $("#chart-actions").hidden = true;
  }
}

$("#btn-trade").addEventListener("click", () => { ticketTicker = selected; location.hash = "handel"; });
$("#btn-thesis").addEventListener("click", () => {
  const f = $("#thesis-form"), mt = pxData?.metrics;
  f.ticker.value = selected;
  if (mt) { f.entry.value = mt.close.toFixed(2); f.target.value = (mt.close + 3 * mt.atr14).toFixed(2); f.stop.value = (mt.close - 1.5 * mt.atr14).toFixed(2); }
  location.hash = "journal";
  setTimeout(() => f.rationale.focus(), 50);
});

// --- Handel -----------------------------------------------------------------------------
let ticketTicker = null, checkedOrder = null;
const ordersSeg = segmented($("#orders-seg"), () => loadOrders());
const tf = $("#ticket");

loaders.handel = async () => {
  const b = await loadBroker().catch(() => null);
  const ok = b?.configured && !b.error;
  $("#ticket-src").textContent = ok ? "Alpaca Paper" : "kein Broker";
  $("#ticket-src").className = `pill ${ok ? "good" : ""}`;
  if (!ok) {
    const msg = b?.error ? esc(b.error) : `Kein Broker verbunden – <a href="#einstellungen">Alpaca verbinden</a>.`;
    $("#pos-table tbody").innerHTML = `<tr><td colspan="7" class="empty">${msg}</td></tr>`;
    $("#orders-table tbody").innerHTML = `<tr><td colspan="7" class="empty">${msg}</td></tr>`;
    return;
  }
  if (ticketTicker) { tf.ticker.value = ticketTicker; ticketTicker = null; prefill(); }
  loadPositions($("#pos-table tbody")).catch((e) => toast(e.message));
  loadOrders().catch((e) => toast(e.message));
};
$("#btn-refresh-trade").addEventListener("click", () => loaders.handel());

async function loadPositions(tbody, short = false) {
  const ps = await api("/api/broker/positions");
  const total = ps.reduce((s, p) => s + num(p.market_value), 0), pl = ps.reduce((s, p) => s + num(p.unrealized_pl), 0);
  if (!short) $("#pos-total").innerHTML = ps.length ? `${money(total)} · <span class="${cls(pl)}">${signed(pl)} $</span>` : "";
  const cols = short ? 4 : 7;
  tbody.innerHTML = ps.length ? ps.map((p) => short ? `
    <tr><td class="sym">${esc(p.symbol)}</td><td class="num">${fmt(p.qty, 0)}</td><td class="num">${money(p.market_value)}</td>
    <td class="num ${cls(p.unrealized_pl)}">${signed(num(p.unrealized_plpc) * 100, 1, " %")}</td></tr>` : `
    <tr><td class="sym">${esc(p.symbol)}</td><td class="num">${fmt(p.qty, 0)}</td><td class="num">${fmt(p.avg_entry_price)}</td>
    <td class="num">${fmt(p.current_price)}<span class="sub ${cls(p.change_today)}">${signed(num(p.change_today) * 100, 2, " %")}</span></td>
    <td class="num">${money(p.market_value)}</td>
    <td class="num ${cls(p.unrealized_pl)}">${signed(p.unrealized_pl)} $<span class="sub">${signed(num(p.unrealized_plpc) * 100, 1, " %")}</span></td>
    <td><button class="btn sm danger" data-close="${esc(p.symbol)}">Schließen</button></td></tr>`).join("")
    : `<tr><td colspan="${cols}" class="empty">Keine offenen Positionen.</td></tr>`;
  $$("[data-close]", tbody).forEach((b) => b.addEventListener("click", () => closePosition(b.dataset.close)));
}

async function closePosition(sym) {
  const ok = await confirmDialog(`${sym} schließen?`, `<p>Die gesamte Position wird zum Markt verkauft. Offene Stop- und Ziel-Orders für ${esc(sym)} werden vorher storniert.</p>`, "Position schließen");
  if (!ok) return;
  try { await api(`/api/trade/close/${encodeURIComponent(sym)}`, { method: "POST" }); toast(`${sym}: Verkauf übermittelt`); }
  catch (e) { toast(e.message); }
  loaders.handel();
}

async function loadOrders() {
  const st = ordersSeg();
  const os = await api(`/api/broker/orders?status=${st}`);
  const tb = $("#orders-table tbody");
  const LABEL = { new: "neu", accepted: "angenommen", pending_new: "wartet", partially_filled: "teilweise", filled: "ausgeführt", canceled: "storniert", expired: "abgelaufen", rejected: "abgelehnt", held: "wartet (Bracket)", replaced: "ersetzt" };
  const row = (o, leg = false) => `
    <tr${leg ? ' class="muted"' : ""}>
      <td>${leg ? "" : fmtDate(o.created_at)}</td>
      <td class="sym">${leg ? "↳" : esc(o.symbol)}</td>
      <td>${esc(o.side === "buy" ? "Kauf" : "Verkauf")} ${esc(o.type)}${o.order_class === "bracket" && !leg ? " · Bracket" : ""}</td>
      <td class="num">${fmt(o.filled_qty, 0)}/${fmt(o.qty, 0)}</td>
      <td class="num">${fmt(o.limit_price ?? o.stop_price)}</td>
      <td>${esc(LABEL[o.status] || o.status)}${o.filled_avg_price ? `<span class="sub">Ø ${fmt(o.filled_avg_price)}</span>` : ""}</td>
      <td>${!leg && st === "open" ? `<button class="btn sm" data-cancel="${esc(o.id)}">Stornieren</button>` : ""}</td>
    </tr>`;
  tb.innerHTML = os.length ? os.map((o) => row(o) + (o.legs || []).map((l) => row(l, true)).join("")).join("")
    : `<tr><td colspan="7" class="empty">${st === "open" ? "Keine offenen Orders." : "Noch keine erledigten Orders."}</td></tr>`;
  $$("[data-cancel]", tb).forEach((b) => b.addEventListener("click", async () => {
    if (!(await confirmDialog("Order stornieren?", "<p>Die Order und ihre Stop-/Ziel-Teile werden storniert.</p>", "Stornieren"))) return;
    try { await api(`/api/trade/orders/${b.dataset.cancel}`, { method: "DELETE" }); toast("Order storniert"); } catch (e) { toast(e.message); }
    loadOrders();
  }));
}

function ticketBody() {
  return {
    ticker: tf.ticker.value.trim().toUpperCase(), qty: Number(tf.qty.value), price: Number(tf.price.value),
    stop: Number(tf.stop.value), target: Number(tf.target.value), horizon_days: Number(tf.horizon_days.value),
    rationale: tf.rationale.value.trim(), model: tf.model.value.trim() || "manuell",
  };
}

function renderTicketSum() {
  const o = ticketBody(), risk = (o.price - o.stop) * o.qty, reward = (o.target - o.price) * o.qty;
  const eq = num(state.broker?.account?.equity);
  if (!o.qty || !o.price || !o.stop || !o.target) { $("#ticket-sum").innerHTML = ""; return; }
  const items = [
    ["Volumen", money(o.qty * o.price)], ["Risiko bis Stop", `${money(risk)}${eq ? ` · ${fmt((risk / eq) * 100, 2)} %` : ""}`],
    ["Chance bis Ziel", money(reward)], ["Chance/Risiko", risk > 0 ? `${fmt(reward / risk, 1)} : 1` : "–"],
  ];
  $("#ticket-sum").innerHTML = items.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function invalidateTicket() {
  checkedOrder = null;
  $("#btn-send").disabled = true;
  $("#ticket-result").innerHTML = "";
  renderTicketSum();
}
$$("input, textarea", tf).forEach((i) => i.addEventListener("input", invalidateTicket));

async function prefill() {
  const t = tf.ticker.value.trim().toUpperCase();
  if (!t) return;
  try {
    const k = await api(`/api/trade/ticket/${encodeURIComponent(t)}`);
    tf.ticker.value = k.ticker;
    tf.price.value = k.price.toFixed(2);
    tf.stop.value = k.stop.toFixed(2);
    tf.target.value = k.target.toFixed(2);
    tf.qty.value = k.qty;
    invalidateTicket();
    $("#ticket-result").innerHTML = `<p class="muted small">Vorschlag: Kurs ${fmt(k.price)} (${k.price_source === "alpaca" ? "Alpaca live" : "letzter Schluss"}),
      Stop 1,5 × ATR, Ziel 3 × ATR, Stückzahl nach Risiko-Limits.${k.tradable ? "" : ' <span class="neg">Nicht über Alpaca handelbar.</span>'}</p>`;
  } catch (e) { toast(e.message); }
}
$("#btn-prefill").addEventListener("click", prefill);

function showViolations(e) {
  const v = e.data?.violations;
  $("#ticket-result").innerHTML = `<div class="result bad"><span class="status bad">${esc(v ? "Abgelehnt" : e.message)}</span>
    ${v ? `<ul>${v.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${e.data?.suggested_qty != null ? `<p class="small">Maximal zulässige Stückzahl: <b>${e.data.suggested_qty}</b></p>` : ""}</div>`;
}

tf.addEventListener("submit", async (e) => {
  e.preventDefault();
  const o = ticketBody();
  try {
    const r = await api("/api/trade/check", { method: "POST", body: o });
    checkedOrder = o;
    $("#btn-send").disabled = false;
    $("#ticket-result").innerHTML = `<div class="result ok"><span class="status ok">Prüfung bestanden</span>
      <p class="small">Letzter Kurs ${fmt(r.last_price)} · max. ${r.risk.suggested_qty} Stück nach Limits. Order kann gesendet werden.</p></div>`;
  } catch (err) { checkedOrder = null; $("#btn-send").disabled = true; showViolations(err); }
});

$("#btn-send").addEventListener("click", async () => {
  if (!checkedOrder) return;
  const o = checkedOrder;
  const ok = await confirmDialog("Order senden?", `
    <dl class="facts">
      <div><dt>Titel</dt><dd>${esc(o.ticker)}</dd></div>
      <div><dt>Kauf (Limit, GTC)</dt><dd>${o.qty} × ${fmt(o.price)}</dd></div>
      <div><dt>Stop-Loss</dt><dd>${fmt(o.stop)}</dd></div>
      <div><dt>Ziel</dt><dd>${fmt(o.target)}</dd></div>
      <div><dt>Volumen</dt><dd>${money(o.qty * o.price)}</dd></div>
    </dl>
    <p class="muted small">Alpaca Paper-Trading. Die Order wird serverseitig erneut geprüft; dazu wird eine These im Journal angelegt.</p>`, "Order senden");
  if (!ok) return;
  try {
    const r = await api("/api/trade/order", { method: "POST", body: o });
    toast(`Order ${o.ticker} übermittelt (${r.order.status})`);
    $("#ticket-result").innerHTML = `<div class="result ok"><span class="status ok">Order übermittelt</span>
      <p class="small">Status ${esc(r.order.status)} · These ${esc(r.thesis.id)} angelegt, Frist ${esc(r.thesis.deadline)}.</p></div>`;
    tf.rationale.value = "";
    checkedOrder = null;
    $("#btn-send").disabled = true;
    loadOrders(); loadPositions($("#pos-table tbody"));
  } catch (err) { showViolations(err); }
});

// --- Journal ----------------------------------------------------------------------------
const STATUS_LABEL = { open: "offen", hit: "Treffer", miss: "Fehlschlag", cancelled: "storniert" };

loaders.journal = async () => {
  const filter = $("#thesis-filter").value;
  const [list, st, acts] = await Promise.all([
    api(`/api/theses${filter ? `?status=${filter}` : ""}`), api("/api/stats"), api("/api/actions?limit=60"),
  ]);
  const kpi = (label, r, sub) => `<div class="kpi"><div class="kpi-label">${esc(label)}</div>
    <div class="kpi-value">${r.hit_rate == null ? "–" : Math.round(r.hit_rate * 100) + " %"}</div>
    <div class="kpi-sub">${r.hit + r.miss ? `${r.hit} Treffer · ${r.miss} Fehlschläge` : sub}</div></div>`;
  $("#model-stats").innerHTML = kpi("Trefferquote gesamt", st.total, `${st.open} offen`) +
    Object.entries(st.by_model).slice(0, 5).map(([m, r]) => kpi(`Quelle: ${m}`, r, "")).join("");

  const today = new Date().toISOString().slice(0, 10);
  $("#thesis-list").innerHTML = list.length ? list.map((t) => {
    const due = t.status === "open" && t.deadline <= today;
    const pill = t.status === "hit" ? "good" : t.status === "miss" ? "bad" : due ? "warn" : "";
    return `
      <div class="thesis" data-id="${esc(t.id)}">
        <div class="thesis-head"><strong>${esc(t.ticker)} ${t.direction === "up" ? "↑" : "↓"} ${fmt(t.target)}</strong>
          <span class="pill ${pill}">${due ? "fällig" : STATUS_LABEL[t.status]}</span></div>
        <div class="small muted">Einstieg ${fmt(t.entry)}${t.stop ? ` · Stop ${fmt(t.stop)}` : ""} · Frist ${fmtDay(t.deadline)} · ${esc(t.model || "–")}${t.order_id ? " · mit Order" : ""}</div>
        <p>${esc(t.rationale)}</p>
        ${t.status === "open" ? `<div class="actions">
            <input type="number" step="any" placeholder="Kurs" aria-label="Kurs bei Auswertung">
            <button class="btn sm" data-status="hit">Treffer</button><button class="btn sm" data-status="miss">Fehlschlag</button>
            <button class="btn sm" data-status="cancelled">Stornieren</button></div>`
          : `<div class="small muted">${t.resolved_price != null ? `Ausgewertet bei ${fmt(t.resolved_price)} · ` : ""}${fmtDate(t.resolved_at)}${t.note ? ` · ${esc(t.note)}` : ""}</div>`}
      </div>`;
  }).join("") : `<p class="empty">Keine Thesen in dieser Ansicht.</p>`;
  $$("#thesis-list [data-status]").forEach((b) => b.addEventListener("click", async () => {
    const card = b.closest(".thesis"), price = $("input", card).value;
    try {
      await api(`/api/theses/${encodeURIComponent(card.dataset.id)}/resolve`, { method: "POST", body: { status: b.dataset.status, price: price ? Number(price) : null } });
      loaders.journal(); loadStatus();
    } catch (e) { toast(e.message); }
  }));

  const label = (a) => a.action ? { order: "Order", cancel: "Storno", close: "Position geschlossen", broker_keys_saved: "Broker verbunden", broker_keys_deleted: "Broker getrennt" }[a.action] || a.action
    : a.hook ? `Hook ${a.hook}` : "Eintrag";
  $("#action-table tbody").innerHTML = acts.length ? acts.map((a) => {
    const { ts, ...rest } = a;
    return `<tr><td>${fmtDate(ts)}</td><td>${esc(label(a))}</td><td><code>${esc(JSON.stringify(rest)).slice(0, 400)}</code></td></tr>`;
  }).join("") : `<tr><td colspan="3" class="empty">Noch keine Aktionen protokolliert.</td></tr>`;
};
$("#thesis-filter").addEventListener("change", () => loaders.journal());

$("#btn-evaluate").addEventListener("click", async () => {
  try {
    const r = await api("/api/evaluate", { method: "POST" });
    toast(r.resolved.length ? `${r.resolved.length} Thesen ausgewertet` : "Keine These fällig oder entschieden");
    loaders.journal(); loadStatus();
  } catch (e) { toast(e.message); }
});

$("#thesis-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#thesis-msg");
  const body = {
    ticker: f.ticker.value.trim(), direction: f.direction.value, entry: Number(f.entry.value), target: Number(f.target.value),
    stop: f.stop.value ? Number(f.stop.value) : null, horizon_days: Number(f.horizon_days.value), rationale: f.rationale.value, model: f.model.value,
  };
  try {
    const t = await api("/api/theses", { method: "POST", body });
    msg.className = "small status ok"; msg.textContent = `Gespeichert, Frist ${fmtDay(t.deadline)}`;
    f.rationale.value = "";
    $("#thesis-filter").value = "open";
    loaders.journal(); loadStatus();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

// --- Einstellungen ----------------------------------------------------------------------
const sf = $("#settings-form");
const SCREEN_KEYS = ["top_n", "min_history_days", "min_avg_volume", "rsi_min", "rsi_max"];
const RISK_KEYS = ["max_position_pct", "max_risk_per_trade_pct", "max_daily_loss_pct", "max_open_positions"];

async function loadConfig() {
  const c = await api("/api/config");
  state.config = c;
  $("#universe-list").innerHTML = c.universe.map((t) => `<option value="${esc(t)}">`).join("");
  return c;
}

loaders.einstellungen = async () => {
  loadBroker().catch(() => {});
  const c = await loadConfig();
  sf.universe.value = c.universe.join("\n");
  sf.benchmark.value = c.benchmark || "";
  SCREEN_KEYS.forEach((k) => (sf[k].value = c.screen[k]));
  RISK_KEYS.forEach((k) => (sf[k].value = c.risk[k]));
  sf.enforce_market_hours.checked = c.risk.enforce_market_hours;
  const sc = c.schedule || { enabled: false, times: [], tz: "Europe/Berlin" };
  sf.schedule_enabled.checked = sc.enabled;
  sf.schedule_times.value = sc.times.join(", ");
  $("#sched-tz").textContent = sc.tz;
};

sf.addEventListener("submit", async (e) => {
  e.preventDefault();
  const c = state.config, msg = $("#settings-msg");
  const body = {
    ...c,
    universe: sf.universe.value.split(/[\s,;]+/).map((t) => t.trim().toUpperCase()).filter(Boolean),
    benchmark: sf.benchmark.value.trim().toUpperCase(),
    screen: Object.fromEntries(SCREEN_KEYS.map((k) => [k, Number(sf[k].value)])),
    risk: { ...c.risk, ...Object.fromEntries(RISK_KEYS.map((k) => [k, Number(sf[k].value)])), enforce_market_hours: sf.enforce_market_hours.checked },
    schedule: { ...c.schedule, enabled: sf.schedule_enabled.checked, times: sf.schedule_times.value.split(/[\s,;]+/).filter(Boolean) },
  };
  try {
    await api("/api/config", { method: "PUT", body });
    msg.className = "small status ok"; msg.textContent = "Gespeichert";
    loaders.einstellungen(); loadStatus();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

$("#broker-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#broker-msg");
  msg.className = "small muted"; msg.textContent = "Prüfe Schlüssel bei Alpaca …";
  try {
    const r = await api("/api/broker/keys", { method: "PUT", body: { key_id: f.key_id.value.trim(), secret: f.secret.value.trim() } });
    f.reset();
    msg.className = "small status ok"; msg.textContent = `Verbunden mit Konto ${r.account_number}`;
    loadBroker();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

$("#btn-disconnect").addEventListener("click", async () => {
  if (!(await confirmDialog("Broker trennen?", "<p>Die gespeicherten Alpaca-Schlüssel werden vom Umbrel gelöscht. Offene Orders bei Alpaca bleiben bestehen.</p>", "Trennen"))) return;
  await api("/api/broker/keys", { method: "DELETE" });
  $("#broker-msg").textContent = "";
  loadBroker();
});

// --- Start ------------------------------------------------------------------------------
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const page = location.hash.slice(1) || "dashboard";
    if (page === "markt" && selected) selectTicker(selected);
    if (page === "dashboard" && state.broker?.configured) loadHistory();
  }, 200);
});

loadConfig().catch(() => {});
loadStatus().catch((e) => toast(e.message));
route();
setInterval(() => { if (!polling) loadStatus().catch(() => {}); }, 30000);
