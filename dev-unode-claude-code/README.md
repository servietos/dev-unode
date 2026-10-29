# Claude Code für servietos/dev-unode

Diesen Ordner **unverändert** ins Repo legen:

https://github.com/servietos/dev-unode

Zielpfad im Repo:

```
dev-unode-claude-code/
  umbrel-app.yml
  docker-compose.yml
  entrypoint.sh
```

Danach in Umbrel:

App Store → ⋯ → Community App Stores → Add:

```
https://github.com/servietos/dev-unode
```

Store öffnen → Claude Code installieren.

Erststart dauert einige Minuten. Danach `/login` im Terminal oder `data/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Ab Version 1.1.0

- **Python:** `python3` (mit venv/pip) und `uv` sind vorhanden. `uv` und
  damit installierte Python-Versionen liegen persistent in `data/.local`.
- **Cron:** Jobs in `data/crontab` eintragen, dann App neu starten.
  Jeden Job über `/data/bin/with-env` starten, damit PATH, HOME und
  `data/.env` geladen sind. Logs z. B. nach `/data/logs/`.
- **Zeitzone:** `TZ=Europe/Berlin` (Standard) in `data/.env` anpassbar.
- **Terminal:** Wer Claude beendet, landet in einer Shell; `claude` startet neu.
