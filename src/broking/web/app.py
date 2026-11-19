"""Weboberfläche für broking: Kurse, Screen, Charts, Journal, Handel im Paper-Depot (EUR).

Start lokal:   uvicorn web.app:app --port 8000   (aus dem Projektordner)
Im Container:  siehe Dockerfile; Daten liegen dann unter BROKING_DATA_DIR=/data.

Orders gehen ausschließlich ans simulierte Paper-Depot (tools/broker.py) und werden
serverseitig gegen risk_check geprüft. paper_trading lässt sich hier nicht abschalten.
Schreibende API-Aufrufe brauchen den Header X-Broking (CSRF-Schutz).
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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import advisor  # noqa: E402
import broker  # noqa: E402
import evaluate_theses  # noqa: E402
import fetch_earnings  # noqa: E402
import fetch_prices  # noqa: E402
import journal  # noqa: E402
import trading  # noqa: E402
import risk_check  # noqa: E402
import screen  # noqa: E402
from broker import BrokerError, PaperBroker  # noqa: E402
from common import (CONFIG_PATH, DEFAULT_CONFIG_PATH, JOURNAL_DIR, SCREENS_DIR,  # noqa: E402
                    load_config, read_prices)
from indicators import sma_series  # noqa: E402
from instruments import currency as currency_of  # noqa: E402
from risk_check import core_tickers  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CONFIG_VERSION = 2


def ensure_config() -> None:
    """Beim ersten Start die mitgelieferte Konfiguration ins Datenvolume kopieren,
    eine alte Konfiguration (v1, Alpaca/US) auf v2 (Paper-Depot EUR, Core-Satellite) heben."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)
        return
    cfg = load_config()
    if cfg.get("version", 1) >= CONFIG_VERSION:
        return
    new = load_config(DEFAULT_CONFIG_PATH)
    new["schedule"] = cfg.get("schedule", new["schedule"])  # Zeitplan des Nutzers behalten
    backup = CONFIG_PATH.with_name("config.v1.json")
    shutil.copyfile(CONFIG_PATH, backup)
    _write_config(new)
    journal.log_action({"source": "web", "action": "config_migrated", "from": cfg.get("version", 1),
                        "to": CONFIG_VERSION, "backup": backup.name})


def _write_config(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)


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
                result["fetch"] = fetch_prices.fetch(fetch_prices.universe_with_fx(cfg), force=force)
                try:  # Quartalstermine: einmal pro Tag, ein Ausfall darf den Lauf nicht stoppen
                    result["earnings"] = fetch_earnings.fetch(cfg["universe"])
                except Exception as e:
                    result["earnings"] = {"error": str(e)[:200]}
            if "screen" in steps:
                self.state["step"] = "screen"
                res = screen.run(cfg["universe"], cfg["screen"], cfg["screen"]["top_n"],
                                 exclude=core_tickers(cfg))
                save_screen(res)
                result["screen"] = {"passed": res["passed"], "screened": res["screened"]}
            if "evaluate" in steps:
                self.state["step"] = "evaluate"
                ev = evaluate_theses.evaluate()
                result["evaluate"] = {"resolved": len(ev["resolved"])}
            if "sync" in steps:  # Depot fortschreiben (Limits, Stops, Ziele) und Tageswert festhalten
                self.state["step"] = "sync"
                c = PaperBroker(cfg)
                result["sync"] = {"equity": c.mark()["equity"]}
                broker.sync_portfolio(c)
            if "advise" in steps:  # Claude bewertet die Kandidaten (nur mit Token)
                self.state["step"] = "advise"
                rep = advisor.run(cfg)
                result["advise"] = {"proposals": len(rep["proposals"])}
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


ALL_STEPS = ["fetch", "screen", "evaluate", "sync"]

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

    @staticmethod
    def steps_for(cfg: dict, key: str) -> list[str]:
        """Claude bewertet nur im ersten Lauf des Tages (morgens), und nur wenn aktiviert + Token da."""
        first = min(cfg.get("schedule", {}).get("times") or ["99:99"])
        if advisor.settings(cfg)["enabled"] and key.endswith(first) and advisor.load_token():
            return [*ALL_STEPS, "advise"]
        return ALL_STEPS

    def loop(self) -> None:
        while True:
            try:
                cfg = load_config()
                key = self.due(cfg)
                if key and jobs.start(self.steps_for(cfg, key), force=True, trigger="zeitplan"):
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


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Fremde Seiten können keinen eigenen Header setzen, ohne dass der Browser blockt."""
    if (request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-broking") != "1"):
        return JSONResponse({"detail": "Header X-Broking fehlt"}, status_code=403)
    return await call_next(request)


@app.exception_handler(BrokerError)
def broker_error(_request, e: BrokerError):
    return JSONResponse({"detail": str(e)}, status_code=e.status or 502)


@app.exception_handler(trading.OrderRejected)
def order_rejected(_request, e: trading.OrderRejected):
    return JSONResponse({"detail": "Order abgelehnt", "violations": e.violations,
                         "suggested_qty": (e.risk or {}).get("suggested_qty")}, status_code=422)


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
        "broker": "paper",
        "broker_configured": True,
        "advisor": {"enabled": advisor.settings(cfg)["enabled"], "token_configured": advisor.load_token() is not None},
    }


class RunRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: ["fetch", "screen"])
    force: bool = False


@app.post("/api/run")
def run(req: RunRequest):
    if not set(req.steps) <= set(ALL_STEPS) or not req.steps:
        raise HTTPException(400, f"steps: Auswahl aus {ALL_STEPS}")
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
        "currency": currency_of(t),
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
        for k, lo, hi in (("max_position_pct", 0, 100), ("max_risk_per_trade_pct", 0, 10),
                          ("max_daily_loss_pct", 0, 20), ("max_drawdown_pct", 0, 50),
                          ("max_fee_pct", 0, 10), ("min_order_eur", -1, 10_000)):
            v = float(rk[k])
            if not lo < v <= hi:
                raise ValueError(f"{k} muss zwischen {max(lo, 0)} und {hi} liegen")
            risk_cfg[k] = v
        risk_cfg["max_open_positions"] = int(rk["max_open_positions"])
        if not 1 <= risk_cfg["max_open_positions"] <= 30:
            raise ValueError("max_open_positions muss 1–30 sein")
        risk_cfg["enforce_market_hours"] = bool(rk["enforce_market_hours"])

        pf = new["portfolio"]
        core = {check_ticker(str(t)): float(p) for t, p in pf["core"].items()}
        sat, drift = float(pf["satellite_pct"]), float(pf["rebalance_drift_pct"])
        if not all(0 < p <= 100 for p in core.values()) or not 0 <= sat <= 100 or sum(core.values()) + sat > 100:
            raise ValueError("Core- und Satellit-Quote: je 0–100 %, zusammen höchstens 100 %")
        if not 0 <= drift <= 25:
            raise ValueError("Rebalancing-Toleranz muss 0–25 % sein")
        missing = [t for t in core if t not in universe]
        if missing:
            raise ValueError(f"Core-Titel müssen im Universum stehen: {', '.join(missing)}")
        portfolio_cfg = {**base["portfolio"], "core": core, "satellite_pct": sat, "rebalance_drift_pct": drift}

        times = sorted(set(sched.get("times", [])))
        if not all(TIME_RE.match(t) for t in times):
            raise ValueError("Zeitplan: Uhrzeiten im Format HH:MM")
        schedule_cfg = {"enabled": bool(sched.get("enabled")), "tz": base.get("schedule", {}).get("tz", "Europe/Berlin"),
                        "times": times}

        adv = {**advisor.settings(base), **(new.get("advisor") or {})}
        advisor_cfg = {"enabled": bool(adv["enabled"]), "model": str(adv["model"]), "top_n": int(adv["top_n"]),
                       "auto_trade": bool(adv["auto_trade"])}
        if advisor_cfg["model"] not in advisor.MODELS or not 1 <= advisor_cfg["top_n"] <= 20:
            raise ValueError(f"Claude: Modell aus {', '.join(advisor.MODELS)}, Kandidaten 1–20")
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"Feld fehlt oder hat falschen Typ: {e}")
    unsupported = [t for t in universe if currency_of(t) is None]
    if unsupported:
        raise ValueError(f"Nur EUR- und USD-Titel möglich, nicht: {', '.join(unsupported)}")
    benchmark = check_ticker(str(new.get("benchmark", base.get("benchmark", "EUNL.DE"))))
    # paper_trading, broker und Gebühren sind über die Oberfläche nicht änderbar
    return {**base, "paper_trading": True, "universe": universe, "benchmark": benchmark,
            "portfolio": portfolio_cfg, "screen": screen_cfg, "risk": risk_cfg, "schedule": schedule_cfg,
            "advisor": advisor_cfg}


@app.put("/api/config")
def put_config(new: dict):
    try:
        cfg = validate_config(new)
    except (ValueError, HTTPException) as e:
        raise HTTPException(400, getattr(e, "detail", None) or str(e))
    _write_config(cfg)
    return cfg


# --- Thesen-Auswertung -----------------------------------------------------------------

@app.get("/api/stats")
def stats():
    """Trefferquote der Thesen (Prognosen) plus das, was im Depot wirklich herauskam (nach Gebühren)."""
    out = evaluate_theses.stats(journal.load_theses())
    try:
        out["trades"] = client().closed_trades()
    except BrokerError:
        out["trades"] = None
    return out


@app.post("/api/evaluate")
def evaluate():
    return evaluate_theses.evaluate()


# --- Broker (Paper-Depot, EUR) ----------------------------------------------------------

def client() -> PaperBroker:
    return PaperBroker(load_config())


def allocation(cfg: dict, account: dict, positions: list[dict]) -> dict:
    eq = account["equity"] or 1
    core = core_tickers(cfg)
    core_v = sum(p["market_value"] for p in positions if p["symbol"] in core)
    sat_v = sum(p["market_value"] for p in positions if p["symbol"] not in core)
    target_core = sum(cfg["portfolio"]["core"].values())
    return {
        "core_pct": round(core_v / eq * 100, 1), "satellite_pct": round(sat_v / eq * 100, 1),
        "cash_pct": round(account["cash"] / eq * 100, 1),
        "target_core_pct": target_core, "target_satellite_pct": cfg["portfolio"]["satellite_pct"],
        "core_below_target": core_v / eq * 100 < target_core - cfg["portfolio"]["rebalance_drift_pct"],
        "satellite_positions": sum(1 for p in positions if p["symbol"] not in core),
    }


@app.get("/api/broker")
def broker_status():
    cfg = load_config()
    out = {"configured": True, "paper": True, "broker": "paper", "core": cfg["portfolio"]["core"]}
    try:
        c = PaperBroker(cfg)
        a, ps = c.account(), c.positions()
        out.update(account=a, allocation=allocation(cfg, a, ps))
    except BrokerError as e:
        out["error"] = str(e)
    return out


class ResetIn(BaseModel):
    start_cash_eur: float = Field(ge=100, le=10_000_000, allow_inf_nan=False)
    confirm: str


@app.post("/api/broker/reset")
def broker_reset(r: ResetIn):
    """Paper-Depot neu starten. Thesen und Aktionsprotokoll bleiben erhalten."""
    if r.confirm != "RESET":
        raise HTTPException(400, "Bestätigung fehlt")
    a = client().reset(r.start_cash_eur)
    broker.sync_portfolio(client())
    journal.log_action({"source": "web", "action": "paper_reset", "start_cash_eur": r.start_cash_eur})
    return a


@app.get("/api/broker/positions")
def broker_positions():
    return client().positions()


@app.get("/api/broker/orders")
def broker_orders(status: str = Query("open", pattern="^(open|closed|all)$")):
    keep = ("id", "client_order_id", "symbol", "side", "type", "order_class", "qty", "filled_qty",
            "limit_price", "stop_price", "filled_avg_price", "status", "created_at", "filled_at",
            "fee_eur", "realized_pl_eur", "note")
    out = []
    for o in client().orders(status, 100):
        row = {k: o.get(k) for k in keep}
        row["legs"] = [{k: leg.get(k) for k in keep} for leg in (o.get("legs") or [])]
        out.append(row)
    return out


@app.get("/api/broker/history")
def broker_history(period: str = Query("3M", pattern="^(1M|3M|6M|1A)$")):
    """Depotwert und Benchmark, beide auf 100 indexiert (eine gemeinsame Achse)."""
    h = client().history(period)
    pairs = [(d, e) for d, e in zip(h["dates"], h["equity"]) if e]
    if not pairs:
        return {"dates": [], "equity": [], "equity_idx": [], "benchmark": None, "benchmark_idx": []}
    dates, equity = [p[0] for p in pairs], [p[1] for p in pairs]
    bench = load_config().get("benchmark")
    closes = {r["date"]: r["close"] for r in read_prices(bench)} if bench else {}
    bseries, last = [], None
    for d in dates:  # Lücken (Feiertage) mit letztem Kurs füllen
        last = closes.get(d, last)
        bseries.append(last)
    b0 = next((b for b in bseries if b), None)
    return {
        "dates": dates, "equity": equity,
        "equity_idx": [round(e / equity[0] * 100, 2) for e in equity],
        "benchmark": bench,
        "benchmark_idx": [round(b / b0 * 100, 2) if b and b0 else None for b in bseries],
    }


@app.get("/api/trade/ticket/{ticker}")
def trade_ticket(ticker: str):
    return trading.ticket(client(), check_ticker(ticker), load_config())


class OrderIn(BaseModel):
    ticker: str
    qty: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    stop: float = Field(gt=0, allow_inf_nan=False)
    target: float = Field(gt=0, allow_inf_nan=False)
    horizon_days: int = 10
    rationale: str = Field(max_length=2000)
    model: str = Field("manuell", max_length=60)
    thesis_id: str | None = Field(None, pattern=r"^[0-9A-Za-z.\-=^]{1,60}$")  # Freigabe eines Vorschlags


@app.post("/api/trade/check")
def trade_check(o: OrderIn):
    res = trading.check(client(), {**o.model_dump(), "ticker": check_ticker(o.ticker)}, load_config())
    return {"approved": True, "risk": res["risk"], "last_price": res["last_price"], "odds": res["odds"]}


@app.post("/api/trade/order")
def trade_order(o: OrderIn):
    return trading.place(client(), {**o.model_dump(), "ticker": check_ticker(o.ticker)}, load_config())


@app.delete("/api/trade/orders/{order_id}")
def trade_cancel(order_id: str):
    if not re.match(r"^[0-9a-f-]{36}$", order_id):
        raise HTTPException(400, "Ungültige Order-ID")
    trading.cancel(client(), order_id)
    return {"cancelled": order_id}


@app.post("/api/trade/close/{symbol}")
def trade_close(symbol: str):
    return trading.close(client(), check_ticker(symbol))


@app.get("/api/trade/core")
def core_plan():
    return trading.core_plan(client(), load_config())


@app.post("/api/trade/core")
def core_buy():
    return trading.buy_core(client(), load_config())


# --- Claude (Morgenbewertung) ------------------------------------------------------------

@app.get("/api/advisor")
def advisor_status():
    s = advisor.settings(load_config())
    return {**s, "token_configured": advisor.load_token() is not None,
            "cli_available": advisor.claude_bin() is not None, "report": advisor.load_report()}


class TokenIn(BaseModel):
    token: str = Field(max_length=500)


@app.put("/api/advisor/token")
def advisor_token(t: TokenIn):
    """Token nur annehmen, nie zurückgeben – auch nicht teilweise."""
    try:
        advisor.save_token(t.token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    journal.log_action({"source": "web", "action": "advisor_token_saved"})
    return {"token_configured": True}


@app.delete("/api/advisor/token")
def advisor_token_delete():
    advisor.delete_token()
    journal.log_action({"source": "web", "action": "advisor_token_deleted"})
    return {"token_configured": False}


@app.post("/api/advisor/run")
def advisor_run():
    if advisor.load_token() is None:
        raise HTTPException(400, "Kein Claude-Token hinterlegt")
    if not jobs.start(["advise"], False, trigger="manuell"):
        raise HTTPException(409, "Es läuft bereits ein Job")
    return jobs.state


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
