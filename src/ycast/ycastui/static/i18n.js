"use strict";
/* Sprache der Oberfläche: Deutsch (Quelltext) oder Englisch.
   Die deutschen Texte sind zugleich die Schlüssel. t("Lautstärke {v}", {v: 3}) übersetzt und setzt ein.
   Feste Texte im HTML übersetzt translateDom() beim Laden: Textstücke zwischen Tags und die Attribute
   placeholder, title, aria-label und label. Gewählt wird per Umschalter (im Browser gemerkt),
   sonst nach der Browsersprache. */
const LANG = (() => {
  let l = "";
  try { l = localStorage.getItem("ycast-lang") || ""; } catch { /* privat */ }
  if (l !== "de" && l !== "en") l = /^de\b/i.test(navigator.language || "") ? "de" : "en";
  return l;
})();
const LOCALE = LANG === "de" ? "de-DE" : "en-GB";
document.documentElement.lang = LANG;

const EN = {
  // ---------- Kopf, Display, Bedienung ----------
  "Darstellung": "Appearance", "Wie das System": "Same as system", "Hell": "Light", "Dunkel": "Dark",
  "Sprache": "Language",
  "Verbinde …": "Connecting …", "Einschalten": "Power on", "Ein / Standby": "On / standby",
  "In Standby schalten": "Switch to standby",
  "Vorheriger Sender": "Previous station", "Nächster Sender": "Next station",
  "Abspielen": "Play", "Pause": "Pause", "Abspielen / Pause": "Play / pause",
  "Stumm": "Mute", "Leiser": "Volume down", "Lauter": "Volume up", "Lautstärke": "Volume",
  "Lautstärke {v}": "Volume {v}",
  "Zuletzt gespielt": "Recently played", "Ausschalten in": "Sleep timer", "Aus": "Off",
  "15 Minuten": "15 minutes", "30 Minuten": "30 minutes", "60 Minuten": "60 minutes", "90 Minuten": "90 minutes",
  "noch {n} min": "{n} min left",
  "Der Marantz geht in {n} Minuten in Standby": "The Marantz will switch to standby in {n} minutes",
  "Ausschalt-Timer aus": "Sleep timer off",
  "Nicht verbunden": "Not connected", "IP unter Einrichtung eintragen": "Enter the IP under Setup",
  "Nicht erreichbar": "Not reachable", "Ist der Marantz am Strom und im Netz?": "Is the Marantz powered and on the network?",
  "Standby": "Standby", "Bereit": "Ready", "Stream": "Stream", "Starte": "Starting",
  "Tippe auf Abspielen bei einem Sender, der Marantz schaltet sich dann ein.":
    "Tap Play on a station and the Marantz will switch on.",
  "Marantz wird eingeschaltet …": "Switching on the Marantz …", "HLS wird umgewandelt …": "Converting HLS …",
  " (über das HTTPS-Relay)": " (via the HTTPS relay)", " (HLS, von YCast umgewandelt)": " (HLS, converted by YCast)",
  " (von YCast in MP3 umgewandelt)": " (converted to MP3 by YCast)",
  "„{name}“ läuft auf dem Marantz{via}": "\"{name}\" is playing on the Marantz{via}",
  "Wähle zuerst einen Sender aus der Liste": "Pick a station from the list first",
  "Keine Sender in dieser Kategorie": "No stations in this category",
  "Medienserver": "Media server", "Netzwerk": "Network", "Radio": "Radio", "UKW": "FM", "Optisch": "Optical",
  "{name} auf dem Marantz abspielen": "Play {name} on the Marantz",

  // ---------- Reiter, Meine Sender ----------
  "Meine Sender": "My stations", "Sender suchen": "Find stations", "Einrichtung": "Setup",
  "Kategorien": "Categories", "Neue Kategorie": "New category", "Umbenennen": "Rename", "Löschen": "Delete",
  "Noch keine Sender in dieser Kategorie. Füge unten einen hinzu oder nutze „Sender suchen“.":
    "No stations in this category yet. Add one below or use \"Find stations\".",
  "Sendername": "Station name", "Stream-URL (http:// oder https://)": "Stream URL (http:// or https://)",
  "Stream-URL": "Stream URL", "Hinzufügen": "Add", "Export": "Export", "Import": "Import",
  "Keine Kategorie": "No category", "Lege oben eine neue Kategorie an.": "Create a new category above.",
  "{n} Sender · erscheint am Marantz unter „My Stations“": "{n} stations · shown on the Marantz under \"My Stations\"",
  "Ziehen zum Sortieren oder auf eine Kategorie": "Drag to reorder or onto a category",
  "HTTPS über Relay · ": "HTTPS via relay · ", "Auf dem Marantz abspielen": "Play on the Marantz",
  "Weitere Aktionen": "More actions", "Im Browser vorhören": "Preview in browser", "Stream testen": "Test stream",
  "Nach oben": "Move up", "Nach unten": "Move down", "Bearbeiten": "Edit",
  "Gespeichert": "Saved", "Reihenfolge gespeichert": "Order saved",
  "Senderliste konnte nicht neu geladen werden: {msg}": "Could not reload the station list: {msg}",
  "„{name}“ gibt es dort schon": "\"{name}\" already exists there",
  "Nach „{cat}“ verschoben": "Moved to \"{cat}\"",
  "Anlegen": "Create", "Kategorie gibt es schon": "Category already exists", "Kategorie angelegt": "Category created",
  "Kategorie umbenennen": "Rename category", "Speichern": "Save", "Kategorie löschen?": "Delete category?",
  "<p>„{name}“ mit {n} Sendern wird gelöscht. Die vorige Fassung bleibt als <code>stations.yml.bak</code> erhalten.</p>":
    "<p>\"{name}\" with {n} stations will be deleted. The previous version is kept as <code>stations.yml.bak</code>.</p>",
  "Kategorie gelöscht": "Category deleted",
  "teste …": "testing …", "läuft": "running", "Fehler {n}": "Error {n}", "Fehler": "Error",
  "nicht erreichbar": "not reachable",
  "{name}: Stream erreichbar": "{name}: stream reachable", "{name}: Stream nicht erreichbar": "{name}: stream not reachable",
  "Sender löschen?": "Delete station?",
  "<p>„{name}“ wird aus „{cat}“ entfernt.</p>": "<p>\"{name}\" will be removed from \"{cat}\".</p>",
  "„{name}“ gelöscht": "\"{name}\" deleted", "Sender bearbeiten": "Edit station", "Kategorie": "Category",
  "Name gibt es in der Kategorie schon": "This name already exists in the category",
  "„{name}“ hinzugefügt": "\"{name}\" added",
  "Export (stations.yml)": "Export (stations.yml)", "Schließen": "Close",
  "Text markieren und kopieren, oder <a href=\"api/stations/export\" target=\"_blank\" rel=\"noopener\">als Datei öffnen</a>.":
    "Select and copy the text, or <a href=\"api/stations/export\" target=\"_blank\" rel=\"noopener\">open it as a file</a>.",
  "Import (YCast-Format)": "Import (YCast format)", "Kategorie:\n  Sendername: http://stream-url": "Category:\n  Station name: http://stream-url",
  "Modus": "Mode", "Ergänzen (vorhandene bleiben)": "Merge (keep existing)", "Ersetzen (alles neu)": "Replace (everything new)",
  "Importieren": "Import", "Import gespeichert": "Import saved",

  // ---------- Suchen ----------
  "Im Radio Browser suchen": "Search Radio Browser",
  "Name, z. B. Bayern 3, Antenne, Jazz …": "Name, e.g. FIP, BBC Radio 6, Jazz …",
  "Land": "Country", "Deutschland": "Germany", "Österreich": "Austria", "Schweiz": "Switzerland",
  "Alle Länder": "All countries", "Genre": "Genre", "Alle Genres": "All genres", "Sortierung": "Sort order",
  "Beliebteste zuerst": "Most popular first", "Name A–Z": "Name A–Z", "Beste Bitrate zuerst": "Best bitrate first",
  "Meiste Bewertungen": "Most votes", "Suchen": "Search",
  "Ergebnisse erscheinen beim Tippen. HLS- und Ogg-Sender wandelt YCast für den Marantz um.":
    "Results appear as you type. YCast converts HLS and Ogg stations for the Marantz.",
  "Sender manuell hinzufügen": "Add a station manually",
  "Für Sender, die der Radio Browser nicht kennt. Die Stream-URL steht meist auf der Website des Senders (oft mit .mp3, .aac oder /stream).":
    "For stations Radio Browser doesn't know. The stream URL is usually on the station's website (often ending in .mp3, .aac or /stream).",
  "Name": "Name", "z. B. Mein Lokalradio": "e.g. My local radio", "http://… oder https://…": "http://… or https://…",
  "Testen": "Test", "Vorhören": "Preview",
  "Häufig": "Common", "Alle Länder A–Z ({n})": "All countries A–Z ({n})", "Länderliste: {msg}": "Country list: {msg}",
  "Bitte eine URL mit http:// oder https:// eingeben.": "Please enter a URL starting with http:// or https://.",
  "Teste …": "Testing …", "Stream erreichbar: {info}": "Stream reachable: {info}",
  " · läuft am Marantz über das HTTPS-Relay": " · plays on the Marantz via the HTTPS relay",
  "Nicht erreichbar ({why}). Hinzufügen geht trotzdem.": "Not reachable ({why}). You can still add it.",
  "keine Daten": "no data", "Lege zuerst eine Kategorie an": "Create a category first",
  "URL muss mit http:// oder https:// beginnen": "URL must start with http:// or https://",
  "„{name}“ ist schon in „{cat}“": "\"{name}\" is already in \"{cat}\"",
  "„{name}“ zu „{cat}“ hinzugefügt": "\"{name}\" added to \"{cat}\"",
  "Suche …": "Searching …", "{n} Treffer": "{n} results", "Nichts gefunden": "Nothing found", "Nichts gefunden.": "Nothing found.",
  "Zu meinen Sendern": "Add to my stations",
  "Diesen Stream wandelt YCast für den Marantz um; der Start dauert ein paar Sekunden länger":
    "YCast converts this stream for the Marantz; starting takes a few seconds longer",
  "{what} · wird umgewandelt": "{what} · converted",
  "Sender": "Station", "Neue Kategorie …": "New category …", "Name der neuen Kategorie": "Name of the new category",
  "Bitte einen Namen für die neue Kategorie eingeben": "Please enter a name for the new category",

  // ---------- Vorhören ----------
  "Vorhören beenden": "Stop preview", "Diesen Sender auf dem Marantz abspielen": "Play this station on the Marantz",
  "Auf dem Marantz": "On the Marantz", "verbinde …": "connecting …",
  "Browser kann diesen Stream nicht abspielen": "The browser can't play this stream",
  "läuft im Browser": "playing in the browser", "Stream nicht abspielbar": "Stream not playable",
  "Abbrechen": "Cancel", "OK": "OK",

  // ---------- Einrichtung ----------
  "Für Fernbedienung und „Auf dem Marantz abspielen“. Die Adresse wird aus den Abrufen erkannt; trage sie nur ein, wenn sie nicht stimmt. Im Marantz muss „Netzwerk-Standby“ eingeschaltet sein, damit er sich aus dem Standby wecken lässt.":
    "For the remote control and \"Play on the Marantz\". The address is detected from the receiver's requests; only enter it if it is wrong. \"Network standby\" must be enabled on the Marantz so it can be woken from standby.",
  "IP des Marantz": "Marantz IP", "Automatisch": "Automatic", "automatisch": "automatic",
  "fest eingetragen": "set manually", "aus den Abrufen erkannt": "detected from requests",
  "Adresse": "Address", "Zustand": "State", "eingeschaltet": "on",
  "noch unbekannt: öffne einmal Internet Radio am Marantz oder trage die IP ein":
    "unknown yet: open Internet Radio on the Marantz once or enter the IP",
  "erkannt: {ip}": "detected: {ip}", "z. B. 192.168.178.56": "e.g. 192.168.178.56",
  "Marantz-Adresse gespeichert": "Marantz address saved", "Marantz wird automatisch erkannt": "Marantz will be detected automatically",
  "Senderdatenbank": "Station database",
  "Die Suche hier und die Menüs am Marantz (Land, Genre, Sprache, beliebte Sender) nutzen den freien":
    "The search here and the menus on the Marantz (country, genre, language, popular stations) use the free",
  "Community Radio Browser": "Community Radio Browser",
  ". Er läuft auf mehreren Servern; „Automatisch“ nimmt den nächsten, wenn einer nicht antwortet. Ein fester Server weicht nicht aus.":
    ". It runs on several servers; \"Automatic\" switches to the next one if a server doesn't respond. A fixed server has no fallback.",
  "Server": "Server", "fest: {s}": "fixed: {s}", " (aus RADIOBROWSER_API)": " (from RADIOBROWSER_API)",
  "Auswahl": "Choice", "Zuletzt benutzt": "Last used", "noch keine Abfrage seit dem Start": "no request since start",
  "Bekannte Server": "Known servers", "Radio Browser: fest {s}": "Radio Browser: fixed {s}",
  "Radio Browser: automatisch": "Radio Browser: automatic", "{s} antwortet · {n} Sender": "{s} responds · {n} stations",
  "Sprache dieser Oberfläche. Ohne Auswahl richtet sie sich nach dem Browser.":
    "Language of this interface. Without a choice it follows the browser.",
  "Eigener Server": "Custom server", "eigener": "custom", "Entfernen": "Remove",
  "Zum Beispiel ein selbst betriebener Radio-Browser-Spiegel, auch im Heimnetz mit Port (":
    "For example a self-hosted Radio Browser mirror, also in your home network with a port (",
  "). YCast prüft die Adresse beim Hinzufügen. Eigene Server lassen sich oben fest auswählen; bei „Automatisch“ werden sie zuerst gefragt.":
    "). YCast checks the address when adding it. Custom servers can be selected as fixed server above; with \"Automatic\" they are asked first.",
  "https://… oder http://IP:Port": "https://… or http://IP:port",
  "{s} hinzugefügt · {n} Sender": "{s} added · {n} stations", "{s} entfernt": "{s} removed",
  "Auf dem iPhone als App": "As an app on the iPhone",
  "In Safari diese Seite öffnen, unten auf": "Open this page in Safari, tap", "Teilen": "Share",
  "tippen und": "at the bottom and choose", "Zum Home-Bildschirm": "Add to Home Screen",
  "wählen. YCast startet dann im Vollbild mit eigenem Symbol. Beim ersten Start einmal mit dem Umbrel-Passwort anmelden.":
    ". YCast then starts full screen with its own icon. Sign in once with your Umbrel password on first launch.",
  "Dienst": "Service", "Receiver": "Receiver",
  "Der Receiver-Dienst braucht eine eigene, freie IP in deinem Heimnetz (Port 80). Wähle eine Adresse außerhalb des DHCP-Bereichs deines Routers. Adapter, Netz und Gateway erkennt die App beim Start selbst.":
    "The receiver service needs its own free IP in your home network (port 80). Pick an address outside your router's DHCP range. The app detects adapter, network and gateway on startup.",
  "IP für YCast": "IP for YCast", "Standard": "Default",
  "DNS über Pi-hole": "DNS via Pi-hole", "DNS prüfen": "Check DNS",
  "Der Marantz fragt": "The Marantz requests", "ab. Deine Pi-holes müssen diesen Namen auf": ". Your Pi-holes must redirect this name to",
  "umleiten.": ".",
  "Pi-hole 6 öffnen →": "Open Pi-hole 6 →",
  "Zwei Einträge anlegen, jeweils mit der IP": "Create two entries, each with the IP",
  "Auf jedem Pi-hole wiederholen.": "Repeat on every Pi-hole.",
  "Der Marantz muss einen Pi-hole als DNS nutzen (per DHCP der Fritzbox oder fest im Receiver). Danach den Marantz einmal komplett stromlos machen.":
    "The Marantz must use a Pi-hole as DNS (via your router's DHCP or set in the receiver). Then unplug the Marantz from power once.",
  "Alternative: alle": "Alternative: redirect all", "umleiten (nicht über die Weboberfläche möglich)": "(not possible via the web interface)",
  "Pi-hole direkt installiert, per SSH:": "Pi-hole installed directly, via SSH:", "Kopieren": "Copy",
  "Pi-hole im Docker-Container, als Umgebungsvariable (danach Container neu starten):":
    "Pi-hole in a Docker container, as an environment variable (restart the container afterwards):",
  "Pi-hole 1 (IP)": "Pi-hole 1 (IP)", "z. B. 192.168.178.2": "e.g. 192.168.178.2", "Pi-hole 2 (IP)": "Pi-hole 2 (IP)",
  "optional": "optional", "Letzte Abrufe": "Recent requests", "Zeit": "Time", "Gerät": "Device", "Anfrage": "Request",
  "getrennt": "disconnected", "läuft auf {ip}": "running on {ip}", "gesehen {ago}": "seen {ago}", "noch nie gesehen": "never seen",
  "vTuner-Dienst": "vTuner service", "nicht erreichbar ({e})": "not reachable ({e})", "LAN-IP": "LAN IP",
  "nicht gesetzt": "not set", "„My Stations“": "\"My Stations\"", "{c} Kategorien · {s} Sender": "{c} categories · {s} stations",
  "HTTPS-Relay": "HTTPS relay", "an: HTTPS-Streams laufen über YCast": "on: HTTPS streams go through YCast", "aus": "off",
  "Zuletzt gesehen": "Last seen", "Angefragt als": "Requested as", "Kennung": "Identifier", "Status": "Status",
  "Noch kein Abruf. Sobald der Marantz „Internet Radio“ öffnet und DNS stimmt, erscheint er hier.":
    "No request yet. As soon as the Marantz opens \"Internet Radio\" and DNS is set up, it shows up here.",
  "Noch keine Abrufe.": "No requests yet.",
  "Aktive IP": "Active IP", " (Standard)": " (default)", "Netz": "Network", "Adapter": "Adapter",
  "Gespeichert: <strong>{ip}</strong>. Gilt nach einem Neustart der App (Umbrel-Dashboard: YCast-Symbol gedrückt halten bzw. Rechtsklick → <em>Neu starten</em>). Danach die Local DNS Records in den Pi-holes auf {ip} ändern.":
    "Saved: <strong>{ip}</strong>. Takes effect after restarting the app (Umbrel dashboard: long-press or right-click the YCast icon → <em>Restart</em>). Then change the Local DNS Records in your Pi-holes to {ip}.",
  "Standard: {ip}": "Default: {ip}", "z. B. 192.168.178.210": "e.g. 192.168.178.210",
  "IP gespeichert, Neustart der App nötig": "IP saved, app restart required", "Standard-IP gespeichert": "Default IP saved",
  "Pi-holes gespeichert": "Pi-holes saved", "Prüfe …": "Checking …",
  "Trage oben mindestens einen Pi-hole ein und speichere.": "Enter at least one Pi-hole above and save.",
  "falsch": "wrong", "über": "via", "keine Antwort": "no answer", "Kopiert": "Copied", "Bitte manuell kopieren": "Please copy manually",

  // ---------- Zweite Oberfläche (/neu) ----------
  "Neue Oberfläche ausprobieren": "Try the new interface",
  "– für das Handy gedacht: Senderliste oben, Wiedergabe unten in Daumenreichweite. Bearbeiten und Einrichtung bleiben hier.":
    "– made for phones: station list at the top, playback at the bottom within thumb reach. Editing and setup stay here.",
  "Zuletzt": "Recent", "spielt": "playing", "Bearbeiten und Einrichtung": "Edit and setup", "Senderskala": "Station dial",
  "Wiedergabe": "Playback", "Ausschalt-Timer": "Sleep timer", "Ausschalt-Timer: {n} min": "Sleep timer: {n} min",
  "Ausschalt-Timer: antippen schaltet weiter": "Sleep timer: tap to cycle", "Starte …": "Starting …",
  "{n} kbit/s": "{n} kbit/s", "Fertig": "Done", "Zu welcher Kategorie?": "Add to which category?",
  "Sendername, z. B. FIP …": "Station name, e.g. FIP …",
  "Tippe einen Namen. Gesucht wird im freien Radio Browser.": "Type a name. The search uses the free Radio Browser.",
  "Suche läuft …": "Searching …", "Nichts gefunden für „{q}“.": "Nothing found for \"{q}\".",
  "Noch nichts gespielt.": "Nothing played yet.", "Diese Kategorie ist leer.": "This category is empty.",
  "Noch keine Sender. Suche oben nach einem Sender oder lege Kategorien in der klassischen Ansicht an.":
    "No stations yet. Search for a station above, or create categories in the classic view.",
  "Tippe auf einen Sender, der Marantz schaltet sich dann ein.": "Tap a station and the Marantz will switch on.",
  "Favoriten": "Favorites",

  // ---------- Zeit ----------
  "gerade eben": "just now", "vor {n} min": "{n} min ago", "vor {n} h": "{n} h ago",

  // ---------- Meldungen vom Server (häufigste) ----------
  "Kein Marantz bekannt. IP unter „Einrichtung“ eintragen.": "No Marantz known. Enter its IP under \"Setup\".",
  "Ungültige Stream-URL": "Invalid stream URL",
  "Dieses Stream-Format (Ogg/Opus/HLS) kann der Marantz nicht abspielen": "The Marantz can't play this stream format (Ogg/Opus/HLS)",
  "Der Marantz nimmt dieses Stream-Format nicht an": "The Marantz doesn't accept this stream format",
  "Der Marantz ist nicht rechtzeitig eingeschaltet (Netzwerk-Standby aktiv?)": "The Marantz didn't switch on in time (is network standby enabled?)",
  "Der Marantz bietet keine UPnP-Wiedergabe (AVTransport) an": "The Marantz doesn't offer UPnP playback (AVTransport)",
  "HLS-Sender brauchen ffmpeg, das in diesem Image fehlt": "HLS stations need ffmpeg, which is missing in this image",
  "Dieser HLS-Stream lässt sich nicht umwandeln (nicht erreichbar oder ohne Audio)": "This HLS stream can't be converted (not reachable or no audio)",
  "Dieser Stream lässt sich nicht umwandeln (nicht erreichbar oder ohne Audio)": "This stream can't be converted (not reachable or no audio)",
  "Diesen Server zuerst unter „Eigener Server“ hinzufügen": "Add this server under \"Custom server\" first",
  "Bitte eine Adresse eingeben": "Please enter an address",
  "Lautstärke muss eine Zahl sein": "Volume must be a number",
};

// Server-Meldungen mit eingesetzten Werten
const EN_PATTERNS = [
  [/^Marantz unter (.+) nicht erreichbar \((.+)\)$/, "Marantz at $1 not reachable ($2)"],
  [/^Radio Browser nicht erreichbar \((.+)\)$/, "Radio Browser not reachable ($1)"],
  [/^Marantz lehnt (\w+) ab \((.+)\)$/, "Marantz rejected $1 ($2)"],
  [/^UPnP (\w+) fehlgeschlagen \((.+)\)$/, "UPnP $1 failed ($2)"],
  [/^(.+) antwortet nicht wie ein Radio Browser( \(.+\))?$/, "$1 doesn't respond like a Radio Browser$2"],
  [/^(.+) ist schon in der Liste$/, "$1 is already in the list"],
  [/^Höchstens (\d+) eigene Server$/, "At most $1 custom servers"],
  [/^„(.*)“ ist keine gültige Adresse \(http:\/\/ oder https:\/\/, ohne \? und #\)$/, "\"$1\" is not a valid address (http:// or https://, without ? and #)"],
  [/^„(.*)“ ist keine gültige Adresse$/, "\"$1\" is not a valid address"],
];

function t(s, vars) {
  let out = s;
  if (LANG === "en") {
    if (Object.prototype.hasOwnProperty.call(EN, s)) out = EN[s];
    else for (const [re, rep] of EN_PATTERNS) if (re.test(s)) { out = s.replace(re, rep); break; }
  }
  return vars ? out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : out;
}

function translateDom(root = document.body) {
  if (LANG === "de") return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement && n.parentElement.closest("script, style, code, textarea")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const key = n.nodeValue.replace(/\s+/g, " ").trim();
    if (key && Object.prototype.hasOwnProperty.call(EN, key)) {
      const lead = /^\s/.test(n.nodeValue) ? " " : "", trail = /\s$/.test(n.nodeValue) ? " " : "";
      const en = EN[key];
      // Satzzeichen am Anfang (z. B. „. Er läuft …“) schließen ohne Leerzeichen an
      n.nodeValue = (/^[.,;:)]/.test(en) ? "" : lead) + en + trail;
    }
  }
  root.querySelectorAll("[placeholder], [title], [aria-label], optgroup[label]").forEach((el) => {
    for (const a of ["placeholder", "title", "aria-label", "label"]) {
      const v = el.getAttribute(a);
      if (v && Object.prototype.hasOwnProperty.call(EN, v)) el.setAttribute(a, EN[v]);
    }
  });
}

translateDom();
document.querySelectorAll("[data-lang-set]").forEach((b) => {
  b.setAttribute("aria-pressed", String(b.dataset.langSet === LANG));
  b.addEventListener("click", () => {
    if (b.dataset.langSet === LANG) return;
    try { localStorage.setItem("ycast-lang", b.dataset.langSet); } catch { /* privat */ }
    location.reload();
  });
});
