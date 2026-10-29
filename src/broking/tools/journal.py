"""Journal: messbare Thesen und Aktionsprotokoll.

Thesen  → journal/theses.jsonl  (eine Zeile pro These, Status wird fortgeschrieben)
Aktionen → journal/actions.jsonl (append-only, z. B. aus dem PostToolUse-Hook)

Eine These ist messbar: „TICKER steigt/fällt innerhalb von N Tagen über/unter ZIEL“.

Beispiele:
    python tools/journal.py add --ticker AAPL --direction up --entry 190.5 --target 200 \
        --horizon-days 10 --rationale "Ausbruch über 52W-Hoch, Earnings-Beat" --model sonnet-5.5
    python tools/journal.py list --status open
    python tools/journal.py due
    python tools/journal.py resolve --id <ID> --status hit --price 201.3
    echo '{"tool":"place_order","ticker":"AAPL"}' | python tools/journal.py log
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

from common import JOURNAL_DIR, emit

STATUSES = ("open", "hit", "miss", "cancelled")


def _theses_path(d: Path) -> Path:
    return d / "theses.jsonl"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_theses(journal_dir: Path = JOURNAL_DIR) -> list[dict]:
    path = _theses_path(journal_dir)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _save_theses(theses: list[dict], journal_dir: Path) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = _theses_path(journal_dir)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for t in theses:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    tmp.replace(path)  # atomar, damit ein Absturz das Journal nicht zerschießt


def _ensure(journal_dir: Path) -> Path:
    journal_dir.mkdir(parents=True, exist_ok=True)
    return _theses_path(journal_dir)


def add_thesis(ticker: str, direction: str, entry: float, target: float, horizon_days: int,
               rationale: str, model: str = "", stop: float | None = None,
               journal_dir: Path = JOURNAL_DIR, today: dt.date | None = None) -> dict:
    if direction not in ("up", "down"):
        raise ValueError("direction muss 'up' oder 'down' sein")
    if horizon_days <= 0:
        raise ValueError("horizon_days muss > 0 sein")
    if direction == "up" and target <= entry or direction == "down" and target >= entry:
        raise ValueError("Ziel liegt nicht in Richtung der These")
    if not rationale.strip():
        raise ValueError("rationale darf nicht leer sein")
    today = today or dt.date.today()
    thesis = {
        "id": f"{today:%Y%m%d}-{ticker.upper()}-{uuid.uuid4().hex[:6]}",
        "created": _now(),
        "ticker": ticker.upper(),
        "direction": direction,
        "entry": entry,
        "target": target,
        "stop": stop,
        "horizon_days": horizon_days,
        "deadline": (today + dt.timedelta(days=horizon_days)).isoformat(),
        "rationale": rationale.strip(),
        "model": model,
        "status": "open",
    }
    with open(_ensure(journal_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(thesis, ensure_ascii=False) + "\n")
    return thesis


def due_theses(journal_dir: Path = JOURNAL_DIR, today: dt.date | None = None) -> list[dict]:
    """Offene Thesen, deren Frist abgelaufen ist – Futter für evaluate_theses.py."""
    today = (today or dt.date.today()).isoformat()
    return [t for t in load_theses(journal_dir) if t["status"] == "open" and t["deadline"] <= today]


def resolve(thesis_id: str, status: str, price: float | None = None, note: str = "",
            journal_dir: Path = JOURNAL_DIR) -> dict:
    if status not in STATUSES or status == "open":
        raise ValueError(f"status muss einer von {STATUSES[1:]} sein")
    theses = load_theses(journal_dir)
    for t in theses:
        if t["id"] == thesis_id:
            if t["status"] != "open":
                raise ValueError(f"These {thesis_id} ist schon {t['status']}")
            t.update(status=status, resolved_at=_now(), resolved_price=price, note=note)
            _save_theses(theses, journal_dir)
            return t
    raise KeyError(f"These {thesis_id} nicht gefunden")


def log_action(record: dict, journal_dir: Path = JOURNAL_DIR) -> dict:
    journal_dir.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now(), **record}
    with open(journal_dir / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--ticker", required=True)
    a.add_argument("--direction", choices=("up", "down"), required=True)
    a.add_argument("--entry", type=float, required=True)
    a.add_argument("--target", type=float, required=True)
    a.add_argument("--stop", type=float)
    a.add_argument("--horizon-days", type=int, required=True)
    a.add_argument("--rationale", required=True)
    a.add_argument("--model", default="")

    ls = sub.add_parser("list")
    ls.add_argument("--status", choices=STATUSES)

    sub.add_parser("due")

    r = sub.add_parser("resolve")
    r.add_argument("--id", required=True)
    r.add_argument("--status", choices=STATUSES[1:], required=True)
    r.add_argument("--price", type=float)
    r.add_argument("--note", default="")

    sub.add_parser("log", help="JSON-Objekt von stdin ins Aktionsprotokoll")

    args = p.parse_args()
    try:
        if args.cmd == "add":
            emit(add_thesis(args.ticker, args.direction, args.entry, args.target,
                            args.horizon_days, args.rationale, args.model, args.stop))
        elif args.cmd == "list":
            emit([t for t in load_theses() if not args.status or t["status"] == args.status])
        elif args.cmd == "due":
            emit(due_theses())
        elif args.cmd == "resolve":
            emit(resolve(args.id, args.status, args.price, args.note))
        elif args.cmd == "log":
            emit(log_action(json.load(sys.stdin)))
    except (ValueError, KeyError) as e:
        emit({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
