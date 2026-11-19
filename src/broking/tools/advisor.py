"""Claude bewertet die Screen-Kandidaten (Morgenlauf) und schlägt bis zu 3 Satellit-Käufe vor.

    python tools/advisor.py run      # braucht Token in data/secrets.json und den claude-CLI

Ablauf:
1. Python sammelt die Fakten: Top-Kandidaten aus dem letzten Screen, je Titel den Ticket-Vorschlag
   (letzter Kurs, Stop/Ziel aus ATR, Stückzahl nach Limits), Depot, Aufteilung, offene Thesen.
2. Claude (claude -p, ohne Werkzeuge) wählt aus und begründet – rechnet aber nichts selbst.
3. Python prüft jeden Vorschlag (Titel aus der Liste, Stop < Kurs < Ziel, freie Plätze),
   legt ihn als These an (Modell = z. B. "sonnet") und speichert den Bericht in data/reports/.
Standard: Claude handelt nicht, Freigabe durch Timo im Order-Ticket der Weboberfläche.
Optional (`advisor.auto_trade`, Schalter in den Einstellungen, standardmäßig aus): Python legt jeden
geprüften Vorschlag als Paper-Order an – über dieselbe Strecke wie ein manuelles Ticket (`trading.place`:
These vor Order, risk_check, Kurs-Aktualität, Bracket mit Stop und Ziel). Claude selbst führt nie etwas aus;
lehnt eine Prüfung ab, wird nichts umgebaut, die Ablehnung steht im Bericht.

Das Token (claude setup-token) liegt in data/secrets.json und verlässt die App nur als
Umgebungsvariable CLAUDE_CODE_OAUTH_TOKEN für den claude-Prozess.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import journal
import risk_check
import trading
from broker import BrokerError, PaperBroker
from common import DATA_DIR, ROOT, SCREENS_DIR, emit, load_config

SECRETS_PATH = DATA_DIR / "secrets.json"
REPORTS_DIR = DATA_DIR / "reports"
MODELS = ("sonnet", "opus", "haiku")
DEFAULTS = {"enabled": False, "model": "sonnet", "top_n": 10, "auto_trade": False}
TOKEN_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,400}$")
HORIZON_MIN, HORIZON_MAX = 10, 56   # Swing 2–8 Wochen
TIMEOUT_S = 300


class AdvisorError(Exception):
    pass


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("advisor") or {})}


# --- Token ------------------------------------------------------------------------------

def load_token(path: Path = SECRETS_PATH) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("claude_oauth_token") or None
    except (OSError, ValueError):
        return None


def save_token(token: str, path: Path = SECRETS_PATH) -> None:
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise ValueError("Das sieht nicht nach einem Token aus `claude setup-token` aus (beginnt mit sk-ant-)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"claude_oauth_token": token}), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows
    tmp.replace(path)


def delete_token(path: Path = SECRETS_PATH) -> None:
    path.unlink(missing_ok=True)


def claude_bin() -> str | None:
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude")


# --- Fakten sammeln ---------------------------------------------------------------------

def free_slots(cfg: dict, client: PaperBroker) -> int:
    core = risk_check.core_tickers(cfg)
    held = {p["symbol"] for p in client.positions() if p["symbol"] not in core}
    pending = {o["symbol"] for o in client.orders("open", 500) if o.get("side") == "buy" and o["symbol"] not in core}
    return max(0, min(3, int(cfg["risk"]["max_open_positions"])) - len(held | pending))


def gather(cfg: dict, client: PaperBroker) -> dict:
    path = SCREENS_DIR / "latest.json"
    if not path.exists():
        raise AdvisorError("Noch kein Screen – erst Kurse laden und Screen rechnen")
    screen = json.loads(path.read_text(encoding="utf-8"))
    open_theses = [t for t in journal.load_theses() if t["status"] == "open"]
    busy = {t["ticker"] for t in open_theses}
    positions = client.positions()
    busy |= {p["symbol"] for p in positions}
    cands = []
    for c in screen.get("candidates", [])[: settings(cfg)["top_n"]]:
        if c["ticker"] in busy:
            continue
        k = trading.ticket(client, c["ticker"], cfg)
        if k["stale"] or not k["tradable"] or k["core"]:
            continue
        cands.append({
            "ticker": c["ticker"], "currency": k["currency"], "price": k["price"], "price_date": k["price_date"],
            "atr": k["atr"], "stop_atr": k["stop"], "target_atr": k["target"], "qty_by_limits": k["qty"],
            "value_eur": k["value_eur"],
            **{m: c.get(m) for m in ("score", "rsi14", "sma50", "sma200", "mom20_pct", "mom60_pct",
                                     "atr_pct", "vol_ratio", "dd_252_pct")},
        })
    a = client.account()
    return {
        "date": client.today(), "screen_created": screen.get("created"),
        "free_slots": free_slots(cfg, client),
        "account": {k: a.get(k) for k in ("equity", "cash", "start_cash", "high_water")},
        "positions": [{k: p.get(k) for k in ("symbol", "qty", "market_value", "unrealized_plpc", "sleeve")}
                      for p in positions],
        "open_theses": [{k: t.get(k) for k in ("ticker", "entry", "target", "stop", "deadline", "model")}
                        for t in open_theses],
        "recent_results": [{"ticker": t["ticker"], "status": t["status"], "model": t.get("model")}
                           for t in journal.load_theses() if t["status"] in ("hit", "miss")][-8:],
        "candidates": cands,
    }


PROMPT = """Du bist der Analyst des Paper-Depots „broking“ (Core-Satellite, EUR). Heute ist {date}.
Unten stehen die Fakten als JSON. Alle Zahlen sind von Python berechnet – rechne nichts selbst nach.

Aufgabe: Wähle aus `candidates` höchstens {slots} Satellit-Käufe (Swing, 2–8 Wochen) aus – oder keinen.
Regeln:
- Nur Titel aus `candidates`. Kein Day-Trading, keine Leerverkäufe.
- Nimm `stop_atr` und `target_atr` als Stop und Ziel. Weiche nur ab, wenn du eine konkrete Marke nennst
  (z. B. SMA50); dann muss gelten Stop < price < Ziel.
- `horizon_days` zwischen {hmin} und {hmax}.
- Begründung: ein Satz, prüfbar (Trend, Momentum, RSI, Abstand zu SMA). Nicht „sieht stark aus“.
- Überzeugt nichts, ist eine leere Liste die richtige Antwort.

Bewertungsraster (je Kandidat, nur mit den gelieferten Zahlen):
1. Trend: price über sma50 und sma50 über sma200 = intakt; darunter ablehnen.
2. Momentum: mom20_pct und mom60_pct positiv und nicht überhitzt; RSI 50–70 bevorzugt, über 75 meist zu spät.
3. Dehnung: Liegt price weit (> ca. 15 %) über sma50, ist der Einstieg riskant – eher auslassen.
4. Handelbarkeit: vol_ratio ≥ 1 spricht für Beteiligung; sehr hohe atr_pct (> 6 %) heißt breite Stopps, kleine Position.
5. Chance/Risiko: Ziel − Kurs mindestens doppelt so groß wie Kurs − Stop.
6. Streuung: Nicht zwei fast gleiche Wetten (gleiche Branche, gleiche Richtung) in einem Lauf.
7. Lernen: `recent_results` zeigt letzte Treffer und Fehlschläge – wiederhole kein Muster, das zuletzt scheiterte.
Du siehst keine Nachrichten und keine Termine: Quartalszahlen und Ereignisse kennst du nicht. Weise im `summary`
darauf hin, wenn das für einen Vorschlag relevant sein könnte.
- `summary`: höchstens 4 kurze Sätze zu Depot, Markt und Entscheidung, auf Deutsch.

Antworte NUR mit einem JSON-Objekt in genau dieser Form, ohne weiteren Text:
{{"summary": "…", "proposals": [{{"ticker": "…", "stop": 0.0, "target": 0.0, "horizon_days": 21,
"rationale": "…"}}], "skipped": [{{"ticker": "…", "reason": "…"}}]}}

Fakten:
{facts}
"""


def build_prompt(facts: dict) -> str:
    return PROMPT.format(date=facts["date"], slots=facts["free_slots"], hmin=HORIZON_MIN, hmax=HORIZON_MAX,
                         facts=json.dumps(facts, ensure_ascii=False, indent=1))


# --- Claude aufrufen ---------------------------------------------------------------------

def call_claude(prompt: str, token: str, model: str, runner=subprocess.run) -> dict:
    exe = claude_bin()
    if not exe:
        raise AdvisorError("claude-CLI nicht gefunden (im Container eingebaut, lokal: npm i -g @anthropic-ai/claude-code)")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env.update(CLAUDE_CODE_OAUTH_TOKEN=token, DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1",
               DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DO_NOT_TRACK="1")
    cmd = [exe, "-p", "--output-format", "json", "--model", model, "--max-turns", "1",
           "--disallowedTools", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
    try:
        proc = runner(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                      timeout=TIMEOUT_S, env=env, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise AdvisorError(f"Claude hat nach {TIMEOUT_S} s nicht geantwortet")
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout or "").strip().replace(token, "***")
        raise AdvisorError(f"claude-CLI fehlgeschlagen (Exit {proc.returncode}): {detail[:300]}")
    if out.get("is_error"):
        raise AdvisorError(f"Claude meldet einen Fehler: {str(out.get('result'))[:300]}")
    return out


def parse_answer(text: str) -> dict:
    """JSON aus der Antwort holen – auch wenn Claude es in ```json … ``` einpackt."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError:
        raise AdvisorError(f"Antwort ist kein JSON: {text[:200]}")
    if not isinstance(data, dict):
        raise AdvisorError("Antwort ist kein JSON-Objekt")
    return data


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def vet(data: dict, facts: dict, cfg: dict, client: PaperBroker) -> tuple[list[dict], list[dict]]:
    """Vorschläge gegen die Fakten prüfen. Gibt (gültige, verworfene) zurück."""
    by_ticker = {c["ticker"]: c for c in facts["candidates"]}
    ok, rejected = [], []
    props = data.get("proposals") if isinstance(data.get("proposals"), list) else []
    for p in props:
        if not isinstance(p, dict):
            continue
        t = str(p.get("ticker", "")).upper()
        c = by_ticker.get(t)
        reason = None
        stop = _num(p.get("stop")) or (c and c["stop_atr"])
        target = _num(p.get("target")) or (c and c["target_atr"])
        horizon = p.get("horizon_days")
        rationale = str(p.get("rationale") or "").strip()[:300]
        if c is None:
            reason = "nicht in der Kandidatenliste"
        elif any(x["ticker"] == t for x in ok):
            reason = "doppelt"
        elif len(ok) >= facts["free_slots"]:
            reason = "keine freien Satellit-Plätze"
        elif not (stop and target and stop < c["price"] < target):
            reason = "Stop < Kurs < Ziel verletzt"
        elif target > c["price"] * trading.MAX_TARGET_MULT:
            reason = "Ziel unrealistisch hoch"
        elif not isinstance(horizon, int) or not HORIZON_MIN <= horizon <= HORIZON_MAX:
            reason = f"Frist nicht {HORIZON_MIN}–{HORIZON_MAX} Tage"
        elif not rationale:
            reason = "Begründung fehlt"
        if reason:
            rejected.append({"ticker": t or "?", "reason": reason})
            continue
        stop, target = trading.round_px(stop), trading.round_px(target)
        rate = trading.fx_of(t)
        qty = risk_check.max_qty({"ticker": t, "price": c["price"], "stop": stop, "fx": rate},
                                 trading.live_portfolio(client), cfg)
        ok.append({"ticker": t, "currency": c["currency"], "price": c["price"], "price_date": c["price_date"],
                   "stop": stop, "target": target, "horizon_days": horizon, "rationale": rationale,
                   "qty": qty, "value_eur": round(qty * c["price"] * rate, 2)})
    return ok, rejected


# --- Automatisch handeln (optional) --------------------------------------------------------

def auto_execute(p: dict, model: str, cfg: dict, client: PaperBroker) -> dict:
    """Einen geprüften Vorschlag als Paper-Order buchen. Nie umgehen: jede Ablehnung wird nur berichtet."""
    # Mehrere Vorschläge wurden gegen denselben Depotstand bemessen; nach der ersten Order (Gebühr, Cash,
    # Satellit-Anteil) hat sich der Stand geändert. Deshalb direkt vor dem Buchen neu bemessen – mit derselben
    # Funktion wie beim Ticket (max_qty), nie über das Vorgeschlagene hinaus.
    try:
        now_max = risk_check.max_qty({"ticker": p["ticker"], "price": p["price"], "stop": p["stop"],
                                      "fx": trading.fx_of(p["ticker"])}, trading.live_portfolio(client), cfg)
        p["qty"] = min(p["qty"], now_max)
        p["value_eur"] = round(p["qty"] * p["price"] * trading.fx_of(p["ticker"]), 2)
    except BrokerError as e:
        p["qty"] = 0
        p["value_eur"] = 0.0
    if not p["qty"] or p["qty"] <= 0:
        res = {"status": "rejected", "reasons": ["Stückzahl nach Limits 0"]}
    else:
        order = {"ticker": p["ticker"], "qty": p["qty"], "price": p["price"], "stop": p["stop"],
                 "target": p["target"], "horizon_days": p["horizon_days"], "rationale": p["rationale"],
                 "model": model, "thesis_id": p["thesis_id"]}
        try:
            placed = trading.place(client, order, cfg)
            res = {"status": "placed", "order_id": placed["order"].get("id")}
        except trading.OrderRejected as e:
            res = {"status": "rejected", "reasons": e.violations}
        except BrokerError as e:
            res = {"status": "error", "reasons": [str(e)[:200]]}
    journal.log_action({"source": "advisor", "action": "advisor_auto_order", "ticker": p["ticker"],
                        "thesis_id": p["thesis_id"], **res})
    return res


# --- Lauf --------------------------------------------------------------------------------

def save_report(report: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    for name in (f"{stamp}.json", "latest.json"):
        (REPORTS_DIR / name).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def load_report() -> dict | None:
    path = REPORTS_DIR / "latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run(cfg: dict | None = None, client: PaperBroker | None = None, caller=call_claude) -> dict:
    cfg = cfg or load_config()
    client = client or PaperBroker(cfg)
    s = settings(cfg)
    token = load_token()
    if not token:
        raise AdvisorError("Kein Claude-Token hinterlegt (Einstellungen → Claude)")
    created = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    facts = gather(cfg, client)
    report = {"created": created, "model": s["model"], "auto_trade": bool(s["auto_trade"]), "free_slots": facts["free_slots"],
              "candidates": len(facts["candidates"]), "proposals": [], "rejected": [], "skipped": []}
    if not facts["candidates"] or not facts["free_slots"]:
        report["summary"] = ("Keine freien Satellit-Plätze." if not facts["free_slots"]
                             else "Keine handelbaren Kandidaten (Screen leer, Kurse alt oder schon im Depot).")
    else:
        out = caller(build_prompt(facts), token, s["model"])
        data = parse_answer(str(out.get("result", "")))
        ok, rejected = vet(data, facts, cfg, client)
        for p in ok:
            th = journal.add_thesis(p["ticker"], "up", p["price"], p["target"], p["horizon_days"],
                                    p["rationale"], s["model"], p["stop"])
            p["thesis_id"] = th["id"]
        if s["auto_trade"]:
            for p in ok:
                p["auto"] = auto_execute(p, s["model"], cfg, client)
        skipped = data.get("skipped") if isinstance(data.get("skipped"), list) else []
        report.update(summary=str(data.get("summary") or "")[:1200], proposals=ok, rejected=rejected,
                      skipped=[{"ticker": str(x.get("ticker", ""))[:15], "reason": str(x.get("reason", ""))[:200]}
                               for x in skipped if isinstance(x, dict)][:15],
                      cost_usd=out.get("total_cost_usd"), duration_s=round((out.get("duration_ms") or 0) / 1000, 1))
    save_report(report)
    journal.log_action({"source": "advisor", "action": "advisor_run", "model": s["model"],
                        "proposals": [p["ticker"] for p in report["proposals"]],
                        "rejected": report["rejected"], "cost_usd": report.get("cost_usd")})
    return report


def main() -> None:
    if sys.argv[1:2] != ["run"]:
        print("Aufruf: advisor.py run", file=sys.stderr)
        sys.exit(1)
    try:
        emit(run())
    except AdvisorError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
