import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def make_rows(closes, volume=1_000_000, start_day=0):
    """Synthetische Tageskurse: high/low ±1 % um den Schlusskurs."""
    import datetime as dt

    d0 = dt.date(2025, 1, 1) + dt.timedelta(days=start_day)
    return [
        {"date": (d0 + dt.timedelta(days=i)).isoformat(), "open": c, "high": c * 1.01,
         "low": c * 0.99, "close": c, "volume": volume}
        for i, c in enumerate(closes)
    ]
