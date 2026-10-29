"""Technische Kennzahlen in reinem Python (keine pandas-Abhängigkeit)."""
from __future__ import annotations


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(closes: list[float], n: int = 14) -> float | None:
    """RSI nach Wilder."""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100 - 100 / (1 + avg_g / avg_l)


def atr(rows: list[dict], n: int = 14) -> float | None:
    """Average True Range nach Wilder."""
    if len(rows) < n + 1:
        return None
    trs = []
    for prev, cur in zip(rows[:-1], rows[1:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def pct_change(values: list[float], n: int) -> float | None:
    if len(values) < n + 1 or values[-n - 1] == 0:
        return None
    return (values[-1] / values[-n - 1] - 1) * 100


def max_drawdown_pct(values: list[float]) -> float:
    """Größter Rückgang vom Hoch in Prozent (negativ oder 0)."""
    peak, mdd = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v / peak - 1) * 100)
    return mdd


def sma_series(values: list[float], n: int) -> list[float | None]:
    """Gleitender Durchschnitt für jeden Punkt (None, solange zu wenig Historie)."""
    out, acc = [], 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        out.append(acc / n if i >= n - 1 else None)
    return out
