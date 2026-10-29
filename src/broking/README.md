# broking

Screening, Thesen-Journal und Risiko-Check für Paper-Trading – als Python-CLIs (`tools/`)
und als Weboberfläche (`web/`), die auch als Umbrel-App läuft.

## Lokal

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r web/requirements.txt
.venv/bin/uvicorn web.app:app --port 8000      # → http://localhost:8000
python -m unittest discover -s tests -t .      # Tests
```

Windows: `.venv\Scripts\...` statt `.venv/bin/...`.

## Docker / Umbrel

Jeder Push, der `src/broking/` ändert, baut über `.github/workflows/broking-image.yml` das Image
`ghcr.io/servietos/broking:<VERSION>` für amd64 und arm64. Die Umbrel-App liegt in
`dev-unode-broking/` im selben Repo.

Neue Version: `VERSION` erhöhen, denselben Tag in `dev-unode-broking/docker-compose.yml` und
`version` in `dev-unode-broking/umbrel-app.yml` eintragen.

Daten im Container: `/data` (`config.json`, `prices/`, `screens/`, `journal/`).

Die Oberfläche handelt nicht: Es gibt keinen Order-Endpunkt, `paper_trading` ist fest aktiv.
