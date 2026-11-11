"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let cats = [];
let current = 0;
let ycastIp = "";
const checks = {}; // url -> {ok, label}

// ---------- Hilfen ----------
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Fehler ${res.status}`);
  return data;
}
let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), bad ? 5000 : 2200);
}
function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} min`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleString("de-DE");
}
function dialog(title, bodyHtml, okLabel = "OK") {
  const dlg = $("#dlg");
  $("#dlg-title").textContent = title;
  $("#dlg-body").innerHTML = bodyHtml;
  $("#dlg-ok").textContent = okLabel;
  dlg.showModal();
  const first = $("#dlg-body input, #dlg-body textarea, #dlg-body select");
  if (first) first.focus();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      resolve(dlg.returnValue === "ok" ? new FormData($("#dlg-form")) : null);
    }, { once: true });
  });
}

let revision = null; // Stand der Senderliste auf dem Server (Konfliktprüfung bei zwei offenen Tabs)

async function save(msg = "Gespeichert") {
  try {
    const data = await api("api/stations", { method: "PUT", body: { categories: cats, revision } });
    cats = data.categories; revision = data.revision;
    toast(msg);
  } catch (e) {
    toast(e.message, true);
    try { await load(); } catch (e2) { toast("Senderliste konnte nicht neu geladen werden: " + e2.message, true); }
  } finally {
    render();
  }
}
async function load() {
  const data = await api("api/stations");
  cats = data.categories; revision = data.revision;
  current = Math.min(current, Math.max(0, cats.length - 1));
  render();
}

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "setup") refreshStatus();
}));

// ---------- Kategorien ----------
function render() { renderCats(); renderStations(); renderTargets(); }

function renderCats() {
  const ul = $("#cat-list");
  ul.innerHTML = cats.map((c, i) => `<li draggable="true" data-i="${i}" class="${i === current ? "active" : ""}">
    <span>${esc(c.name)}</span><span class="count">${c.stations.length}</span></li>`).join("");
  ul.querySelectorAll("li").forEach((li) => {
    const i = +li.dataset.i;
    li.addEventListener("click", () => { current = i; render(); });
    li.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/cat", i));
    li.addEventListener("dragover", (e) => { e.preventDefault(); li.classList.add("drop"); });
    li.addEventListener("dragleave", () => li.classList.remove("drop"));
    li.addEventListener("drop", (e) => {
      e.preventDefault(); li.classList.remove("drop");
      const st = e.dataTransfer.getData("text/station");
      if (st !== "") { // Sender in andere Kategorie ziehen
        if (i === current) return;
        moveStation(+st, i);
        return;
      }
      const from = +e.dataTransfer.getData("text/cat");
      if (Number.isNaN(from) || from === i) return;
      const [c] = cats.splice(from, 1); cats.splice(i, 0, c);
      current = i; save("Reihenfolge gespeichert");
    });
  });
}

function moveStation(idx, toCat) {
  const st = cats[current].stations[idx];
  if (cats[toCat].stations.some((s) => s.name === st.name)) return toast(`„${st.name}“ gibt es dort schon`, true);
  cats[current].stations.splice(idx, 1);
  cats[toCat].stations.push(st);
  save(`Nach „${cats[toCat].name}“ verschoben`);
}

$("#cat-add").addEventListener("click", async () => {
  const fd = await dialog("Neue Kategorie", `<label>Name<input name="name" required></label>`, "Anlegen");
  const name = fd?.get("name")?.trim();
  if (!name) return;
  if (cats.some((c) => c.name === name)) return toast("Kategorie gibt es schon", true);
  cats.push({ name, stations: [] }); current = cats.length - 1; save("Kategorie angelegt");
});
$("#cat-rename").addEventListener("click", async () => {
  const c = cats[current]; if (!c) return;
  const fd = await dialog("Kategorie umbenennen", `<label>Name<input name="name" value="${esc(c.name)}" required></label>`, "Speichern");
  const name = fd?.get("name")?.trim();
  if (!name || name === c.name) return;
  if (cats.some((x) => x.name === name)) return toast("Kategorie gibt es schon", true);
  c.name = name; save();
});
$("#cat-delete").addEventListener("click", async () => {
  const c = cats[current]; if (!c) return;
  const fd = await dialog("Kategorie löschen?", `<p>„${esc(c.name)}“ mit ${c.stations.length} Sendern wird gelöscht. Die vorige Fassung bleibt als <code>stations.yml.bak</code> erhalten.</p>`, "Löschen");
  if (!fd) return;
  cats.splice(current, 1); current = 0; save("Kategorie gelöscht");
});

// ---------- Sender ----------
function badge(url) {
  const c = checks[url];
  if (!c) return "";
  return `<span class="badge ${c.ok ? "ok" : "bad"}">${esc(c.label)}</span>`;
}

function renderStations() {
  const c = cats[current];
  $("#cat-title").textContent = c ? c.name : "Keine Kategorie";
  $("#cat-meta").textContent = c ? `${c.stations.length} Sender · erscheint am Marantz unter „My Stations“` : "Lege oben eine neue Kategorie an.";
  $("#station-add").hidden = !c;
  $("#cat-rename").hidden = $("#cat-delete").hidden = !c;
  const ul = $("#station-list");
  const list = c ? c.stations : [];
  $("#station-empty").hidden = !c || list.length > 0;
  ul.innerHTML = list.map((s, i) => `<li class="station${playingUrl === s.url ? " playing" : ""}" draggable="true" data-i="${i}">
      <span class="grip" title="Ziehen zum Sortieren oder auf eine Kategorie">⋮⋮</span>
      <div class="st-main"><div class="st-name">${esc(s.name)}${badge(s.url)}</div>
        <div class="st-url" title="${esc(s.url)}">${s.url.startsWith("https://") ? "HTTPS über Relay · " : ""}${esc(s.url)}</div></div>
      <div class="st-actions">
        <button class="btn small st-play" data-act="marantz" title="Auf dem Marantz abspielen"><svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 1l7 4-7 4z"/></svg>Abspielen</button>
        <span class="more">
          <button class="icon-btn" data-act="menu" title="Weitere Aktionen" aria-haspopup="true" aria-expanded="false">⋯</button>
          <span class="more-menu" hidden>
            <button data-act="play">Im Browser vorhören</button>
            <button data-act="check">Stream testen</button>
            <button data-act="up" ${i === 0 ? "disabled" : ""}>Nach oben</button>
            <button data-act="down" ${i === list.length - 1 ? "disabled" : ""}>Nach unten</button>
            <button data-act="edit">Bearbeiten</button>
            <button data-act="del" class="danger">Löschen</button>
          </span>
        </span>
      </div></li>`).join("");
  ul.querySelectorAll(".station").forEach((li) => {
    const i = +li.dataset.i;
    li.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn || btn.disabled) return;
      if (btn.dataset.act === "menu") return toggleMenu(btn);
      closeMenus();
      stationAction(btn.dataset.act, i);
    });
    li.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/station", i); li.classList.add("dragging"); });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("dragover", (e) => { e.preventDefault(); li.classList.add("over"); });
    li.addEventListener("dragleave", () => li.classList.remove("over"));
    li.addEventListener("drop", (e) => {
      e.preventDefault(); li.classList.remove("over");
      const from = e.dataTransfer.getData("text/station");
      if (from === "" || +from === i) return;
      const arr = cats[current].stations;
      const [s] = arr.splice(+from, 1); arr.splice(i, 0, s);
      save("Reihenfolge gespeichert");
    });
  });
}

async function checkUrl(url, name) {
  checks[url] = { ok: true, label: "teste …" }; renderStations();
  try {
    const r = await api("api/check", { method: "POST", body: { url } });
    const d = r.direct;
    if (d.ok) {
      const bits = [d.bitrate && `${d.bitrate} kbit/s`, d.type.split(";")[0]].filter(Boolean).join(" · ");
      checks[url] = { ok: true, label: "läuft" + (bits ? " · " + bits : "") };
      toast(`${name}: Stream erreichbar`);
    } else {
      checks[url] = { ok: false, label: d.status ? `Fehler ${d.status}` : (d.error || "nicht erreichbar") };
      toast(`${name}: Stream nicht erreichbar`, true);
    }
  } catch (e) { checks[url] = { ok: false, label: "Fehler" }; toast(e.message, true); }
  renderStations();
}

function closeMenus() {
  document.querySelectorAll(".more-menu").forEach((m) => { m.hidden = true; });
  document.querySelectorAll('[data-act="menu"]').forEach((b) => b.setAttribute("aria-expanded", "false"));
}
function toggleMenu(btn) {
  const menu = btn.nextElementSibling, open = menu.hidden;
  closeMenus();
  menu.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  if (open) menu.querySelector("button:not([disabled])")?.focus();
}
document.addEventListener("click", (e) => { if (!e.target.closest(".more")) closeMenus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });

async function stationAction(act, i) {
  const arr = cats[current].stations;
  const s = arr[i];
  if (act === "marantz") return playOnMarantz(s.name, s.url);
  if (act === "play") return play(s.name, s.url);
  if (act === "check") return checkUrl(s.url, s.name);
  if (act === "up" || act === "down") {
    const j = act === "up" ? i - 1 : i + 1;
    if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    return save("Reihenfolge gespeichert");
  }
  if (act === "del") {
    const fd = await dialog("Sender löschen?", `<p>„${esc(s.name)}“ wird aus „${esc(cats[current].name)}“ entfernt.</p>`, "Löschen");
    if (!fd) return;
    arr.splice(i, 1); return save(`„${s.name}“ gelöscht`);
  }
  if (act === "edit") {
    const opts = cats.map((c, ci) => `<option value="${ci}" ${ci === current ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    const fd = await dialog("Sender bearbeiten", `
      <label>Name<input name="name" value="${esc(s.name)}" required></label>
      <label>Stream-URL<input name="url" value="${esc(s.url)}" required></label>
      <label>Kategorie<select name="cat">${opts}</select></label>`, "Speichern");
    if (!fd) return;
    const name = fd.get("name").trim(), url = fd.get("url").trim(), cat = +fd.get("cat");
    if (!name || !url) return;
    if (arr.some((x, xi) => xi !== i && x.name === name)) return toast("Name gibt es in der Kategorie schon", true);
    s.name = name; s.url = url;
    if (cat !== current) return moveStation(i, cat);
    save();
  }
}

$("#station-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const name = fd.get("name").trim(), url = fd.get("url").trim();
  if (cats[current].stations.some((s) => s.name === name)) return toast("Name gibt es in der Kategorie schon", true);
  cats[current].stations.push({ name, url });
  e.target.reset();
  save(`„${name}“ hinzugefügt`);
});

// ---------- Import / Export ----------
$("#btn-export").addEventListener("click", async () => {
  const text = await (await fetch("api/stations/export")).text();
  await dialog("Export (stations.yml)", `<textarea readonly>${esc(text)}</textarea>
    <p class="muted small">Text markieren und kopieren, oder <a href="api/stations/export" target="_blank" rel="noopener">als Datei öffnen</a>.</p>`, "Schließen");
});
$("#btn-import").addEventListener("click", async () => {
  const fd = await dialog("Import (YCast-Format)", `
    <textarea name="yaml" placeholder="Kategorie:\n  Sendername: http://stream-url"></textarea>
    <label>Modus<select name="mode"><option value="merge">Ergänzen (vorhandene bleiben)</option><option value="replace">Ersetzen (alles neu)</option></select></label>`, "Importieren");
  if (!fd) return;
  try {
    const data = await api("api/stations/import", { method: "POST", body: { yaml: fd.get("yaml"), mode: fd.get("mode") } });
    cats = data.categories; revision = data.revision;
    current = 0; render(); toast("Import gespeichert");
  } catch (e) { toast(e.message, true); }
});

// ---------- Suche ----------
function renderTargets() {
  for (const sel of [$("#search-target"), $("#manual-cat")]) {
    const keep = sel.value;
    sel.innerHTML = cats.map((c, i) => `<option value="${i}">${esc(c.name)}</option>`).join("");
    if (keep && cats[+keep]) sel.value = keep; else sel.value = String(current);
  }
}

// Länder und Genres aus dem Radio Browser (einmal pro Seitenaufruf)
let listsLoaded = false;
async function loadLists() {
  if (listsLoaded) return;
  listsLoaded = true;
  const dn = (() => { try { return new Intl.DisplayNames(["de"], { type: "region" }); } catch { return null; } })();
  const deName = (c) => { try { return (dn && dn.of(c.code)) || c.name; } catch { return c.name; } };
  try {
    const { countries } = await api("api/countries");
    const list = countries.map((c) => ({ ...c, de: deName(c) })).sort((a, b) => a.de.localeCompare(b.de, "de"));
    const opt = (c) => `<option value="${esc(c.code)}">${esc(c.de)} (${c.count})</option>`;
    const top = ["DE", "AT", "CH"].map((code) => list.find((c) => c.code === code)).filter(Boolean);
    const sel = $("#search-country"), keep = sel.value;
    sel.innerHTML = `<optgroup label="Häufig">${top.map(opt).join("")}</optgroup>
      <option value="">Alle Länder</option>
      <optgroup label="Alle Länder A–Z (${list.length})">${list.map(opt).join("")}</optgroup>`;
    sel.value = keep;
  } catch (e) { listsLoaded = false; toast("Länderliste: " + e.message, true); }
  try {
    const { tags } = await api("api/tags");
    const sel = $("#search-tag"), keep = sel.value;
    const sorted = [...tags].sort((a, b) => a.localeCompare(b, "de"));
    sel.innerHTML = `<option value="">Alle Genres</option>` + sorted.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
    sel.value = keep;
  } catch { /* Genre-Vorschläge sind optional */ }
}
document.querySelector('.tab[data-tab="search"]').addEventListener("click", loadLists);

// Manuell hinzufügen
const manual = $("#manual-form");
function manualValues() {
  return { name: manual.name.value.trim(), url: manual.url.value.trim(), ci: +manual.cat.value };
}
$("#manual-test").addEventListener("click", async () => {
  const { url } = manualValues();
  const out = $("#manual-result");
  if (!/^https?:\/\//.test(url)) { out.textContent = "Bitte eine URL mit http:// oder https:// eingeben."; return; }
  out.textContent = "Teste …";
  try {
    const d = (await api("api/check", { method: "POST", body: { url } })).direct;
    out.textContent = d.ok
      ? `Stream erreichbar: ${[d.name, d.bitrate && d.bitrate + " kbit/s", d.type.split(";")[0]].filter(Boolean).join(" · ")}`
        + (url.startsWith("https://") ? " · läuft am Marantz über das HTTPS-Relay" : "")
      : `Nicht erreichbar (${d.status ? "HTTP " + d.status : d.error || "keine Daten"}). Hinzufügen geht trotzdem.`;
    if (d.ok && d.name && !manual.name.value.trim()) manual.name.value = d.name.slice(0, 60);
  } catch (e) { out.textContent = e.message; }
});
$("#manual-play").addEventListener("click", () => {
  const { name, url } = manualValues();
  if (/^https?:\/\//.test(url)) play(name || url, url);
});
manual.addEventListener("submit", (e) => {
  e.preventDefault();
  const { name, url, ci } = manualValues();
  if (!cats[ci]) return toast("Lege zuerst eine Kategorie an", true);
  if (!/^https?:\/\//.test(url)) return toast("URL muss mit http:// oder https:// beginnen", true);
  if (cats[ci].stations.some((s) => s.name === name)) return toast(`„${name}“ ist schon in „${cats[ci].name}“`, true);
  cats[ci].stations.push({ name, url });
  manual.reset(); $("#manual-result").textContent = "";
  save(`„${name}“ zu „${cats[ci].name}“ hinzugefügt`);
});
let results = [];
// Land oder Genre gewechselt: sofort neu suchen
for (const id of ["#search-country", "#search-tag"]) {
  $(id).addEventListener("change", () => $("#search-form").requestSubmit());
}

// Darstellung: Auto / Hell / Dunkel, im Browser gemerkt
function applyTheme(t) {
  const theme = ["light", "auto"].includes(t) ? t : "dark";   // Standard: dunkel wie das Gerät
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll("[data-theme-set]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.themeSet === theme)));
}
let savedTheme = "dark";
try { savedTheme = localStorage.getItem("ycast-theme") || "dark"; } catch { /* privat */ }
applyTheme(savedTheme);
document.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => {
  const t = b.dataset.themeSet;
  try { localStorage.setItem("ycast-theme", t); } catch { /* privat */ }
  applyTheme(t);
}));

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const params = new URLSearchParams([...new FormData(e.target)].filter(([, v]) => v));
  const box = $("#search-results");
  box.innerHTML = `<p class="muted">Suche …</p>`;
  try {
    results = (await api("api/search?" + params)).results;
  } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  if (!results.length) { box.innerHTML = `<p class="muted">Nichts gefunden.</p>`; return; }
  box.innerHTML = results.map((r, i) => `<div class="result">
      ${r.favicon ? `<img src="${esc(r.favicon)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ph'}))">` : '<div class="ph"></div>'}
      <div><strong>${esc(r.name)}</strong>${r.hls ? '<span class="badge bad" title="HLS-Streams spielt der Marantz nicht ab">HLS · nicht am Marantz</span>' : ""}
        <div class="meta">${esc([r.country, r.codec, r.bitrate ? r.bitrate + " kbit/s" : "", r.tags].filter(Boolean).join(" · "))}</div>
        <div class="row"><button class="btn small" data-i="${i}" data-act="marantz" ${r.hls ? "disabled" : ""}>Auf dem Marantz abspielen</button>
          <button class="btn ghost small" data-i="${i}" data-act="play">Vorhören</button>
          <button class="btn ghost small" data-i="${i}" data-act="add">Zu meinen Sendern</button></div></div></div>`).join("");
});
$("#search-results").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const r = results[+b.dataset.i];
  if (b.dataset.act === "marantz") return playOnMarantz(r.name, r.url);
  if (b.dataset.act === "play") return play(r.name, r.url);
  const ci = +$("#search-target").value;
  if (!cats[ci]) return toast("Lege zuerst eine Kategorie an", true);
  let name = r.name.slice(0, 60) || "Sender";
  if (cats[ci].stations.some((s) => s.name === name)) return toast(`„${name}“ ist schon in „${cats[ci].name}“`, true);
  cats[ci].stations.push({ name, url: r.url });
  save(`„${name}“ zu „${cats[ci].name}“ hinzugefügt`);
});

// ---------- Player ----------
const audio = $("#audio");
function play(name, url) {
  $("#player").hidden = false;
  $("#player-name").textContent = name;
  $("#player-state").textContent = "verbinde …";
  audio.src = url;
  audio.play().catch(() => ($("#player-state").textContent = "Browser kann diesen Stream nicht abspielen"));
}
audio.addEventListener("playing", () => ($("#player-state").textContent = "läuft im Browser"));
audio.addEventListener("error", () => ($("#player-state").textContent = "Stream nicht abspielbar"));
$("#player-stop").addEventListener("click", () => { audio.pause(); audio.removeAttribute("src"); audio.load(); $("#player").hidden = true; });

// ---------- Status & Einrichtung ----------
function setPill(el, ok, text) {
  el.className = "dot" + (ok === null ? "" : ok ? " ok" : " bad");
  const label = $("#" + el.id + "-text");
  if (label) label.textContent = text;
}

async function refreshStatus() {
  let s;
  try { s = await api("api/status"); } catch { setPill($("#pill-ycast"), false, "getrennt"); return; }
  ycastIp = s.ip;
  setPill($("#pill-ycast"), s.ycast.ok, s.ycast.ok ? `läuft auf ${s.ip}` : "nicht erreichbar");
  const last = s.last_receiver;
  setPill($("#pill-avr"), last ? (Date.now() / 1000 - last.t < 86400) : null, last ? `gesehen ${ago(last.t)}` : "noch nie gesehen");

  document.querySelectorAll(".ip").forEach((el) => (el.textContent = s.ip || "–"));
  $("#dns-cli").textContent = `sudo pihole-FTL --config misc.dnsmasq_lines '["address=/vtuner.com/${s.ip || "?"}"]'`;
  $("#dns-env").textContent = `FTLCONF_misc_dnsmasq_lines: 'address=/vtuner.com/${s.ip || "?"}'`;
  $("#svc-info").innerHTML = `
    <dt>vTuner-Dienst</dt><dd>${s.ycast.ok ? "läuft" : "nicht erreichbar (" + esc(s.ycast.error || "") + ")"}</dd>
    <dt>LAN-IP</dt><dd>${esc(s.ip || "nicht gesetzt")} · Port 80</dd>
    <dt>„My Stations“</dt><dd>${s.counts.categories} Kategorien · ${s.counts.stations} Sender</dd>
    <dt>HTTPS-Relay</dt><dd>${s.ycast.relay ? "an: HTTPS-Streams laufen über YCast" : "aus"}</dd>`;
  $("#avr-info").innerHTML = last ? `
    <dt>Zuletzt gesehen</dt><dd>${ago(last.t)}</dd>
    <dt>IP</dt><dd>${esc(last.client)}</dd>
    <dt>Angefragt als</dt><dd>${esc(last.host)}</dd>
    <dt>Kennung</dt><dd>${esc(last.agent || "–")}</dd>` :
    `<dt>Status</dt><dd>Noch kein Abruf. Sobald der Marantz „Internet Radio“ öffnet und DNS stimmt, erscheint er hier.</dd>`;
  $("#log-body").innerHTML = s.requests.slice(0, 30).map((r) =>
    `<tr><td>${new Date(r.t * 1000).toLocaleTimeString("de-DE")}</td><td>${esc(r.client)}</td><td>${esc(r.path)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">Noch keine Abrufe.</td></tr>`;
  renderNetwork(s.network);
  const form = $("#pihole-form");
  if (document.activeElement?.form !== form) {
    form.p0.value = s.settings.piholes[0] || ""; form.p1.value = s.settings.piholes[1] || "";
  }
}

function renderNetwork(n) {
  if (!n) return;
  const a = n.active;
  $("#net-info").innerHTML = `
    <dt>Aktive IP</dt><dd><strong>${esc(a.ip || "–")}</strong>${n.saved ? "" : " (Standard)"}</dd>
    <dt>Netz</dt><dd>${esc(a.subnet || "–")} · Gateway ${esc(a.gateway || "–")}</dd>
    <dt>Adapter</dt><dd>${esc(a.parent || "–")}${a.host_ip ? " · Umbrel " + esc(a.host_ip) : ""}</dd>`;
  $("#net-note").hidden = !a.note;
  $("#net-note").textContent = a.note || "";
  const want = n.saved || n.default;
  $("#net-pending").hidden = !n.pending;
  $("#net-pending").innerHTML = n.pending ? `Gespeichert: <strong>${esc(want)}</strong>. Gilt nach einem Neustart der App
    (Umbrel-Dashboard: YCast-Symbol gedrückt halten bzw. Rechtsklick → <em>Neu starten</em>).
    Danach die Local DNS Records in den Pi-holes auf ${esc(want)} ändern.` : "";
  const inp = $("#net-ip");
  if (document.activeElement !== inp) inp.value = n.saved || "";
  inp.placeholder = n.default ? `Standard: ${n.default}` : "z. B. 192.168.178.210";
}

async function saveNetwork(ip) {
  try {
    renderNetwork(await api("api/network", { method: "PUT", body: { ip } }));
    toast(ip ? "IP gespeichert, Neustart der App nötig" : "Standard-IP gespeichert");
  } catch (err) { toast(err.message, true); }
}
$("#net-form").addEventListener("submit", (e) => { e.preventDefault(); saveNetwork($("#net-ip").value.trim()); });
$("#net-default").addEventListener("click", () => { $("#net-ip").value = ""; saveNetwork(""); });

$("#pihole-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try { await api("api/settings", { method: "PUT", body: { piholes: [f.p0.value, f.p1.value] } }); toast("Pi-holes gespeichert"); }
  catch (err) { toast(err.message, true); }
});
$("#dns-check").addEventListener("click", async () => {
  const box = $("#dns-results");
  box.innerHTML = `<p class="muted">Prüfe …</p>`;
  try {
    const r = await api("api/dns");
    if (!r.results.length) { box.innerHTML = `<p class="muted">Trage oben mindestens einen Pi-hole ein und speichere.</p>`; return; }
    box.innerHTML = r.results.map((x) => `<div class="dns-row"><span class="badge ${x.ok ? "ok" : "bad"}">${x.ok ? "OK" : "falsch"}</span>
      <code>${esc(x.host)}</code> über ${esc(x.server)} → ${esc(x.ips.join(", ") || x.error || "keine Antwort")}</div>`).join("");
  } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]"); if (!b) return;
  navigator.clipboard?.writeText($("#" + b.dataset.copy).textContent).then(() => toast("Kopiert"), () => toast("Bitte manuell kopieren", true));
});

// ---------- Marantz: Display und Fernbedienung ----------
const SOURCES = { IRADIO: "Internet Radio", DLNA: "Medienserver", SERVER: "Medienserver", USB: "USB", NET: "Netzwerk",
  BT: "Bluetooth", CD: "CD", TUNER: "Radio", FM: "UKW", DAB: "DAB", AUX: "AUX", "AUX1": "AUX", OPTICAL: "Optisch", AIRPLAY: "AirPlay", SPOTIFY: "Spotify" };
const DIAL_LEN = 433.5;         // Länge des Lautstärke-Bogens (270°) im SVG
let mz = null;                  // letzter Status
let playingUrl = "";
let mzBusy = false;
let volTimer;

function sourceName(s) {
  const key = (s.source || s.input || "").toUpperCase();
  return SOURCES[key] || s.source || s.input || "";
}

function renderMarantz() {
  const ph = $("#porthole"), title = $("#ph-title"), sub = $("#ph-sub"), src = $("#ph-source"), vol = $("#ph-vol");
  const power = $("#btn-power"), mute = $("#btn-mute"), range = $("#vol-range"), note = $("#deck-note");
  const controls = [power, mute, range, $("#btn-vol-up"), $("#btn-vol-down")];
  if (!mz || !mz.configured) {
    ph.dataset.state = "offline"; src.textContent = "Marantz"; title.textContent = "Nicht verbunden";
    sub.textContent = "IP unter Einrichtung eintragen"; vol.textContent = "";
    controls.forEach((c) => (c.disabled = true)); note.textContent = ""; setDial(0); return;
  }
  if (!mz.reachable) {
    ph.dataset.state = "offline"; src.textContent = "Marantz"; title.textContent = "Nicht erreichbar";
    sub.textContent = mz.ip; vol.textContent = "";
    controls.forEach((c) => (c.disabled = true)); note.textContent = "Ist der Marantz am Strom und im Netz?"; setDial(0); return;
  }
  controls.forEach((c) => (c.disabled = false));
  power.setAttribute("aria-pressed", String(mz.power));
  power.setAttribute("aria-label", mz.power ? "In Standby schalten" : "Einschalten");
  mute.setAttribute("aria-pressed", String(mz.mute));
  range.max = mz.volume_max || 60;
  if (document.activeElement !== range && mz.volume != null) range.value = mz.volume;
  if (!mz.power) {
    ph.dataset.state = "standby"; src.textContent = "Marantz"; title.textContent = "Standby";
    sub.textContent = ""; vol.textContent = ""; note.textContent = "Tippe auf Abspielen bei einem Sender, der Marantz schaltet sich dann ein.";
    [mute, range, $("#btn-vol-up"), $("#btn-vol-down")].forEach((c) => (c.disabled = true));
    setDial(0); return;
  }
  ph.dataset.state = "on";
  const lines = (mz.lines || []).filter((l) => l && !/^now playing$/i.test(l));
  src.textContent = sourceName(mz);
  title.textContent = lines[0] || sourceName(mz) || "Bereit";
  sub.textContent = lines.slice(1, 3).join(" · ");
  vol.textContent = mz.mute ? "Stumm" : `Lautstärke ${mz.volume ?? "–"}`;
  note.textContent = "";
  setDial(mz.mute ? 0 : (mz.volume || 0) / (mz.volume_max || 60));
}

function setDial(fraction) {
  const f = Math.max(0, Math.min(1, fraction));
  $("#dial-level").style.strokeDasharray = `${(DIAL_LEN * f).toFixed(1)} 578`;
}

async function refreshMarantz() {
  if (mzBusy) return;
  try { mz = await api("api/marantz"); } catch (e) { mz = { configured: true, reachable: false, ip: "" }; }
  renderMarantz();
  renderMzSettings();
}

async function mzCommand(cmd, value) {
  try {
    await api("api/marantz/cmd", { method: "POST", body: { cmd, value } });
    setTimeout(refreshMarantz, cmd === "power_on" ? 2500 : 400);
  } catch (e) { toast(e.message, true); }
}

async function playOnMarantz(name, url) {
  if (mzBusy) return;
  mzBusy = true;
  const wasOff = mz && mz.reachable && !mz.power;
  $("#porthole").dataset.state = "on";
  $("#ph-source").textContent = "Starte";
  $("#ph-title").textContent = name;
  $("#ph-sub").textContent = wasOff ? "Marantz wird eingeschaltet …" : "";
  try {
    const r = await api("api/marantz/play", { method: "POST", body: { name, url } });
    playingUrl = url; renderStations();
    toast(`„${name}“ läuft auf dem Marantz${r.via_relay ? " (über das HTTPS-Relay)" : ""}`);
  } catch (e) { toast(e.message, true); }
  finally {
    mzBusy = false;
    setTimeout(refreshMarantz, 1500);
  }
}

$("#btn-power").addEventListener("click", () => mzCommand(mz && mz.power ? "standby" : "power_on"));
$("#btn-mute").addEventListener("click", () => mzCommand(mz && mz.mute ? "mute_off" : "mute_on"));
$("#btn-vol-up").addEventListener("click", () => mzCommand("vol_up"));
$("#btn-vol-down").addEventListener("click", () => mzCommand("vol_down"));
$("#vol-range").addEventListener("input", (e) => {
  const v = +e.target.value;
  setDial(v / (+e.target.max || 60));
  $("#ph-vol").textContent = `Lautstärke ${v}`;
  clearTimeout(volTimer);
  volTimer = setTimeout(() => mzCommand("volume", v), 250);
});

// Einrichtung: Marantz-IP
function renderMzSettings() {
  if (!mz) return;
  const how = { gespeichert: "fest eingetragen", erkannt: "aus den Abrufen erkannt" }[mz.how] || "";
  $("#mz-info").innerHTML = mz.configured ? `
    <dt>Adresse</dt><dd>${esc(mz.ip)}${how ? " (" + how + ")" : ""}</dd>
    <dt>Zustand</dt><dd>${mz.reachable ? (mz.power ? "eingeschaltet" : "Standby") : "nicht erreichbar"}</dd>` :
    `<dt>Adresse</dt><dd>noch unbekannt: öffne einmal Internet Radio am Marantz oder trage die IP ein</dd>`;
  const inp = $("#mz-ip");
  if (document.activeElement !== inp) inp.value = mz.how === "gespeichert" ? mz.ip : "";
  inp.placeholder = mz.how === "erkannt" ? `erkannt: ${mz.ip}` : "z. B. 192.168.178.56";
}
async function saveMz(ip) {
  try {
    await api("api/marantz/settings", { method: "PUT", body: { ip } });
    toast(ip ? "Marantz-Adresse gespeichert" : "Marantz wird automatisch erkannt");
    refreshMarantz();
  } catch (e) { toast(e.message, true); }
}
$("#mz-form").addEventListener("submit", (e) => { e.preventDefault(); saveMz($("#mz-ip").value.trim()); });
$("#mz-auto").addEventListener("click", () => { $("#mz-ip").value = ""; saveMz(""); });

load().catch((e) => toast(e.message, true));
refreshStatus();
refreshMarantz();
// Abfragen nur bei sichtbarer Seite: Marantz alle 5 s, Dienststatus alle 15 s (im Reiter Einrichtung) bzw. 60 s
let tick = 0;
setInterval(() => {
  if (document.visibilityState !== "visible") return;
  tick += 1;
  refreshMarantz();
  if ((!$("#tab-setup").hidden && tick % 3 === 0) || tick % 12 === 0) refreshStatus();
}, 5000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") { refreshMarantz(); refreshStatus(); }
});
