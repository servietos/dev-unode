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

async function save(msg = "Gespeichert") {
  try {
    const data = await api("api/stations", { method: "PUT", body: { categories: cats } });
    cats = data.categories;
    toast(msg);
  } catch (e) {
    toast(e.message, true);
    await load();
  }
  render();
}
async function load() {
  cats = (await api("api/stations")).categories;
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
  $("#cat-meta").textContent = c ? `${c.stations.length} Sender · erscheint am Marantz unter „My Stations“` : "Lege links eine Kategorie an.";
  $("#station-add").hidden = !c;
  $("#cat-rename").hidden = $("#cat-delete").hidden = !c;
  const ul = $("#station-list");
  const list = c ? c.stations : [];
  $("#station-empty").hidden = !c || list.length > 0;
  ul.innerHTML = list.map((s, i) => `<li class="station" draggable="true" data-i="${i}">
      <span class="grip" title="Ziehen zum Sortieren oder auf eine Kategorie">⋮⋮</span>
      <button class="icon-btn play" data-act="play" title="Im Browser vorhören">▶</button>
      <div class="st-main"><div class="st-name">${esc(s.name)}${s.url.startsWith("https://") ? '<span class="badge">HTTPS · Relay</span>' : ""}${badge(s.url)}</div>
        <div class="st-url" title="${esc(s.url)}">${esc(s.url)}</div></div>
      <div class="st-actions">
        <button class="icon-btn" data-act="check" title="Stream testen">✓</button>
        <button class="icon-btn" data-act="edit" title="Bearbeiten">✎</button>
        <button class="icon-btn" data-act="del" title="Löschen">✕</button>
      </div></li>`).join("");
  ul.querySelectorAll(".station").forEach((li) => {
    const i = +li.dataset.i;
    li.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act) stationAction(act, i);
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

async function stationAction(act, i) {
  const arr = cats[current].stations;
  const s = arr[i];
  if (act === "play") return play(s.name, s.url);
  if (act === "check") return checkUrl(s.url, s.name);
  if (act === "del") { arr.splice(i, 1); return save(`„${s.name}“ gelöscht`); }
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
    cats = (await api("api/stations/import", { method: "POST", body: { yaml: fd.get("yaml"), mode: fd.get("mode") } })).categories;
    current = 0; render(); toast("Import gespeichert");
  } catch (e) { toast(e.message, true); }
});

// ---------- Suche ----------
function renderTargets() {
  const sel = $("#search-target"), keep = sel.value;
  sel.innerHTML = cats.map((c, i) => `<option value="${i}">${esc(c.name)}</option>`).join("");
  if (keep && cats[+keep]) sel.value = keep; else sel.value = String(current);
}
let results = [];
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
        <div class="row"><button class="btn ghost small" data-i="${i}" data-act="play">▶ Vorhören</button>
          <button class="btn small" data-i="${i}" data-act="add">+ Hinzufügen</button></div></div></div>`).join("");
});
$("#search-results").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const r = results[+b.dataset.i];
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
function setPill(el, ok, text) { el.className = "pill " + (ok === null ? "" : ok ? "ok" : "bad"); el.textContent = text; }

async function refreshStatus() {
  let s;
  try { s = await api("api/status"); } catch { setPill($("#pill-ycast"), false, "Weboberfläche getrennt"); return; }
  ycastIp = s.ip;
  setPill($("#pill-ycast"), s.ycast.ok, s.ycast.ok ? `YCast läuft · ${s.ip}` : "YCast nicht erreichbar");
  const last = s.last_receiver;
  setPill($("#pill-avr"), last ? (Date.now() / 1000 - last.t < 86400) : null, last ? `Receiver ${ago(last.t)}` : "Receiver noch nie gesehen");

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

load().catch((e) => toast(e.message, true));
refreshStatus();
setInterval(refreshStatus, 15000);
