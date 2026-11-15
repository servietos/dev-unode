"use strict";
/* YCast, zweite Oberfläche „Daumenzone“: Hören zuerst.
   Bearbeiten, Sortieren, Import/Export und Einrichtung bleiben in der klassischen Ansicht (./). */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let cats = [], revision = null, recent = [], mz = null;
let band = "", playingUrl = "", busyUrl = "", results = [], pendingAdd = null;
let volTimer, searchTimer, searchSeq = 0;
const SLEEP_STEPS = [0, 15, 30, 60, 90];

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error ? t(data.error) : t("Fehler {n}", { n: res.status }));
  return data;
}
let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg; el.className = "toast" + (bad ? " bad" : ""); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.hidden = true), bad ? 5000 : 2400);
}
function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return t("gerade eben");
  if (s < 3600) return t("vor {n} min", { n: Math.round(s / 60) });
  if (s < 86400) return t("vor {n} h", { n: Math.round(s / 3600) });
  return new Date(ts * 1000).toLocaleDateString(LOCALE);
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* privat */ } },
};

// ---------- Kategorien („Bänder“) und Senderliste ----------
function bands() {
  const out = cats.map((c, i) => ({ id: "c" + i, name: c.name }));
  if (recent.length) out.unshift({ id: "recent", name: t("Zuletzt") });
  return out;
}
function stationsOf(id) {
  if (id === "recent") return recent;
  const c = cats[+String(id).slice(1)];
  return c ? c.stations : [];
}
function renderBands() {
  const list = bands();
  if (!list.some((b) => b.id === band)) band = store.get("ycast-neu-band") || "";
  if (!list.some((b) => b.id === band)) band = list.find((b) => b.id !== "recent")?.id || list[0]?.id || "";
  $("#bands").innerHTML = list.map((b) =>
    `<button type="button" class="band" role="tab" data-band="${b.id}" aria-selected="${b.id === band}">${esc(b.name)}</button>`).join("");
  $("#bands").setAttribute("role", "tablist");
  $(`[data-band="${band}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
}
function renderList() {
  const list = stationsOf(band);
  const empty = $("#empty");
  empty.hidden = list.length > 0;
  if (!cats.length && !recent.length) {
    empty.innerHTML = esc(t("Noch keine Sender. Suche oben nach einem Sender oder lege Kategorien in der klassischen Ansicht an."));
  } else {
    empty.textContent = band === "recent" ? t("Noch nichts gespielt.") : t("Diese Kategorie ist leer.");
  }
  $("#stations").innerHTML = list.map((s) => {
    const on = s.url === playingUrl;
    const hint = s.url === busyUrl ? t("Starte …") : on ? t("spielt") : band === "recent" && s.t ? ago(s.t) : "";
    return `<li><button type="button" class="station" data-url="${esc(s.url)}" data-name="${esc(s.name)}"
      aria-current="${on}" aria-busy="${s.url === busyUrl}"><span class="name">${esc(s.name)}</span>
      <span class="hint">${esc(hint)}</span></button></li>`;
  }).join("");
}
$("#bands").addEventListener("click", (e) => {
  const b = e.target.closest("[data-band]"); if (!b) return;
  band = b.dataset.band; store.set("ycast-neu-band", band);
  renderBands(); renderList(); renderScale();
});
$("#stations").addEventListener("click", (e) => {
  const b = e.target.closest(".station"); if (!b) return;
  play(b.dataset.name, b.dataset.url);
});

// ---------- Senderskala: die Sender der laufenden Kategorie als Striche, die Nadel zeigt den laufenden ----------
function scaleList() {
  const ci = cats.findIndex((c) => c.stations.some((s) => s.url === playingUrl));
  if (ci >= 0) return cats[ci].stations;
  const list = stationsOf(band);
  return list.length ? list : (cats[0] ? cats[0].stations : []);
}
function renderScale() {
  const list = scaleList(), n = list.length, box = $("#scale");
  const pos = (i) => (n === 1 ? 50 : (i / (n - 1)) * 100);
  const idx = list.findIndex((s) => s.url === playingUrl);
  // Mindestens 76 px je Sender: Name lesbar, Tipp-Ziel breit genug; bei vielen Sendern wird gewischt
  box.innerHTML = `<div class="scale-track" style="min-width:${Math.max(0, (n - 1) * 76)}px">${list.map((s, i) =>
    `<button type="button" class="tick" style="left:${pos(i)}%" data-i="${i}" aria-label="${esc(s.name)}"
      ${i === idx ? 'aria-current="true"' : ""}><span>${esc(s.name)}</span></button>`).join("")}
    <span class="needle" style="left:${idx >= 0 ? pos(idx) : 50}%"></span></div>`;
  box.toggleAttribute("data-empty", idx < 0);
  box.dataset.count = String(n);
  if (idx >= 0) {
    const track = box.firstElementChild;
    box.scrollLeft = Math.max(0, (track.offsetWidth * pos(idx)) / 100 - box.clientWidth / 2 + 44);
  }
}
$("#scale").addEventListener("click", (e) => {
  const b = e.target.closest(".tick"); if (!b) return;
  const s = scaleList()[+b.dataset.i];
  if (s) play(s.name, s.url);
});

// ---------- Wiedergabe ----------
function setPlayKey(on) {
  const k = $("#k-play");
  k.setAttribute("aria-pressed", String(on));
  k.setAttribute("aria-label", on ? t("Pause") : t("Abspielen"));
}
function setVolume(v, max) {
  const r = $("#vol");
  r.max = max || 60;
  if (document.activeElement !== r && v != null) r.value = v;
  r.style.setProperty("--fill", `${(+r.value / +r.max) * 100}%`);
}
function renderPlayer() {
  const p = $("#player"), name = $("#now-name"), title = $("#now-title");
  const fmt = $("#now-format"), rate = $("#now-rate"), vol = $("#now-vol");
  const keys = ["#k-prev", "#k-play", "#k-next", "#k-power", "#k-sleep", "#k-mute", "#k-down", "#k-up", "#vol"].map($);
  fmt.textContent = rate.textContent = vol.textContent = title.textContent = "";
  setPlayKey(false);
  if (!mz || !mz.configured || !mz.reachable) {
    p.dataset.state = "off";
    name.textContent = !mz || !mz.configured ? t("Nicht verbunden") : t("Nicht erreichbar");
    title.textContent = !mz || !mz.configured ? t("IP unter Einrichtung eintragen") : t("Ist der Marantz am Strom und im Netz?");
    keys.forEach((k) => (k.disabled = true));
    return;
  }
  keys.forEach((k) => (k.disabled = false));
  const power = $("#k-power");
  power.setAttribute("aria-pressed", String(mz.power));
  power.setAttribute("aria-label", mz.power ? t("In Standby schalten") : t("Einschalten"));
  $("#k-mute").setAttribute("aria-pressed", String(!!mz.mute));
  setVolume(mz.volume, mz.volume_max);
  const left = mz.sleep_left || 0;
  $("#sleep-min").textContent = left ? String(Math.ceil(left / 60)) : "";
  $("#k-sleep").setAttribute("aria-label", left ? t("Ausschalt-Timer: {n} min", { n: Math.ceil(left / 60) }) : t("Ausschalt-Timer"));
  if (!mz.power) {
    p.dataset.state = "standby";
    name.textContent = t("Standby");
    ["#k-mute", "#k-down", "#k-up", "#vol"].forEach((s) => ($(s).disabled = true));
    return;
  }
  p.dataset.state = "on";
  setPlayKey(mz.playing === true);
  vol.textContent = mz.mute ? t("Stumm") : t("Lautstärke {v}", { v: mz.volume ?? "–" });
  const lines = (mz.lines || []).map((l) => l.replace(/\s+/g, " ").trim()).filter((l) => l && !/^now playing$/i.test(l));
  const n = mz.now;
  if (n && n.station) {
    name.textContent = n.station;
    fmt.textContent = n.format || "";
    rate.textContent = n.out ? `→ ${n.out}` : n.kbps ? t("{n} kbit/s", { n: n.kbps }) : "";
    title.textContent = lines.filter((l) => l.toLowerCase() !== n.station.toLowerCase()).slice(0, 2).join(" – ");
    if (n.url !== playingUrl) { playingUrl = n.url; renderList(); renderScale(); }
  } else {
    name.textContent = lines[0] || mz.source || mz.input || t("Bereit");
    title.textContent = lines.slice(1, 3).join(" – ");
  }
}
async function refreshMarantz() {
  if (busyUrl) return;
  try { mz = await api("api/marantz"); } catch { mz = { configured: true, reachable: false }; }
  renderPlayer();
}
async function command(cmd, value) {
  try {
    await api("api/marantz/cmd", { method: "POST", body: { cmd, value } });
    setTimeout(refreshMarantz, cmd === "power_on" ? 2500 : 400);
  } catch (e) { toast(e.message, true); }
}
async function play(name, url) {
  if (busyUrl) return;
  busyUrl = url;
  const wasOff = mz && mz.reachable && !mz.power;
  $("#now-name").textContent = name;
  $("#now-title").textContent = wasOff ? t("Marantz wird eingeschaltet …") : t("Starte …");
  renderList();
  try {
    const r = await api("api/marantz/play", { method: "POST", body: { name, url } });
    playingUrl = url;
    const via = { relay: " (über das HTTPS-Relay)", hls: " (HLS, von YCast umgewandelt)", convert: " (von YCast in MP3 umgewandelt)" }[r.via];
    toast(t("„{name}“ läuft auf dem Marantz{via}", { name, via: via ? t(via) : "" }));
    loadRecent();
  } catch (e) { toast(e.message, true); }
  finally {
    busyUrl = "";
    renderList(); renderScale();
    setTimeout(refreshMarantz, 1500);
  }
}
function step(dir) {
  const list = scaleList();
  if (!list.length) return toast(t("Keine Sender in dieser Kategorie"), true);
  const i = list.findIndex((s) => s.url === playingUrl);
  const next = i < 0 ? (dir > 0 ? 0 : list.length - 1) : (i + dir + list.length) % list.length;
  play(list[next].name, list[next].url);
}
$("#k-prev").addEventListener("click", () => step(-1));
$("#k-next").addEventListener("click", () => step(1));
$("#k-play").addEventListener("click", () => {
  if (!mz || !mz.reachable || busyUrl) return;
  if (mz.power && mz.playing) { setPlayKey(false); return command("pause"); }
  if (mz.power && mz.transport && mz.transport !== "NO_MEDIA_PRESENT") { setPlayKey(true); return command("play"); }
  const s = scaleList().find((x) => x.url === playingUrl) || recent[0];
  if (s) play(s.name, s.url);
  else if (mz.power) command("play");
  else toast(t("Wähle zuerst einen Sender aus der Liste"), true);
});
$("#k-power").addEventListener("click", () => command(mz && mz.power ? "standby" : "power_on"));
$("#k-mute").addEventListener("click", () => command(mz && mz.mute ? "mute_off" : "mute_on"));
$("#k-up").addEventListener("click", () => command("vol_up"));
$("#k-down").addEventListener("click", () => command("vol_down"));
$("#vol").addEventListener("input", (e) => {
  const v = +e.target.value;
  setVolume(v, +e.target.max);
  $("#now-vol").textContent = t("Lautstärke {v}", { v });
  clearTimeout(volTimer);
  volTimer = setTimeout(() => command("volume", v), 250);
});
$("#k-sleep").addEventListener("click", async () => {
  const cur = mz && mz.sleep_left ? mz.sleep_minutes : 0;
  const minutes = SLEEP_STEPS[(SLEEP_STEPS.indexOf(cur) + 1) % SLEEP_STEPS.length];
  try {
    const r = await api("api/marantz/sleep", { method: "PUT", body: { minutes } });
    if (mz) Object.assign(mz, { sleep_left: r.sleep_left, sleep_minutes: r.sleep_minutes });
    renderPlayer();
    toast(minutes ? t("Der Marantz geht in {n} Minuten in Standby", { n: minutes }) : t("Ausschalt-Timer aus"));
  } catch (err) { toast(err.message, true); }
});

// ---------- Suchen und Hinzufügen ----------
const search = $("#search");
$("#open-search").addEventListener("click", () => { search.showModal(); $("#q").focus(); });
$("#close-search").addEventListener("click", () => search.close());
$("#search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
$("#q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  if ($("#q").value.trim().length >= 2) searchTimer = setTimeout(runSearch, 350);
});
async function runSearch() {
  const q = $("#q").value.trim();
  if (q.length < 2) return;
  const seq = ++searchSeq;
  $("#search-hint").textContent = t("Suche läuft …");
  try {
    const r = await api("api/search?" + new URLSearchParams({ name: q, order: "clickcount" }));
    if (seq !== searchSeq) return;
    results = r.results;
    $("#search-hint").textContent = results.length ? "" : t("Nichts gefunden für „{q}“.", { q });
    $("#search-hint").hidden = !!results.length;
    $("#results").innerHTML = results.map((x, i) => `<li class="result">
      <div class="result-main"><p>${esc(x.name)}</p>
        <p class="result-meta">${[x.country, x.codec, x.bitrate ? t("{n} kbit/s", { n: x.bitrate }) : ""]
          .filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("")}</p></div>
      <button type="button" class="key key-small" data-act="play" data-i="${i}" aria-label="${esc(t("Auf dem Marantz abspielen"))}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M8 5.5v13l10.5-6.5z"/></svg></button>
      <button type="button" class="key key-small" data-act="add" data-i="${i}" aria-label="${esc(t("Zu meinen Sendern"))}">+</button>
    </li>`).join("");
  } catch (err) {
    if (seq === searchSeq) { $("#search-hint").hidden = false; $("#search-hint").textContent = err.message; }
  }
}
$("#results").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const r = results[+b.dataset.i]; if (!r) return;
  if (b.dataset.act === "play") { search.close(); return play(r.name, r.url); }
  pendingAdd = r;
  const names = cats.length ? cats.map((c) => c.name) : [t("Favoriten")];
  $("#pick-list").innerHTML = names.map((n, i) => `<li><button type="button" data-ci="${i}">${esc(n)}</button></li>`).join("");
  $("#pick").showModal();
});
$("#pick-cancel").addEventListener("click", () => $("#pick").close());
$("#pick-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ci]"); if (!b || !pendingAdd) return;
  $("#pick").close();
  if (!cats.length) cats.push({ name: t("Favoriten"), stations: [] });
  const cat = cats[+b.dataset.ci], name = pendingAdd.name.trim().slice(0, 120) || t("Sender");
  if (cat.stations.some((s) => s.name === name)) return toast(t("„{name}“ ist schon in „{cat}“", { name, cat: cat.name }), true);
  cat.stations.push({ name, url: pendingAdd.url });
  try {
    const data = await api("api/stations", { method: "PUT", body: { categories: cats, revision } });
    cats = data.categories; revision = data.revision;
    toast(t("„{name}“ zu „{cat}“ hinzugefügt", { name, cat: cat.name }));
  } catch (err) { toast(err.message, true); await loadStations(); }
  renderBands(); renderList(); renderScale();
});

// ---------- Laden ----------
async function loadStations() {
  try { const d = await api("api/stations"); cats = d.categories; revision = d.revision; }
  catch (e) { toast(e.message, true); }
}
async function loadRecent() {
  try { recent = (await api("api/recent")).items || []; } catch { recent = []; }
  renderBands(); renderList(); renderScale();
}

// Platz für den unten stehenden Player freihalten (nur auf dem Handy)
const mobile = matchMedia("(max-width: 899px)");
function setPlayerH() {
  document.documentElement.style.setProperty("--player-h", mobile.matches ? `${$("#player").offsetHeight}px` : "0px");
}
new ResizeObserver(setPlayerH).observe($("#player"));
mobile.addEventListener("change", setPlayerH);

(async () => {
  await loadStations();
  await loadRecent();
  refreshMarantz();
})();
setInterval(() => { if (document.visibilityState === "visible") refreshMarantz(); }, 5000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { refreshMarantz(); loadRecent(); } });
