"""Claude-Code-Hooks für Order-Tools (Alpaca MCP).

    python tools/hooks.py pre    # PreToolUse: Order gegen risk_check prüfen, Exit 2 = blockiert
    python tools/hooks.py post   # PostToolUse: Aufruf + Ergebnis ins Aktionsprotokoll

Beide lesen das Hook-JSON von stdin ({"tool_name", "tool_input", "tool_response", ...}).

Der Pre-Hook arbeitet „fail closed“: Was er nicht versteht (fehlendes Portfolio, unbekannte
Feldnamen, kein Preis), blockiert er. Das Portfolio kommt aus data/portfolio.json; solange
kein Broker angebunden ist, wird es von Hand bzw. vom Tageslauf gepflegt.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    from common import DATA_DIR, JOURNAL_DIR, PRICES_DIR, load_config, read_prices
    import journal
    import risk_check
except Exception as _e:  # Importfehler = Exit 1 = Claude Code ließe durch → im pre-Modus blockieren
    if "pre" in sys.argv[1:2]:
        print(f"Risk-Hook kann nicht laden, Order blockiert: {_e}", file=sys.stderr)
        sys.exit(2)
    raise

PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
BLOCKED_TOOL_WORDS = ("crypto", "option", "exercise", "close_all", "replace")  # nur Aktien/ETFs, long
READ_PREFIXES = ("get_", "list_", "search_")    # lesende MCP-Tools gehen ohne Prüfung durch
REDUCING_WORDS = ("cancel", "close_position")   # verringern Risiko: erlaubt, aber protokolliert
MAX_PORTFOLIO_AGE_S = 15 * 60

# Alpaca-MCP-Feldnamen variieren je Version; wir nehmen den ersten vorhandenen.
FIELDS = {
    "ticker": ("symbol", "ticker"),
    "side": ("side",),
    "qty": ("qty", "quantity"),
    "price": ("limit_price", "price"),
    # Nur echte Stop-Loss-Felder. "stop_price" ist bei Stop-/Stop-Limit-Käufen der Einstiegs-Trigger!
    "stop": ("stop_loss", "stop_loss_price"),
}


def _pick(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):  # z. B. Bracket: {"stop_loss": {"stop_price": 182}}
            v = v.get("stop_price")
        if v not in (None, ""):
            return v
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def to_order(tool_input: dict, prices_dir: Path = PRICES_DIR) -> dict:
    o = {k: _pick(tool_input, keys) for k, keys in FIELDS.items()}
    o["ticker"] = str(o["ticker"] or "").upper()
    o["side"] = str(o["side"] or "").lower()
    o["qty"], o["price"], o["stop"] = _num(o["qty"]), _num(o["price"]), _num(o["stop"])
    if isinstance(o["qty"], float) and o["qty"].is_integer():
        o["qty"] = int(o["qty"])
    if o["price"] is None and o["ticker"]:  # Market-Order: letzter Schlusskurs aus dem Cache
        rows = read_prices(str(o["ticker"]), prices_dir)
        o["price"] = rows[-1]["close"] if rows else None
    return o


def pre(event: dict, portfolio_path: Path = PORTFOLIO_PATH, cfg: dict | None = None,
        prices_dir: Path = PRICES_DIR, journal_dir: Path = JOURNAL_DIR, now=None) -> tuple[int, str]:
    """Gibt (exit_code, meldung) zurück."""
    tool = str(event.get("tool_name", "")).lower()
    short = tool.rsplit("__", 1)[-1]
    if short.startswith(READ_PREFIXES):
        return 0, ""
    if any(w in tool for w in BLOCKED_TOOL_WORDS):
        return 2, f"Risk-Hook: {event.get('tool_name')} ist gesperrt (nur Aktien/ETFs, long)."
    if any(w in short for w in REDUCING_WORDS):
        journal.log_action({"hook": "pre", "tool": event.get("tool_name"), "input": event.get("tool_input"),
                            "note": "risikomindernd, erlaubt"}, journal_dir)
        return 0, ""
    tin = event.get("tool_input") or {}
    if str(tin.get("side", "")).lower() == "buy" and str(tin.get("order_class", "")).lower() not in ("bracket", "oto"):
        return 2, "Risk-Hook: Käufe nur als Bracket/OTO mit stop_loss.stop_price."
    try:
        with open(portfolio_path, encoding="utf-8") as f:
            portfolio = json.load(f)
    except (OSError, ValueError) as e:
        return 2, f"Risk-Hook: Portfolio nicht lesbar ({portfolio_path}): {e}"
    age = time.time() - float(portfolio.get("synced_at") or 0)
    if age > MAX_PORTFOLIO_AGE_S:
        return 2, "Risk-Hook: Portfolio älter als 15 Minuten – erst `python3 tools/broker.py sync`."
    order = to_order(tin, prices_dir)
    res = risk_check.check(order, portfolio, cfg or load_config(), now)
    journal.log_action({"hook": "pre", "tool": event.get("tool_name"), "order": order, "risk": res},
                       journal_dir)
    if res["approved"]:
        return 0, ""
    hint = f" Vorschlag: qty={res['suggested_qty']}." if "suggested_qty" in res else ""
    return 2, "Risk-Hook blockiert: " + "; ".join(res["violations"]) + "." + hint


def post(event: dict) -> None:
    resp = event.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    journal.log_action({"hook": "post", "tool": event.get("tool_name"),
                        "input": event.get("tool_input"), "response": text[:2000]})


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = None
    if mode == "pre":
        if not isinstance(event, dict):
            print("Risk-Hook: ungültiges Hook-JSON", file=sys.stderr)
            sys.exit(2)
        try:
            code, msg = pre(event)
        except Exception as e:  # Absturz = Exit 1 = Claude Code lässt durch → deshalb blockieren
            code, msg = 2, f"Risk-Hook-Fehler, Order blockiert: {type(e).__name__}: {e}"
        if msg:
            print(msg, file=sys.stderr)  # stderr geht bei Exit 2 an Claude zurück
        sys.exit(code)
    elif mode == "post":
        if isinstance(event, dict):
            try:
                post(event)
            except OSError as e:  # Protokollfehler darf den Lauf nicht stoppen, aber sichtbar sein
                print(f"Journal-Hook: {e}", file=sys.stderr)
    else:
        print("Aufruf: hooks.py pre|post", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
