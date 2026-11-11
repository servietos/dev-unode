# YCast für Umbrel

vTuner-Ersatz für AV-Receiver und Netzwerk-Player mit eigener Weboberfläche, als App für
[umbrelOS](https://umbrel.com) im Community App Store
[`servietos/dev-unode`](https://github.com/servietos/dev-unode). Getestet mit einem Marantz M-CR510.

Basis ist [YCast 1.1.0 von milaq](https://github.com/milaq/YCast) (PyPI), ergänzt um:

- **Weboberfläche** (Umbrel-Dashboard): Kategorien und Sender verwalten, Drag & Drop,
  vorhören im Browser, Stream-Test, Suche im Community Radio Browser, Import/Export der
  `stations.yml`, Status, Abruf-Log des Receivers, DNS-Prüfung, Netzwerk-Einstellung.
- **Fernbedienung** für Denon/Marantz-Netzwerkgeräte (getestet M-CR510): Ein/Standby, Lautstärke,
  Stumm, Display mit dem laufenden Titel und „Abspielen“ bei jedem Sender. Status und Befehle über
  die Webschnittstelle des Geräts (`/goform/…`, bewusst nicht Telnet, damit die Hersteller-App frei
  bleibt), Abspielen per UPnP AVTransport. Die Geräte-IP wird aus den Abrufen erkannt oder fest
  eingetragen; im Gerät muss Netzwerk-Standby aktiv sein.
- **iPhone/Android**: Web-App-Manifest und Symbole, „Zum Home-Bildschirm“ startet im Vollbild.
- **HLS und Ogg/Opus**: `/hls?url=…&sig=…` im vTuner-Dienst wandelt mit ffmpeg um (AAC → ADTS und
  MP3 werden nur umverpackt, sonst MP3 192 kbit/s). Vorher lädt YCast die Playlist selbst und prüft,
  dass alle Adressen darin öffentlich sind (kein Zugriff aufs Heimnetz über fremde Playlisten).
  Gleiche Plätze-Begrenzung wie das Relay, ffmpeg bricht nach 15 s ohne Daten ab.
- **Komfort**: „Zuletzt gespielt“ (`data/recent.json`) und Ausschalt-Timer (Standby nach Ablauf).
- **HTTPS-Relay**: Receiver können kein HTTPS. YCast schreibt `https://` normalerweise auf
  `http://` um, was bei vielen Sendern scheitert. Hier laufen HTTPS-Streams stattdessen über
  `http://<YCast-IP>/relay?url=…` (abschaltbar mit `RELAY_HTTPS=off`).

## Passt das für mich?

Du brauchst:

1. **Einen Receiver mit vTuner-Internetradio**, meist Baujahr 2011–2017
   (Marantz, Denon, Yamaha, Onkyo, Pioneer, Grundig …).
2. **Einen Umbrel mit kabelgebundenem Netzwerk.** Der Receiver-Dienst bekommt per macvlan eine
   eigene IP; das klappt über WLAN-Adapter meist nicht.
3. **Eine freie IP im Heimnetz** außerhalb des DHCP-Bereichs des Routers. Standard ist
   `<dein Netz>.210`, also z. B. `192.168.178.210` bei einer Fritzbox.
4. **Einen DNS-Server, in dem du eigene Einträge anlegen kannst**, z. B. Pi-hole, AdGuard Home
   oder ein Router mit lokalen DNS-Einträgen, und den der Receiver nutzt.

## Einrichtung

1. App „YCast“ aus dem Community App Store installieren und öffnen.
2. **Status & Einrichtung → Netzwerk**: Die aktive IP prüfen und bei Bedarf ändern. Eine neue IP
   gilt nach einem Neustart der App im Umbrel-Dashboard.
3. Im DNS-Server den vTuner-Namen des Receivers auf diese IP zeigen lassen:

   | Hersteller | Namen |
   |---|---|
   | Denon, Marantz | `denon.vtuner.com`, `denon2.vtuner.com` |
   | Yamaha | `radioyamaha.vtuner.com`, `radioyamaha2.vtuner.com` |
   | Onkyo | `onkyo.vtuner.com`, `onkyo2.vtuner.com` |
   | Grundig | `grundig.vtuner.com`, `grundig.radiosetup.com` |

   **Pi-hole 6:** *Settings → Local DNS Records*. Alle `*.vtuner.com` auf einmal geht nur über
   `misc.dnsmasq_lines`, und das lässt sich in Pi-hole 6 nicht über die Weboberfläche setzen:

   ```
   # direkt installiert
   sudo pihole-FTL --config misc.dnsmasq_lines '["address=/vtuner.com/<YCast-IP>"]'
   # Docker (Umgebungsvariable, danach Container neu starten)
   FTLCONF_misc_dnsmasq_lines: 'address=/vtuner.com/<YCast-IP>'
   ```

4. Pi-hole-IPs in der App eintragen und **DNS prüfen** klicken.
5. Receiver einmal stromlos machen, dann Internetradio öffnen. Unter „Status & Einrichtung“
   erscheint er als „Receiver gesehen“.

## Aufbau

| Dienst | Modus | Port | Netz |
|---|---|---|---|
| `web` | `YCASTUI_MODE=web` | 8000 | Umbrel (`app_proxy`, App-Port 3848) |
| `ycast` | `YCASTUI_MODE=vtuner` | 80 | Umbrel + macvlan mit eigener LAN-IP |

Der Receiver fragt den vTuner-Namen auf **Port 80** ab. Port 80 des Umbrel belegt das
Dashboard, deshalb bekommt der vTuner-Dienst per macvlan eine eigene IP. Der Umbrel-Host selbst
erreicht macvlan-IPs nicht, andere Geräte im LAN schon.

**Netzwerk beim Start:** Umbrel führt vor jedem Start `dev-unode-ycast/exports.sh` auf dem Host
aus. Das Skript erkennt Adapter, Netz und Gateway über die Standardroute, liest die in der
Oberfläche gespeicherte IP aus `data/network.conf` und prüft sie (im Netz, nicht Netz-/Broadcast-,
Router- oder Umbrel-Adresse). Bei ungültigem Wert startet die App mit `<Netz>.210` und zeigt
den Grund in der Oberfläche an, damit sie immer erreichbar bleibt. Die Werte gehen als
`APP_YCAST_*` an `docker-compose.yml`.

Beide Dienste teilen sich `/data`:

- `stations.yml`: Senderliste im YCast-Format (`Kategorie → Name → URL`), vorige Fassung als `.bak`
- `settings.json`: IPs der DNS-Server für die DNS-Prüfung
- `network.conf`: gewünschte IP des Receiver-Dienstes (`YCAST_IP=…`), fehlt = Standard
- `requests.log`: letzte Abrufe (JSON-Zeilen)

## Entwicklung

```
pip install -r requirements.txt
python -m unittest discover -s tests -t .
YCAST_DATA_DIR=./data PORT=8000 python -m ycastui web
YCAST_DATA_DIR=./data PORT=8080 YCAST_IP=127.0.0.1:8080 python -m ycastui vtuner
```

Neue Version: `VERSION` erhöhen, Image-Tag in `dev-unode-ycast/docker-compose.yml` und
`version` in `dev-unode-ycast/umbrel-app.yml` anpassen. Der Workflow
`.github/workflows/ycast-image.yml` baut `ghcr.io/servietos/ycast` (amd64 + arm64).
