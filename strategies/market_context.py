"""Market context factors for gold - USD, oil, bitcoin.

These are logged ALONGSIDE every signal, not used as trade filters yet.

Why: with 4 signals there is no evidence base to justify a filter, and
correlations between gold and these factors are regime-dependent and unstable.
Adding a filter now would be curve-fitting to noise. Logging costs nothing and
builds the dataset needed to test the relationship properly at 1,000 signals.

Factor instrument IDs (verified on eToro):
  USD   25      US Dollar Index (Non Expiry)
  OIL   17      Oil (Non Expiry, WTI)
  BRENT 341     Brent Oil (Non Expiry)
  BTC   100000  Bitcoin
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

FACTORS: Dict[str, int] = {"USD": 25, "OIL": 17, "BRENT": 341, "BTC": 100000}
GOLD_ID = 559


def _fetch(client, instrument_id: int, interval: str, count: int):
    path = f"/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = client.get(path)
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    return list(reversed(rows))          # oldest-first


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if not new or not old:
        return None
    return (new - old) / old * 100.0


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 10:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _returns(rows) -> List[float]:
    closes = [float(r["close"]) for r in rows if r.get("close")]
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _trend(chg_24h: Optional[float]) -> str:
    if chg_24h is None:
        return "unknown"
    if chg_24h > 0.5:
        return "up"
    if chg_24h < -0.5:
        return "down"
    return "flat"


def snapshot(client=None, bars: int = 200, with_correlations: bool = True) -> dict:
    """Return the current state of each factor plus its correlation to gold."""
    from etoro import build_client
    client = client or build_client()

    out: dict = {"factors": {}, "correlations": {}, "note": ""}
    gold_rows = None

    for sym, iid in FACTORS.items():
        try:
            rows = _fetch(client, iid, "OneHour", bars)
        except Exception as exc:
            out["factors"][sym] = {"error": str(exc)[:120]}
            continue
        if not rows:
            out["factors"][sym] = {"error": "no candles"}
            continue
        closes = [float(r["close"]) for r in rows if r.get("close")]
        if not closes:
            out["factors"][sym] = {"error": "no closes"}
            continue
        last = closes[-1]
        chg_1h = _pct_change(last, closes[-2]) if len(closes) > 1 else None
        chg_24h = _pct_change(last, closes[-25]) if len(closes) > 25 else None
        out["factors"][sym] = {
            "instrument_id": iid,
            "price": round(last, 4),
            "chg_1h_pct": round(chg_1h, 3) if chg_1h is not None else None,
            "chg_24h_pct": round(chg_24h, 3) if chg_24h is not None else None,
            "trend_24h": _trend(chg_24h),
        }
        if with_correlations:
            try:
                if gold_rows is None:
                    gold_rows = _fetch(client, GOLD_ID, "OneHour", bars)
                r = _pearson(_returns(gold_rows), _returns(rows))
                out["correlations"][sym] = round(r, 3) if r is not None else None
            except Exception:
                out["correlations"][sym] = None

    out["note"] = ("hourly log-return correlation vs gold, "
                   f"approx {bars} bars; index-aligned from the most recent bar")
    return out


if __name__ == "__main__":
    import json, sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(snapshot(), indent=2))
