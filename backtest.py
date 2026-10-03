"""Backtest the Adaptive Gold Range Scalper on recent 15m candles.

Walks forward bar by bar: at each 15m close it recomputes the regime, the
range, and the entry rules, then manages any open position against the NEXT
bars using their high/low.

Deliberately conservative: if a single bar touches both the stop and a target,
the STOP is assumed to hit first.

    python backtest.py --bars 1000 --equity 200 --risk-pct 0.01
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from etoro import build_client
from strategies.gold_range_scalper import ScalperConfig, analyze
from strategies.indicators import to_candles

SYMBOLS = {"GOLD": 559, "GLD": 3025}


def fetch(client, iid, interval, count):
    p = client.get(f"/market-data/instruments/{iid}/history/candles/desc/{interval}/{count}")
    groups = p.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    return to_candles(rows)[::-1]


def run(symbol="GOLD", bars=1000, equity=200.0, risk_pct=0.01, cfg=None):
    cfg = cfg or ScalperConfig()
    iid = SYMBOLS[symbol.upper()]
    client = build_client()

    m15 = fetch(client, iid, "FifteenMinutes", bars)
    h1 = fetch(client, iid, "OneHour", 400)
    if not m15 or not h1:
        return {"error": "no candles"}

    warmup = max(cfg.range_lookback_bars + 5, 120)
    spread_pct = 0.00482          # observed live
    trades = []
    pos = None
    signals = 0
    gate_blocks = 0

    for i in range(warmup, len(m15)):
        bar = m15[i]
        bt = bar.time

        # ---- manage an open position against THIS bar
        if pos is not None:
            hit = None
            if pos["side"] == "BUY":
                if pos["stop"] is not None and bar.low <= pos["stop"]:
                    hit = ("stop", 1.0, pos["stop"])
                elif pos["tp2"] is not None and bar.high >= pos["tp2"]:
                    hit = ("tp2", 1.0, pos["tp2"])
                elif pos["tp1"] is not None and not pos["tp1_done"] and bar.high >= pos["tp1"]:
                    hit = ("tp1", cfg.tp1_portion, pos["tp1"])
            else:
                if pos["stop"] is not None and bar.high >= pos["stop"]:
                    hit = ("stop", 1.0, pos["stop"])
                elif pos["tp2"] is not None and bar.low <= pos["tp2"]:
                    hit = ("tp2", 1.0, pos["tp2"])
                elif pos["tp1"] is not None and not pos["tp1_done"] and bar.low <= pos["tp1"]:
                    hit = ("tp1", cfg.tp1_portion, pos["tp1"])

            if hit:
                reason, portion, price = hit
                portion = min(portion, pos["remaining"])
                notional = pos["notional"] * portion
                direction = 1.0 if pos["side"] == "BUY" else -1.0
                gross = notional * (price - pos["entry"]) / pos["entry"] * direction
                cost = notional * spread_pct / 100.0
                pnl = gross - cost
                trades.append({"reason": reason, "portion": round(portion, 3),
                               "entry": pos["entry"], "exit": round(price, 2),
                               "notional": round(notional, 2), "pnl": round(pnl, 4)})
                pos["remaining"] -= portion
                if reason == "tp1":
                    pos["tp1_done"] = True
                if pos["remaining"] <= 1e-9 or reason == "stop":
                    pos = None

        if pos is not None:
            continue

        # ---- look for a new entry
        h1w = [c for c in h1 if c.time <= bt]
        m15w = m15[:i + 1]
        if len(h1w) < 60:
            continue
        rep = analyze(h1w, m15w, cfg)
        sig = rep.get("signal", {})
        if rep.get("mode") != "RANGE":
            gate_blocks += 1
            continue
        if sig.get("action") not in ("BUY", "SELL"):
            continue

        signals += 1
        entry = float(sig["entry"])
        stop = float(sig["stop"])
        stop_dist = abs(entry - stop) / entry
        if stop_dist <= 0:
            continue
        notional = (equity * risk_pct) / stop_dist
        pos = {"side": sig["action"], "entry": entry, "stop": stop,
               "tp1": sig.get("tp1"), "tp2": sig.get("tp2"),
               "notional": notional, "remaining": 1.0, "tp1_done": False,
               "opened": bt}

    # ---- summarise
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    return {
        "symbol": symbol, "bars": len(m15), "hours": round(len(m15) * 0.25, 1),
        "signals_fired": signals, "bars_in_trend_mode": gate_blocks,
        "fills": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else None,
        "gross_profit": round(gross_p, 2), "gross_loss": round(gross_l, 2),
        "net_pnl": round(net, 2),
        "end_equity": round(equity + net, 2),
        "return_pct": round(net / equity * 100, 2),
        "by_reason": {r: sum(1 for t in trades if t["reason"] == r)
                      for r in ("tp1", "tp2", "stop")},
        "open_at_end": pos is not None,
        "trades": trades[:40],
    }


def main(argv=None):
    p = argparse.ArgumentParser(prog="backtest.py")
    p.add_argument("--symbol", default="GOLD")
    p.add_argument("--bars", type=int, default=1000)
    p.add_argument("--equity", type=float, default=200.0)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = run(a.symbol, a.bars, a.equity, a.risk_pct)
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
