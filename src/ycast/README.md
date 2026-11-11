# YCast für Umbrel

vTuner-Ersatz für AV-Receiver (getestet für Marantz M-CR510) mit eigener Weboberfläche.
Basis ist [YCast 1.1.0 von milaq](https://github.com/milaq/YCast) (PyPI), ergänzt um:

- **Weboberfläche** (Umbrel-Dashboard): Kategorien und Sender verwalten, Drag & Drop,
  vorhören im Browser, Stream-Test, Suche im Community Radio Browser, Import/Export der
  `stations.yml`, Status, Abruf-Log des Receivers, DNS-Prüfung der Pi-holes.
- **HTTPS-Relay**: Receiver können kein HTTPS. YCast schreibt `https://` normalerweise auf
  `http://` um, was bei vielen Sendern scheitert. Hier laufen HTTPS-Streams stattdessen über
  `http://<YCAST_IP>/relay?url=…` (abschaltbar mit `RELAY_HTTPS=off`).

## Aufbau

| Dienst | Modus | Port | Netz |
|---|---|---|---|
| `web` | `YCASTUI_MODE=web` | 8000 | Umbrel (`app_proxy`, App-Port 3848) |
| `ycast` | `YCASTUI_MODE=vtuner` | 80 | Umbrel + macvlan mit eigener LAN-IP |

Der Receiver fragt `denon.vtuner.com` auf **Port 80** ab. Port 80 des Umbrel belegt das
Dashboard, deshalb bekommt der vTuner-Dienst per macvlan eine eigene IP
(Standard `192.168.178.210` auf `enp1s0`; in `dev-unode-ycast/docker-compose.yml` anpassen).
Hinweis: Der Umbrel-Host selbst erreicht macvlan-IPs nicht, andere Geräte im LAN schon.

Beide Dienste teilen sich `/data`:

- `stations.yml`: Senderliste im YCast-Format (`Kategorie → Name → URL`), vorige Fassung als `.bak`
- `settings.json`: IPs der Pi-holes für die DNS-Prüfung
- `requests.log`: letzte Abrufe (JSON-Zeilen)

## Pi-hole 6

*Settings → Local DNS Records*, auf allen Pi-holes:

| Domain | IP |
|---|---|
| `denon.vtuner.com` | `192.168.178.210` |
| `denon2.vtuner.com` | `192.168.178.210` |

Der Receiver muss einen Pi-hole als DNS nutzen und danach einmal stromlos gemacht werden.

Alternative für alle `*.vtuner.com`: `misc.dnsmasq_lines` lässt sich in Pi-hole 6 nicht über
die Weboberfläche setzen, nur so:

```
# direkt installiert
sudo pihole-FTL --config misc.dnsmasq_lines '["address=/vtuner.com/192.168.178.210"]'
# Docker (Umgebungsvariable, danach Container neu starten)
FTLCONF_misc_dnsmasq_lines: 'address=/vtuner.com/192.168.178.210'
```

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
