"use strict";

// --- Helfer -----------------------------------------------------------------------------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (v == null || v === "" ? null : Number(v));
const fmt = (v, d = 2) => (num(v) == null || Number.isNaN(num(v)) ? "–" : num(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v, cur = "EUR", d = 2) => (num(v) == null ? "–" : num(v).toLocaleString("de-DE", { style: "currency", currency: cur, minimumFractionDigits: d, maximumFractionDigits: d }));
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

const isPhone = () => window.matchMedia("(max-width: 760px)").matches;

// --- Navigation -------------------------------------------------------------------------
const PAGES = ["dashboard", "markt", "handel", "journal", "hilfe", "einstellungen"];
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
  const adv = s.advisor || {}, advOn = adv.enabled && adv.token_configured;
  $("#statusbar").innerHTML = `
    <span class="pill ${s.paper_trading ? "good" : "bad"}">${s.paper_trading ? "Paper-Trading · kein echtes Geld" : "LIVE"}</span>
    <a href="#einstellungen" class="pill ${advOn && adv.auto_trade ? "warn" : ""}">${!advOn ? "Claude aus" : adv.auto_trade ? "Claude kauft automatisch" : "Claude schlägt nur vor"}</a>
    <span class="pill">${s.next_run ? `Nächster Lauf ${fmtDate(s.next_run)}` : "Zeitplan aus"}</span>`;

  const steps = { fetch: "lade Kurse", screen: "rechne Screen", evaluate: "werte Thesen aus", sync: "gleiche Depot ab", advise: "Claude bewertet" };
  let line = "";
  if (j.running) line = `${j.trigger === "zeitplan" ? "Zeitplan: " : ""}${steps[j.step] || "startet"} …`;
  else if (j.error) line = `Fehler: ${j.error}`;
  else if (j.finished) {
    const f = j.result?.fetch, failed = f ? Object.keys(f.failed || {}) : [];
    line = `Fertig ${fmtDate(j.finished)}` + (f ? ` · ${f.fetched.length} geladen, ${f.cached.length} aus Cache` : "") +
      (j.result?.evaluate ? ` · ${j.result.evaluate.resolved} Thesen ausgewertet` : "") +
      (j.result?.advise ? ` · Claude: ${j.result.advise.proposals} Vorschläge` : "") +
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
    // „Alles aktualisieren“ lädt immer frisch – sonst bleibt ein zu früh geladener Kurs den ganzen Tag stehen
    await api("/api/run", { method: "POST", body: { steps, force: kind === "all" || $("#chk-force").checked } });
  } catch (e) { toast(e.message); }
  loadStatus();
}
$$("[data-run]").forEach((b) => b.addEventListener("click", () => runJob(b.dataset.run)));

async function loadBroker() {
  const b = await api("/api/broker");
  state.broker = b;
  const a = b.account, al = b.allocation;
  $("#broker-line").textContent = b.error ? "Depot: Fehler" : "Paper-Depot · EUR";
  $("#clock-line").textContent = "Kurse: Tagesschluss (Yahoo)";
  $("#broker-info").innerHTML = b.error ? `<span class="neg">${esc(b.error)}</span>` :
    `Gestartet ${fmtDate(a.created)} mit ${money(a.start_cash, "EUR", 0)} · Höchststand ${money(a.high_water)}`;
  const notice = $("#core-notice");
  notice.hidden = !al?.core_below_target;
  if (al?.core_below_target) {
    $("#core-notice-text").innerHTML = `<strong>Core unter Ziel:</strong> ${fmt(al.core_pct, 1)} % statt ${fmt(al.target_core_pct, 0)} %
      (${esc(Object.keys(b.core).join(", "))}). Der Core ist die Messlatte – erst auffüllen, dann Satellit-Trades.`;
  }
  return b;
}

$("#btn-core").addEventListener("click", async () => {
  try {
    const plan = (await api("/api/trade/core")).filter((r) => !r.skip);
    if (!plan.length) { toast("Nichts aufzufüllen"); return; }
    const ok = await confirmDialog("Core auffüllen?", `<dl class="facts">${plan.map((r) => `
      <div><dt>${esc(r.ticker)}</dt><dd>${fmt(r.qty, 4)} × ${fmt(r.price)} = ${money(r.value_eur)}</dd></div>`).join("")}</dl>
      <p class="muted small">Kauf zum letzten Kurs plus Slippage, Gebühr wie Bitpanda. Ohne Stop und ohne These – der Core ist Bestand, keine Wette.</p>`, "Kaufen");
    if (!ok) return;
    const r = await api("/api/trade/core", { method: "POST" });
    toast(`${r.orders.length} Core-Kauf ausgeführt`);
  } catch (e) { toast(e.data?.violations ? e.data.violations.join("; ") : e.message); }
  loaders.dashboard();
});

// --- Dashboard --------------------------------------------------------------------------
const histSeg = segmented($("#hist-seg"), () => loadHistory());

loaders.dashboard = async () => {
  const [b] = await Promise.all([loadBroker().catch(() => null), loadStatus().catch(() => null), loadScreen().catch(() => null),
    loadStatsKpi(), loadAdvisor().catch(() => null), loadFeed().catch(() => null)]);
  const a = b && !b.error ? b.account : null;
  if (a) {
    const eq = num(a.equity), last = num(a.last_equity), day = eq - last, total = eq - num(a.start_cash);
    const al = b.allocation;
    $("#k-equity").textContent = money(eq);
    $("#k-equity-sub").innerHTML = `seit Start <span class="${cls(total)}">${signed((total / num(a.start_cash)) * 100, 2, " %")}</span>`;
    $("#k-day").innerHTML = `<span class="${cls(day)}">${signed(day, 2)} €</span>`;
    $("#k-day-sub").innerHTML = `<span class="${cls(day)}">${signed(last ? (day / last) * 100 : 0, 2, " %")}</span>`;
    $("#k-cash").textContent = money(a.cash);
    $("#k-cash-sub").textContent = eq ? `${fmt((num(a.cash) / eq) * 100, 1)} % des Depots` : "";
    $("#s-alloc").textContent = `${fmt(al.core_pct, 0)} / ${fmt(al.satellite_pct, 0)} / ${fmt(al.cash_pct, 0)} %`;
    loadHistory();
    loadPositions($("#dash-pos tbody"), true).catch(() => {});
  } else {
    ["#k-equity", "#k-day", "#k-cash"].forEach((s) => ($(s).textContent = "–"));
    ["#k-equity-sub", "#k-day-sub", "#k-cash-sub"].forEach((s) => ($(s).textContent = "Depot-Fehler"));
    $("#eq-chart").innerHTML = `<p class="empty">${esc(b?.error || "Depot nicht erreichbar")}</p>`;
    $("#dash-pos tbody").innerHTML = `<tr><td colspan="4" class="empty">${esc(b?.error || "Depot nicht erreichbar")}</td></tr>`;
  }
  const c = state.screen?.candidates || [];
  $("#dash-cands tbody").innerHTML = c.length ? c.slice(0, 6).map((x) => `
    <tr><td class="sym"><a href="#markt" data-pick="${esc(x.ticker)}">${esc(x.ticker)}</a></td><td class="num">${fmt(x.close)}</td>
    <td class="num">${fmt(x.score)}</td><td class="num">${fmt(x.rsi14, 1)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">Noch kein Screen.</td></tr>`;
  $$("#dash-cands [data-pick]").forEach((a) => a.addEventListener("click", () => (selected = a.dataset.pick)));
};

// Was nach Gebühren übrig bleibt – ab 50 % nötiger Trefferquote als Warnung
function oddsLine(o) {
  if (!o) return "";
  const hard = o.breakeven_pct >= 50;
  return `<p class="odds small ${hard ? "neg" : "muted"}">Nach Gebühren: +${money(o.net_win_eur)} am Ziel, −${money(o.net_loss_eur)} am Stop.
    Lohnt sich erst ab <b>${fmt(o.breakeven_pct, 0)} %</b> Trefferquote${hard ? " – die Gebühr frisst das Chance/Risiko auf" : ""}.</p>`;
}

function autoCell(auto, i) {
  if (auto?.status === "placed") return `<span class="pill good">automatisch gebucht</span>`;
  const why = auto ? `<div class="small muted">${esc((auto.reasons || []).join("; "))}</div>` : "";
  const tag = auto ? `<span class="pill bad">${auto.status === "rejected" ? "abgelehnt" : "Fehler"}</span>` : "";
  return `${tag}${why}<button class="btn sm primary" data-prop="${i}">Ticket öffnen</button>`;
}

async function loadAdvisor() {
  const a = await api("/api/advisor"), r = a.report, card = $("#advisor-card");
  card.hidden = !r && !a.enabled;
  if (card.hidden) return a;
  if (!r) {
    $("#adv-meta").textContent = "";
    $("#adv-summary").textContent = a.token_configured ? "Noch keine Bewertung – sie läuft nach dem ersten Zeitplan-Lauf des Tages."
      : "Claude ist aktiviert, aber es ist noch kein Token hinterlegt (Einstellungen).";
    $("#adv-wrap").hidden = true; $("#adv-more").hidden = true;
    return a;
  }
  $("#adv-meta").textContent = `${r.model} · ${fmtDate(r.created)} · ${r.candidates} Kandidaten, ${r.free_slots} freie Plätze`;
  $("#adv-summary").textContent = r.summary || "";
  $("#adv-wrap").hidden = !r.proposals.length;
  $("#adv-wrap").innerHTML = r.proposals.map((p, i) => `
    <div class="prop">
      <div class="prop-head"><b class="sym">${esc(p.ticker)}</b><span class="muted small">Kauf-Idee · ${p.horizon_days} Tage</span></div>
      <dl class="prop-nums">
        <div><dt>Kurs</dt><dd>${money(p.price, p.currency)}</dd></div>
        <div><dt>Verlustgrenze (Stop)</dt><dd>${money(p.stop, p.currency)}</dd></div>
        <div><dt>Gewinnziel</dt><dd>${money(p.target, p.currency)}</dd></div>
      </dl>
      ${oddsLine(p.odds)}
      <p class="prop-why">${esc(p.rationale)}</p>
      <div class="prop-foot">${autoCell(p.auto, i)}</div>
    </div>`).join("");
  $$("#adv-wrap [data-prop]").forEach((btn) => btn.addEventListener("click", () => {
    ticketProposal = { ...r.proposals[btn.dataset.prop], model: r.model, created: r.created };
    location.hash = "handel";
  }));
  const more = [...r.rejected.map((x) => `${x.ticker}: verworfen – ${x.reason}`), ...r.skipped.map((x) => `${x.ticker}: ${x.reason}`)];
  $("#adv-more").hidden = !more.length;
  $("#adv-rejects").innerHTML = `<ul>${more.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`;
  return a;
}

const TONE = { good: "pos", bad: "neg", info: "" };
const eventRow = (e) => `<li><span class="feed-time muted small">${e.ts.length > 10 ? fmtDate(e.ts) : fmtDay(e.ts)}</span><span class="${TONE[e.tone] || ""}">${esc(e.text)}</span></li>`;

async function loadFeed() {
  const ev = await api("/api/events?limit=5&important=true");
  $("#feed").innerHTML = ev.length ? ev.map(eventRow).join("") : `<li class="muted">Noch nichts passiert. Der nächste Lauf lädt Kurse und bewertet die Kandidaten.</li>`;
}

async function loadStatsKpi() {
  const st = await api("/api/stats");
  const t = st.total;
  $("#k-hit").textContent = t.hit_rate == null ? "–" : `${Math.round(t.hit_rate * 100)} %`;
  $("#k-hit-sub").textContent = t.hit + t.miss ? `${t.hit} von ${t.hit + t.miss} Thesen · ${st.open} offen` : `${st.open} offen, noch keine ausgewertet`;
  const tr = st.trades, el = $("#k-trades");
  el.textContent = tr && tr.count ? `${tr.net_eur >= 0 ? "+" : "−"}${money(Math.abs(tr.net_eur))}` : "–";
  el.className = `kpi-value ${tr && tr.count ? (tr.net_eur >= 0 ? "pos" : "neg") : ""}`;
  $("#k-trades-sub").textContent = !tr ? "" : tr.count
    ? `${tr.wins} von ${tr.count} im Plus · ${money(tr.fees_eur)} Gebühren gesamt`
    : `noch kein Trade beendet · ${money(tr.fees_eur)} Gebühren bisher`;
  return st;
}

async function loadHistory() {
  const el = $("#eq-chart");
  try {
    const h = await api(`/api/broker/history?period=${histSeg()}`);
    const series = [{ label: "Depot", color: "var(--series-1)", values: h.equity_idx }];
    if (h.benchmark && h.benchmark_idx.some((v) => v != null)) series.push({ label: `Benchmark ${h.benchmark}`, color: "var(--series-2)", values: h.benchmark_idx });
    legend($("#eq-legend"), series);
    el.classList.toggle("is-empty", h.dates.length < 2);
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
    const isCore = Object.keys(state.broker?.core || {}).includes(t);
    const listed = (state.config?.universe || []).includes(t);
    $("#chart-actions").hidden = false;
    $("#btn-trade").disabled = isCore || !listed;
    $("#trade-hint").textContent = isCore ? "Core-Titel – Kauf über „Core auffüllen“ im Dashboard"
      : listed ? `Kurse in ${d.currency}` : "Nicht im Universum";
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
  thesisFormWanted = true;
  location.hash = "journal";
  setTimeout(() => f.rationale.focus(), 50);
});

// --- Handel -----------------------------------------------------------------------------
let ticketTicker = null, ticketProposal = null, checkedOrder = null;
const ordersSeg = segmented($("#orders-seg"), () => loadOrders());
const tf = $("#ticket");

loaders.handel = async () => {
  const b = await loadBroker().catch(() => null);
  const ok = b && !b.error;
  $("#ticket-src").textContent = ok ? "Paper-Depot" : "Depot-Fehler";
  $("#ticket-src").className = `pill ${ok ? "good" : "bad"}`;
  if (!ok) {
    const msg = esc(b?.error || "Depot nicht erreichbar");
    $("#pos-table tbody").innerHTML = `<tr><td colspan="8" class="empty">${msg}</td></tr>`;
    $("#orders-table tbody").innerHTML = `<tr><td colspan="7" class="empty">${msg}</td></tr>`;
    return;
  }
  if (ticketProposal || ticketTicker || !isPhone()) $("#ticket-details").open = true;
  else if (!tf.ticker.value) $("#ticket-details").open = false;
  if (ticketProposal) { fillProposal(ticketProposal); ticketProposal = null; }
  else if (ticketTicker) { tf.ticker.value = ticketTicker; tf.thesis_id.value = ""; ticketTicker = null; prefill(); }
  loadPositions($("#pos-table tbody")).catch((e) => toast(e.message));
  loadOrders().catch((e) => toast(e.message));
};
$("#btn-refresh-trade").addEventListener("click", () => loaders.handel());

// Abstand von Stop und Ziel zum aktuellen Kurs – das, was man bei einer Swing-Position wissen will
function stopTarget(p) {
  if (p.stop == null && p.target == null) return `<span class="muted">–</span>`;
  const dist = (v) => (p.current_price ? signed((v / num(p.current_price) - 1) * 100, 1, " %") : "");
  return `${p.stop != null ? `<span class="neg">${fmt(p.stop)}</span> <span class="muted small">${dist(p.stop)}</span>` : "–"}
    <span class="sub">${p.target != null ? `<span class="pos">${fmt(p.target)}</span> ${dist(p.target)}` : "kein Ziel"}</span>`;
}

async function loadPositions(tbody, short = false) {
  const ps = await api("/api/broker/positions");
  const total = ps.reduce((s, p) => s + num(p.market_value), 0), pl = ps.reduce((s, p) => s + num(p.unrealized_pl), 0);
  if (!short) $("#pos-total").innerHTML = ps.length ? `${money(total)} · <span class="${cls(pl)}">${signed(pl)} €</span>` : "";
  const cols = short ? 4 : 8;
  const tag = (p) => `<span class="sub">${p.sleeve === "core" ? "Core" : "Satellit"}</span>`;
  tbody.innerHTML = ps.length ? ps.map((p) => short ? `
    <tr><td class="sym">${esc(p.symbol)}${tag(p)}</td><td class="num">${stopTarget(p)}</td><td class="num">${money(p.market_value)}</td>
    <td class="num ${cls(p.unrealized_pl)}">${signed(num(p.unrealized_plpc) * 100, 1, " %")}</td></tr>` : `
    <tr><td class="sym full">${esc(p.symbol)}${tag(p)}</td><td class="num" data-l="Stück">${fmt(p.qty, 4)}</td><td class="num" data-l="Einstand">${money(p.avg_entry_price, p.currency)}</td>
    <td class="num" data-l="Kurs">${money(p.current_price, p.currency)}<span class="sub ${cls(p.change_today)}">${signed(num(p.change_today) * 100, 2, " %")}</span></td>
    <td class="num" data-l="Stop / Ziel">${stopTarget(p)}</td>
    <td class="num" data-l="Wert">${money(p.market_value)}</td>
    <td class="num ${cls(p.unrealized_pl)}" data-l="Gewinn/Verlust">${signed(p.unrealized_pl)} €<span class="sub">${signed(num(p.unrealized_plpc) * 100, 1, " %")}</span></td>
    <td class="full row-actions">${p.stop != null ? `<button class="btn sm" data-stop="${esc(p.symbol)}">Stop nachziehen</button>` : ""}
      <button class="btn sm danger" data-close="${esc(p.symbol)}">Schließen</button></td></tr>`).join("")
    : `<tr><td colspan="${cols}" class="empty">Keine offenen Positionen.</td></tr>`;
  $$("[data-close]", tbody).forEach((b) => b.addEventListener("click", () => closePosition(b.dataset.close)));
  $$("[data-stop]", tbody).forEach((b) => b.addEventListener("click", () => raiseStop(ps.find((p) => p.symbol === b.dataset.stop))));
}

async function raiseStop(p) {
  const ok = await confirmDialog(`Stop für ${p.symbol} nachziehen`, `
    <p>Aktueller Stop <b>${money(p.stop, p.currency)}</b>, letzter Kurs ${money(p.current_price, p.currency)}.
      Der neue Stop muss dazwischen liegen – ein Stop wird nur angehoben, nie gesenkt.</p>
    <label>Neuer Stop (${esc(p.currency)})<input id="new-stop" type="number" step="any" min="${p.stop}" max="${p.current_price}" inputmode="decimal"></label>`, "Stop anheben");
  if (!ok) return;
  const v = Number($("#new-stop").value);
  if (!v) { toast("Kein Stop eingegeben"); return; }
  try {
    const r = await api(`/api/trade/stop/${encodeURIComponent(p.symbol)}`, { method: "POST", body: { stop: v } });
    toast(`Stop ${p.symbol} angehoben auf ${fmt(r.stop)}`);
  } catch (e) { toast(e.message); }
  loaders.handel();
}

async function closePosition(sym) {
  const ok = await confirmDialog(`${sym} schließen?`, `<p>Die gesamte Position wird zum letzten Kurs (minus Slippage und Gebühr) verkauft. Offene Stop- und Ziel-Orders für ${esc(sym)} werden storniert.</p>`, "Position schließen");
  if (!ok) return;
  try {
    const r = await api(`/api/trade/close/${encodeURIComponent(sym)}`, { method: "POST" });
    toast(`${sym} verkauft · Ergebnis ${signed(r.realized_pl_eur)} € · zugehörige These beendet`);
  }
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
      <td ${leg ? "" : 'data-l="Zeit"'}>${leg ? "" : fmtDate(o.created_at)}</td>
      <td class="sym">${leg ? "↳" : esc(o.symbol)}</td>
      <td data-l="Art">${esc(o.side === "buy" ? "Kauf" : "Verkauf")} ${esc({ limit: leg ? "Ziel" : "Limit", stop: "Stop", market: "Markt" }[o.type] || o.type)}${o.order_class === "bracket" && !leg ? " · Bracket" : ""}</td>
      <td class="num" data-l="Stück (ausgeführt/gesamt)">${fmt(o.filled_qty, 4)}/${fmt(o.qty, 4)}</td>
      <td class="num" data-l="Limit / Stop">${fmt(o.limit_price ?? o.stop_price)}</td>
      <td data-l="Status">${esc(LABEL[o.status] || o.status)}${o.filled_avg_price ? `<span class="sub">Ø ${fmt(o.filled_avg_price)}${o.realized_pl_eur != null ? ` · ${signed(o.realized_pl_eur)} €` : ""}</span>` : ""}${o.note ? `<span class="sub">${esc(o.note)}</span>` : ""}</td>
      <td>${st === "open" && ["new", "held"].includes(o.status) ? `<button class="btn sm" data-cancel="${esc(o.id)}">Stornieren</button>` : ""}</td>
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
    thesis_id: tf.thesis_id.value || null,
  };
}

// Vorschlag aus der Claude-Bewertung ins Ticket übernehmen; die Order hängt sich an Claudes These
async function fillProposal(p) {
  tf.ticker.value = p.ticker; tf.price.value = p.price; tf.stop.value = p.stop; tf.target.value = p.target;
  tf.qty.value = p.qty; tf.horizon_days.value = p.horizon_days; tf.rationale.value = p.rationale;
  tf.model.value = p.model; tf.thesis_id.value = p.thesis_id;
  try {
    const k = await api(`/api/trade/ticket/${encodeURIComponent(p.ticker)}`);
    ticketMeta = { ticker: k.ticker, currency: k.currency, fx: k.fx };
  } catch (e) { toast(e.message); }
  invalidateTicket();
  $("#ticket-result").innerHTML = `<p class="muted small">Vorschlag von Claude (${esc(p.model)}) vom ${fmtDate(p.created)}. Kurs vom ${fmtDay(p.price_date)}.
    Werte prüfen und bei Bedarf anpassen, dann „Prüfen“. Die Order wird mit Claudes These ${esc(p.thesis_id)} verknüpft.</p>`;
}

let ticketMeta = { ticker: null, currency: "EUR", fx: 1 };

function renderTicketSum() {
  const o = ticketBody(), fx = ticketMeta.ticker === o.ticker ? ticketMeta.fx : null;
  $$("#ticket .cur").forEach((s) => (s.textContent = fx ? `(${ticketMeta.currency})` : ""));
  if (!o.qty || !o.price || !o.stop || !o.target || !fx) { $("#ticket-sum").innerHTML = ""; return; }
  const risk = (o.price - o.stop) * o.qty * fx, reward = (o.target - o.price) * o.qty * fx, vol = o.qty * o.price * fx;
  const eq = num(state.broker?.account?.equity);
  const items = [
    ["Volumen", `${money(vol)}${eq ? ` · ${fmt((vol / eq) * 100, 1)} %` : ""}`],
    ["Risiko bis Stop", `${money(risk)}${eq ? ` · ${fmt((risk / eq) * 100, 2)} %` : ""}`],
    ["Chance bis Ziel", money(reward)], ["Chance/Risiko vor Gebühren", risk > 0 ? `${fmt(reward / risk, 1)} : 1` : "–"],
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
tf.ticker.addEventListener("input", () => (tf.thesis_id.value = ""));  // anderer Titel = eigene These

async function prefill() {
  const t = tf.ticker.value.trim().toUpperCase();
  if (!t) return;
  try {
    const k = await api(`/api/trade/ticket/${encodeURIComponent(t)}`);
    ticketMeta = { ticker: k.ticker, currency: k.currency, fx: k.fx };
    tf.ticker.value = k.ticker;
    tf.price.value = k.price;
    tf.stop.value = k.stop;
    tf.target.value = k.target;
    tf.qty.value = k.qty;
    invalidateTicket();
    $("#ticket-result").innerHTML = k.core
      ? `<p class="small neg">${esc(k.ticker)} ist Core-Bestand – Kauf über „Core auffüllen“ im Dashboard.</p>`
      : `<p class="muted small">Vorschlag: letzter Schluss ${money(k.price, k.currency)} vom ${fmtDay(k.price_date)}, Stop 1,5 × ATR, Ziel 3 × ATR,
        Stückzahl nach Risiko-Limits (≈ ${money(k.value_eur)}).${k.qty ? "" : ' <span class="neg">Limits lassen keinen Kauf zu.</span>'}</p>`;
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
    if (ticketMeta.ticker !== o.ticker) {  // Titel von Hand eingetippt: Währung und Kurs nachladen
      const k = await api(`/api/trade/ticket/${encodeURIComponent(o.ticker)}`);
      ticketMeta = { ticker: k.ticker, currency: k.currency, fx: k.fx };
      renderTicketSum();
    }
    const r = await api("/api/trade/check", { method: "POST", body: o });
    checkedOrder = o;
    $("#btn-send").disabled = false;
    const pricey = r.odds && r.odds.breakeven_pct >= 50;
    $("#ticket-result").innerHTML = `<div class="result ${pricey ? "warn" : "ok"}"><span class="status ${pricey ? "" : "ok"}">${pricey ? "Limits eingehalten – aber nach Gebühren ungünstig" : "Prüfung bestanden"}</span>
      <p class="small">Letzter Kurs ${fmt(r.last_price)} · ${money(r.risk.value_eur)} + ${money(r.risk.fee_eur)} Gebühr · max. ${fmt(r.risk.suggested_qty, 4)} Stück nach Limits. Order kann gesendet werden.</p>${oddsLine(r.odds)}</div>`;
  } catch (err) { checkedOrder = null; $("#btn-send").disabled = true; showViolations(err); }
});

$("#btn-send").addEventListener("click", async () => {
  if (!checkedOrder) return;
  const o = checkedOrder;
  const ok = await confirmDialog("Order senden?", `
    <dl class="facts">
      <div><dt>Titel</dt><dd>${esc(o.ticker)}</dd></div>
      <div><dt>Kauf (Limit, bis Storno)</dt><dd>${fmt(o.qty, 4)} × ${money(o.price, ticketMeta.currency)}</dd></div>
      <div><dt>Stop-Loss</dt><dd>${money(o.stop, ticketMeta.currency)}</dd></div>
      <div><dt>Ziel</dt><dd>${money(o.target, ticketMeta.currency)}</dd></div>
      <div><dt>Volumen</dt><dd>${money(o.qty * o.price * ticketMeta.fx)}</dd></div>
    </dl>
    <p class="muted small">Paper-Depot. Die Order wird serverseitig erneut geprüft; ${o.thesis_id ? "sie wird mit Claudes These verknüpft" : "dazu wird eine These im Journal angelegt"}.</p>`, "Order senden");
  if (!ok) return;
  try {
    const r = await api("/api/trade/order", { method: "POST", body: o });
    toast(`Order ${o.ticker} übermittelt (${r.order.status})`);
    $("#ticket-result").innerHTML = `<div class="result ok"><span class="status ok">Order übermittelt</span>
      <p class="small">Status ${esc(r.order.status)} · These ${esc(r.thesis.id)} ${o.thesis_id ? "verknüpft" : "angelegt"}, Frist ${esc(r.thesis.deadline)}.</p></div>`;
    tf.rationale.value = "";
    tf.thesis_id.value = "";
    checkedOrder = null;
    $("#btn-send").disabled = true;
    loadOrders(); loadPositions($("#pos-table tbody"));
  } catch (err) { showViolations(err); }
});

// --- Journal ----------------------------------------------------------------------------
const STATUS_LABEL = { open: "offen", hit: "Treffer", miss: "Fehlschlag", cancelled: "storniert" };

let thesisFormWanted = false;

loaders.journal = async () => {
  const td = $("#thesis-details");
  if (thesisFormWanted || !isPhone()) td.open = true;
  else if (!$("#thesis-form").ticker.value) td.open = false;
  thesisFormWanted = false;
  const filter = $("#thesis-filter").value;
  const [list, st, acts] = await Promise.all([
    api(`/api/theses${filter ? `?status=${filter}` : ""}`), api("/api/stats"), api("/api/events?limit=60"),
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
        ${t.status === "open" ? `<details class="fold manual"><summary>Von Hand auswerten</summary>
            <p class="muted small">Normalerweise wertet die App Thesen selbst aus, sobald Ziel, Stop oder Frist erreicht sind.</p>
            <div class="actions">
            <input type="number" step="any" placeholder="Kurs" aria-label="Kurs bei Auswertung">
            <button class="btn sm" data-status="hit">Treffer</button><button class="btn sm" data-status="miss">Fehlschlag</button>
            <button class="btn sm" data-status="cancelled">Stornieren</button></div></details>`
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

  $("#action-table tbody").innerHTML = acts.length ? acts.map((e) =>
    `<tr><td>${e.ts.length > 10 ? fmtDate(e.ts) : fmtDay(e.ts)}</td><td class="wrap ${TONE[e.tone] || ""}">${esc(e.text)}</td></tr>`).join("")
    : `<tr><td colspan="2" class="empty">Noch keine Aktionen protokolliert.</td></tr>`;
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
const RISK_KEYS = ["max_position_pct", "max_risk_per_trade_pct", "max_daily_loss_pct", "max_drawdown_pct",
  "max_open_positions", "min_order_eur", "max_fee_pct"];

async function loadConfig() {
  const c = await api("/api/config");
  state.config = c;
  $("#universe-list").innerHTML = c.universe.map((t) => `<option value="${esc(t)}">`).join("");
  return c;
}

loaders.einstellungen = async () => {
  loadBroker().then((b) => { if (b.account) $("#reset-form").start_cash_eur.value = b.account.start_cash; }).catch(() => {});
  const c = await loadConfig();
  const [coreTicker, corePct] = Object.entries(c.portfolio.core)[0] || ["", 0];
  sf.core_ticker.value = coreTicker;
  sf.core_pct.value = corePct;
  sf.satellite_pct.value = c.portfolio.satellite_pct;
  sf.rebalance_drift_pct.value = c.portfolio.rebalance_drift_pct;
  sf.universe.value = c.universe.join("\n");
  sf.benchmark.value = c.benchmark || "";
  SCREEN_KEYS.forEach((k) => (sf[k].value = c.screen[k]));
  RISK_KEYS.forEach((k) => (sf[k].value = c.risk[k]));
  sf.enforce_market_hours.checked = c.risk.enforce_market_hours;
  const sc = c.schedule || { enabled: false, times: [], tz: "Europe/Berlin" };
  sf.schedule_enabled.checked = sc.enabled;
  sf.schedule_times.value = sc.times.join(", ");
  $("#sched-tz").textContent = sc.tz;
  loadAdvisorSettings().catch((e) => toast(e.message));
};

// --- Claude-Einstellungen (Token getrennt von der Konfiguration, wird nie zurückgegeben) ---
const af = $("#advisor-form"), tkf = $("#token-form");

async function loadAdvisorSettings() {
  const a = await api("/api/advisor");
  af.enabled.checked = a.enabled; af.auto_trade.checked = !!a.auto_trade; af.model.value = a.model; af.top_n.value = a.top_n;
  const pill = $("#adv-pill");
  pill.textContent = !a.cli_available ? "CLI fehlt" : a.token_configured ? (a.enabled ? "aktiv" : "Token hinterlegt") : "kein Token";
  pill.className = `pill ${!a.cli_available ? "bad" : a.token_configured && a.enabled ? "good" : a.token_configured ? "" : "warn"}`;
  $("#btn-token-delete").disabled = !a.token_configured;
  $("#btn-advise").disabled = !a.token_configured || !a.cli_available;
  tkf.token.placeholder = a.token_configured ? "hinterlegt – zum Ersetzen neues Token einfügen" : "sk-ant-…";
}

tkf.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#token-msg");
  try {
    await api("/api/advisor/token", { method: "PUT", body: { token: tkf.token.value } });
    tkf.token.value = "";
    msg.className = "small status ok"; msg.textContent = "Token gespeichert";
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
  loadAdvisorSettings();
});

$("#btn-token-delete").addEventListener("click", async () => {
  if (!(await confirmDialog("Token entfernen?", "<p>Claude kann danach nicht mehr bewerten, bis du ein neues Token hinterlegst. Widerrufen kannst du es in deinem Claude-Konto.</p>", "Entfernen"))) return;
  try { await api("/api/advisor/token", { method: "DELETE" }); toast("Token entfernt"); } catch (e) { toast(e.message); }
  loadAdvisorSettings();
});

af.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#advisor-msg");
  try {
    const c = await loadConfig();
    if (af.auto_trade.checked && !c.advisor?.auto_trade &&
        !(await confirmDialog("Automatisch buchen einschalten?", "<p>Ab dem nächsten Lauf bucht die App Claudes Vorschläge ohne Rückfrage als <b>Paper-Order</b>, sofern alle Risiko-Prüfungen bestehen. Es werden nur Käufe gebucht, nie Verkäufe, und es fließt kein echtes Geld.</p>", "Einschalten"))) return;
    await api("/api/config", { method: "PUT", body: { ...c, advisor: { enabled: af.enabled.checked, auto_trade: af.auto_trade.checked, model: af.model.value, top_n: Number(af.top_n.value) } } });
    msg.className = "small status ok"; msg.textContent = "Gespeichert";
    loadConfig(); loadAdvisorSettings();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
});

$("#btn-advise").addEventListener("click", async () => {
  try { await api("/api/advisor/run", { method: "POST" }); toast("Claude bewertet … das dauert etwa eine Minute"); } catch (e) { toast(e.message); }
  loadStatus();
});

sf.addEventListener("submit", async (e) => {
  e.preventDefault();
  const c = state.config, msg = $("#settings-msg");
  const body = {
    ...c,
    universe: sf.universe.value.split(/[\s,;]+/).map((t) => t.trim().toUpperCase()).filter(Boolean),
    benchmark: sf.benchmark.value.trim().toUpperCase(),
    portfolio: {
      ...c.portfolio,
      core: sf.core_ticker.value.trim() ? { [sf.core_ticker.value.trim().toUpperCase()]: Number(sf.core_pct.value) } : {},
      satellite_pct: Number(sf.satellite_pct.value), rebalance_drift_pct: Number(sf.rebalance_drift_pct.value),
    },
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

$("#reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#reset-msg");
  if (f.confirm.value.trim() !== "RESET") { msg.className = "small status bad"; msg.textContent = "Zur Bestätigung RESET eintippen"; return; }
  const cash = Number(f.start_cash_eur.value);
  if (!(await confirmDialog("Paper-Depot neu starten?", `<p>Alle Positionen, Orders und die Depot-Historie werden gelöscht. Neues Startkapital: <b>${money(cash, "EUR", 0)}</b>.</p>`, "Neu starten"))) return;
  try {
    await api("/api/broker/reset", { method: "POST", body: { start_cash_eur: cash, confirm: "RESET" } });
    f.confirm.value = "";
    msg.className = "small status ok"; msg.textContent = "Depot neu gestartet";
    loadBroker();
  } catch (err) { msg.className = "small status bad"; msg.textContent = err.message; }
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
