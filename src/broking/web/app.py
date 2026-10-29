"""Weboberfläche für broking: Kurse laden, Screen, Charts, Journal, Risiko-Check.

Start lokal:   uvicorn web.app:app --port 8000   (aus dem Projektordner)
Im Container:  siehe Dockerfile; Daten liegen dann unter BROKING_DATA_DIR=/data.

Es gibt bewusst KEINEN Order-Endpunkt: Die Oberfläche zeigt, prüft und protokolliert,
sie handelt nicht. paper_trading lässt sich hier nicht abschalten.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fetch_prices  # noqa: E402
import journal  # noqa: E402
import risk_check  # noqa: E402
import screen  # noqa: E402
from common import (CONFIG_PATH, DEFAULT_CONFIG_PATH, JOURNAL_DIR, SCREENS_DIR,  # noqa: E402
                    load_config, read_prices)
from indicators import sma_series  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def ensure_config() -> None:
    """Beim ersten Start die mitgelieferte Konfiguration ins Datenvolume kopieren."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)


def check_ticker(ticker: str) -> str:
    t = ticker.upper()
    if not TICKER_RE.match(t):
        raise HTTPException(400, f"Ungültiges Kürzel: {ticker}")
    return t


# --- Hintergrundjobs (Kurse laden + Screen) --------------------------------------------

class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "step": None, "started": None, "finished": None,
                      "trigger": None, "result": None, "error": None}

    def start(self, steps: list[str], force: bool, trigger: str) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, step=None, started=_now(), finished=None,
                              trigger=trigger, result=None, error=None)
        threading.Thread(target=self._run, args=(steps, force), daemon=True).start()
        return True

    def _run(self, steps: list[str], force: bool) -> None:
        result = {}
        try:
            cfg = load_config()
            if "fetch" in steps:
                self.state["step"] = "fetch"
                result["fetch"] = fetch_prices.fetch(cfg["universe"], force=force)
            if "screen" in steps:
                self.state["step"] = "screen"
                res = screen.run(cfg["universe"], cfg["screen"], cfg["screen"]["top_n"])
                save_screen(res)
                result["screen"] = {"passed": res["passed"], "screened": res["screened"]}
        except Exception as e:  # Fehler sichtbar machen statt den Thread still sterben zu lassen
            self.state["error"] = str(e)[:500]
        finally:
            self.state.update(running=False, step=None, finished=_now(), result=result)


jobs = Jobs()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def save_screen(res: dict) -> None:
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    res = {"created": _now(), **res}
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    for name in (f"{stamp}.json", "latest.json"):
        (SCREENS_DIR / name).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")


def load_screen() -> dict | None:
    path = SCREENS_DIR / "latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- Zeitplan --------------------------------------------------------------------------

class Scheduler:
    """Startet „Kurse laden + Screen“ zu den Uhrzeiten aus config.schedule (Mo–Fr)."""

    def __init__(self):
        self.last_key: str | None = None

    def next_run(self, cfg: dict, now: dt.datetime | None = None) -> str | None:
        sched = cfg.get("schedule", {})
        if not sched.get("enabled") or not sched.get("times"):
            return None
        from zoneinfo import ZoneInfo

        local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(sched["tz"]))
        for day in range(8):
            d = local.date() + dt.timedelta(days=day)
            if d.weekday() >= 5:
                continue
            for t in sorted(sched["times"]):
                cand = dt.datetime.combine(d, dt.time.fromisoformat(t), local.tzinfo)
                if cand > local:
                    return cand.isoformat(timespec="minutes")
        return None

    def due(self, cfg: dict, now: dt.datetime | None = None) -> str | None:
        """Gibt einen Schlüssel zurück, wenn jetzt eine geplante Minute ist und sie noch nicht lief."""
        sched = cfg.get("schedule", {})
        if not sched.get("enabled"):
            return None
        from zoneinfo import ZoneInfo

        local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(sched["tz"]))
        if local.weekday() >= 5 or local.strftime("%H:%M") not in sched.get("times", []):
            return None
        key = local.strftime("%Y-%m-%d %H:%M")
        return None if key == self.last_key else key

    def loop(self) -> None:
        while True:
            try:
                key = self.due(load_config())
                if key and jobs.start(["fetch", "screen"], force=True, trigger="zeitplan"):
                    self.last_key = key
            except Exception:
                pass  # Konfigurationsfehler dürfen den Zeitplan nicht beenden
            time.sleep(20)


scheduler = Scheduler()

# --- API -------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app):
    ensure_config()
    threading.Thread(target=scheduler.loop, daemon=True).start()
    yield


app = FastAPI(title="Broking", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)


@app.get("/api/status")
def status():
    cfg = load_config()
    last = load_screen()
    return {
        "paper_trading": cfg.get("paper_trading") is True,
        "universe_size": len(cfg["universe"]),
        "job": jobs.state,
        "last_screen": last and last.get("created"),
        "next_run": scheduler.next_run(cfg),
        "open_theses": sum(1 for t in journal.load_theses() if t["status"] == "open"),
        "due_theses": len(journal.due_theses()),
    }


class RunRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: ["fetch", "screen"])
    force: bool = False


@app.post("/api/run")
def run(req: RunRequest):
    if not set(req.steps) <= {"fetch", "screen"} or not req.steps:
        raise HTTPException(400, "steps: fetch und/oder screen")
    if not jobs.start(req.steps, req.force, trigger="manuell"):
        raise HTTPException(409, "Es läuft bereits ein Job")
    return jobs.state


@app.get("/api/screen")
def get_screen():
    return load_screen() or {"candidates": [], "rejected": {}, "missing_data": [], "created": None}


@app.get("/api/prices/{ticker}")
def prices(ticker: str, days: int = Query(250, ge=20, le=2000)):
    t = check_ticker(ticker)
    rows = read_prices(t)
    if not rows:
        raise HTTPException(404, f"Keine Kurse für {t} – erst Kurse laden")
    closes = [r["close"] for r in rows]
    s50, s200 = sma_series(closes, 50), sma_series(closes, 200)
    start = max(0, len(rows) - days)
    rnd = lambda v: None if v is None else round(v, 4)  # noqa: E731
    return {
        "ticker": t,
        "dates": [r["date"] for r in rows[start:]],
        "close": closes[start:],
        "sma50": [rnd(v) for v in s50[start:]],
        "sma200": [rnd(v) for v in s200[start:]],
        "metrics": screen.metrics(rows),
    }


@app.get("/api/theses")
def theses(status: str | None = None):
    items = journal.load_theses()
    if status == "due":
        return journal.due_theses()
    return [t for t in items if not status or t["status"] == status][::-1]


class ThesisIn(BaseModel):
    ticker: str
    direction: str
    entry: float
    target: float
    stop: float | None = None
    horizon_days: int
    rationale: str
    model: str = "manuell"


@app.post("/api/theses")
def add_thesis(t: ThesisIn):
    try:
        return journal.add_thesis(check_ticker(t.ticker), t.direction, t.entry, t.target,
                                  t.horizon_days, t.rationale, t.model, t.stop)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ResolveIn(BaseModel):
    status: str
    price: float | None = None
    note: str = ""


@app.post("/api/theses/{thesis_id}/resolve")
def resolve_thesis(thesis_id: str, r: ResolveIn):
    try:
        return journal.resolve(thesis_id, r.status, r.price, r.note)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/actions")
def actions(limit: int = Query(100, ge=1, le=1000)):
    path = JOURNAL_DIR / "actions.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


class RiskIn(BaseModel):
    order: dict
    portfolio: dict


@app.post("/api/risk/check")
def risk(r: RiskIn):
    if not isinstance(r.portfolio.get("equity"), (int, float)):
        raise HTTPException(400, "portfolio.equity fehlt")
    return risk_check.check(r.order, r.portfolio, load_config())


@app.get("/api/risk/size")
def size(price: float, stop: float, equity: float):
    return {"qty": risk_check.position_size(price, stop, equity, load_config()["risk"])}


@app.get("/api/config")
def get_config():
    return load_config()


def validate_config(new: dict) -> dict:
    """Nur bekannte Felder mit plausiblen Werten; paper_trading bleibt immer true."""
    base = load_config()
    try:
        universe = [check_ticker(str(t)) for t in new["universe"]]
        if not universe:
            raise ValueError("Universum ist leer")
        sc, rk, sched = new["screen"], new["risk"], new.get("schedule", base.get("schedule", {}))
        screen_cfg = {
            "top_n": int(sc["top_n"]), "min_history_days": int(sc["min_history_days"]),
            "min_avg_volume": float(sc["min_avg_volume"]),
            "rsi_min": float(sc["rsi_min"]), "rsi_max": float(sc["rsi_max"]),
        }
        if not (1 <= screen_cfg["top_n"] <= 50 and 0 <= screen_cfg["rsi_min"] < screen_cfg["rsi_max"] <= 100
                and screen_cfg["min_history_days"] >= 200):
            raise ValueError("Screen-Werte unplausibel (top_n 1–50, RSI 0–100, Historie ≥ 200)")
        risk_cfg = {**base["risk"]}
        for k, hi in (("max_position_pct", 100), ("max_risk_per_trade_pct", 10), ("max_daily_loss_pct", 20)):
            v = float(rk[k])
            if not 0 < v <= hi:
                raise ValueError(f"{k} muss zwischen 0 und {hi} liegen")
            risk_cfg[k] = v
        risk_cfg["max_open_positions"] = int(rk["max_open_positions"])
        risk_cfg["enforce_market_hours"] = bool(rk["enforce_market_hours"])
        times = sorted(set(sched.get("times", [])))
        if not all(TIME_RE.match(t) for t in times):
            raise ValueError("Zeitplan: Uhrzeiten im Format HH:MM")
        schedule_cfg = {"enabled": bool(sched.get("enabled")), "tz": base.get("schedule", {}).get("tz", "Europe/Berlin"),
                        "times": times}
    except (KeyError, TypeError) as e:
        raise ValueError(f"Feld fehlt oder hat falschen Typ: {e}")
    benchmark = check_ticker(str(new.get("benchmark", base.get("benchmark", "URTH"))))
    return {**base, "paper_trading": True, "universe": universe, "benchmark": benchmark,
            "screen": screen_cfg, "risk": risk_cfg, "schedule": schedule_cfg}


@app.put("/api/config")
def put_config(new: dict):
    try:
        cfg = validate_config(new)
    except (ValueError, HTTPException) as e:
        raise HTTPException(400, getattr(e, "detail", None) or str(e))
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    return cfg


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
