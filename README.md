## Servietos Community App Store for  Umbrel

This repository is created based on template at https://github.com/getumbrel/umbrel-community-app-store.

Contained apps (alle mit Weboberfläche über das Umbrel-Dashboard, x86 und ARM):

| App | Zweck | Version |
|---|---|---|
| broking | Aktien-Screen, Thesen-Journal, Handel über Alpaca Paper-Trading (Quellcode in `src/broking`) | 0.2.0 |
| chromium | Browser im Browser (linuxserver/chromium) | 7b8e918f-ls63 |
| claude-code | Claude Code im Browser-Terminal | 1.1.2 |
| crafty | Crafty Controller: Minecraft-Server (Java & Bedrock) anlegen und verwalten | 4.11.0 |
| freshrss | RSS-Reader | 1.30.4 |
| goldcoin-core | Goldcoin-Fullnode mit Statusseite | 0.1.4 |
| hello-world | Beispiel-App aus der Umbrel-Vorlage | 1.0.0 |
| myspeed | Speedtest-Verlauf | 1.0.9 |
| openclaude | OpenClaude im Browser-Terminal | 1.0.1 |
| owncast | Livestream-Server (Zuschauerseite ohne Umbrel-Login, RTMP auf Port 1935) | 0.2.1-1 |
| pi-hole | DNS-Server und Werbeblocker (Weboberfläche auf Port 8082) | 2025.05.1 |
| smart-tv-telegram | Telegram-Videos per Bot auf Smart-TV/Chromecast, mit Einrichtungsseite | 1.4.0-1.3 |
| ycast | Internetradio für AV-Receiver mit vTuner (Marantz, Denon, Yamaha, Onkyo …), Fernbedienung, HLS/Ogg-Umwandlung, HTTPS-Relay, einstellbare IP (Anleitung in `src/ycast/README.md`) | 3.1.3 |

Hinweis: Apps mit Spiele- oder Streaming-Ports (crafty, owncast) brauchen für Zugriffe von außen
eine Portfreigabe im Router; pi-hole kollidiert mit dem offiziellen Pi-hole aus dem Umbrel-Store.

Hinweis für Entwickler: Umbrel kopiert bei App-Updates nur `docker-compose.yml`, `umbrel-app.yml`, `exports.sh`,
`*.template` und `hooks/`. Skripte, die mit Updates mitkommen sollen, liegen deshalb als `configs`-Inhalt in der
Compose-Datei; Quelle bleibt die Datei im App-Ordner, nach Änderungen `python tools/embed.py` ausführen.

## How to use (description from Umbrel's template):

1. Start by clicking the "Use this template" button located above.
2. Assign an ID and name to your app store within the `umbrel-app-store.yml` file. This file specifies two important attributes:
    - `id` - Acts as a unique prefix for every app within your Community App Store. You must start your application's ID with your app store's ID. For instance, in this template, the app store ID is `sparkles`, and there's an app named `hello world`. Consequently, the app's ID should be: `sparkles-hello-world`.
    - `name` - This is the name of the Community App Store displayed in the umbrelOS UI.
3. Change the name of the `sparkles-hello-world` folder to match your app's ID. The app ID is for you to decide. For example, if your app store ID is `whistles`, and your app is named My Video Downloader, you could set its app ID to `whistles-my-video-downloader`, and rename the folder accordingly.
4. Next, enter your app's listing details in the `whistles-my-video-downloader/umbrel-app.yml`. These are displayed in the umbrelOS UI.
5. Include the necessary Docker services in `whistles-my-video-downloader/docker-compose.yml`.
6. That's it! Your Community App Store, featuring your unique app, is now set up and ready to go. To use your Community App Store, you can add its GitHub url the umbrelOS user interface.
