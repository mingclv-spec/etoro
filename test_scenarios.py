"""Scenario test suite - the 14 cases Ming specified.

Runs against the REAL modules with an isolated temp ledger, so nothing here
can touch the live paper journal. Reports PASS / FAIL / GAP per scenario.
A GAP means the capability is not implemented at all - which is a finding,
not a failure of the test.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TMP = tempfile.mkdtemp(prefix="scalper-tests-")

import strategies.paper as paper
paper.POSITIONS_PATH = os.path.join(TMP, "paper_positions.jsonl")
paper.TRADES_PATH = os.path.join(TMP, "trades.jsonl")

import position_monitor as pm
pm.LOG_PATH = os.path.join(TMP, "pm.jsonl")
pm.MARKET_STATE = os.path.join(TMP, "market_state.json")
pm.notify = lambda *a, **k: None

import execute
execute.LOG_PATH = os.path.join(TMP, "exec.jsonl")
execute.STATE_PATH = os.path.join(TMP, "exec_state.json")
execute.notify = lambda *a, **k: None

from strategies.trading_gate import GateConfig, evaluate as gate
from strategies.killswitch import KillSwitchConfig, KillSwitchState, evaluate as ks_eval

E, S, T1, T2 = 4133.77, 4113.10, 4160.39, 4178.14
RESULTS = []


def record(num, name, status, detail=""):
    RESULTS.append((num, name, status, detail))


def clean():
    for f in (paper.POSITIONS_PATH, paper.TRADES_PATH, execute.STATE_PATH):
        if os.path.exists(f):
            os.remove(f)


def open_pos(side="BUY"):
    return paper.open_position("GOLD", side, 400.0, E, stop=S,
                               tp1=T1, tp2=T2, spread_pct=0.00482)


def net():
    return round(sum(t["pnl"] for t in paper.closed_trades()), 4)


# ---- 1-5: exit paths -------------------------------------------------
def exits():
    # BUY -> TP1 -> TP2
    clean(); open_pos("BUY")
    pm.check_once(forced_price=T1); pm.check_once(forced_price=T2)
    ok = len(paper.open_positions()) == 0 and net() > 0
    record(1, "BUY -> TP1 -> TP2", "PASS" if ok else "FAIL", f"net ${net()}")

    # BUY -> STOP
    clean(); open_pos("BUY")
    pm.check_once(forced_price=S - 5)
    ok = len(paper.open_positions()) == 0 and net() < 0
    record(2, "BUY -> STOP LOSS", "PASS" if ok else "FAIL", f"net ${net()}")

    # SELL -> TP1 -> TP2   (SELL profits when price FALLS)
    clean()
    paper.open_position("GOLD", "SELL", 400.0, E, stop=E * 1.005,
                        tp1=E - 26.62, tp2=E - 44.37, spread_pct=0.00482)
    pm.check_once(forced_price=E - 26.62); pm.check_once(forced_price=E - 44.37)
    ok = len(paper.open_positions()) == 0 and net() > 0
    record(3, "SELL -> TP1 -> TP2", "PASS" if ok else "FAIL", f"net ${net()}")

    # SELL -> STOP
    clean()
    paper.open_position("GOLD", "SELL", 400.0, E, stop=E * 1.005,
                        tp1=E - 26.62, tp2=E - 44.37, spread_pct=0.00482)
    pm.check_once(forced_price=E * 1.006)
    ok = len(paper.open_positions()) == 0 and net() < 0
    record(4, "SELL -> STOP LOSS", "PASS" if ok else "FAIL", f"net ${net()}")

    # BUY -> TP1 -> then STOP on the remainder
    clean(); open_pos("BUY")
    pm.check_once(forced_price=T1)
    half = paper.open_positions()[0]["remaining_portion"]
    pm.check_once(forced_price=S - 5)
    ok = (abs(half - 0.5) < 1e-9) and len(paper.open_positions()) == 0
    record(5, "BUY -> TP1 -> SL (remainder)", "PASS" if ok else "FAIL",
           f"net ${net()} (TP1 banked, rest stopped)")


# ---- 6-9, 11-12: gates ----------------------------------------------
def gates():
    # 6: same candle twice -> skip
    clean()
    fake = {"mode": "RANGE", "signal": {"action": "BUY", "entry": E, "stop": S, "tp1": T1, "tp2": T2}}
    class C:
        def __init__(self, t): self.time = t
    hold = (execute.analyze, execute.fetch_candles, execute._live_market)
    execute.analyze = lambda *a, **k: fake
    # need >= 2 candles: the code drops the still-forming one
    execute.fetch_candles = lambda *a, **k: [C("2026-10-02T02:00:00Z"),
                                              C("2026-10-02T02:15:00Z")]
    execute._live_market = lambda: (0.00482, 0.3)
    r1 = execute.run_once("GOLD", 200.0, 0.01)
    r2 = execute.run_once("GOLD", 200.0, 0.01)
    execute.analyze, execute.fetch_candles, execute._live_market = hold
    ok = r1.get("result") == "PAPER position opened" and "idempotent" in str(r2.get("result"))
    record(6, "Signal twice same candle -> skip", "PASS" if ok else "FAIL",
           f"run2 = {r2.get('result')}")

    # 7: signal while a position is already open -> must refuse to stack
    clean()
    class C2:
        def __init__(self, t): self.time = t
    hold2 = (execute.analyze, execute.fetch_candles, execute._live_market)
    execute.analyze = lambda *a, **k: fake
    execute._live_market = lambda: (0.00482, 0.3)
    execute.fetch_candles = lambda *a, **k: [C2("2026-10-02T02:00:00Z"), C2("2026-10-02T02:15:00Z")]
    a1 = execute.run_once("GOLD", 200.0, 0.01)
    execute.fetch_candles = lambda *a, **k: [C2("2026-10-02T02:15:00Z"), C2("2026-10-02T02:30:00Z")]
    a2 = execute.run_once("GOLD", 200.0, 0.01)
    execute.analyze, execute.fetch_candles, execute._live_market = hold2
    n_open = len(paper.open_positions())
    ok = n_open == 1 and "refusing to stack" in str(a2.get("result"))
    record(7, "Two simultaneous signals -> risk gate",
           "PASS" if ok else "GAP",
           f"open={n_open}; 2nd attempt = {a2.get('result')}")

    # 8: breakout -> no new trades
    g = gate("BREAKOUT", {"allowed": True, "blocks": []}, 0.0048, 0.3, True)
    record(8, "Range -> breakout -> stop new trades",
           "PASS" if not g["trading_enabled"] else "FAIL", "; ".join(g["blocks"]))

    # 9: API disconnect -> no new trades
    g = gate("RANGE", {"allowed": True, "blocks": []}, 0.0048, 0.3, False)
    record(9, "API disconnect -> no new trades",
           "PASS" if not g["trading_enabled"] else "FAIL", "; ".join(g["blocks"]))

    # 11: stale price -> no trade
    g = gate("RANGE", {"allowed": True, "blocks": []}, 0.0048, 999.0, True)
    record(11, "Stale price feed -> no trade",
           "PASS" if not g["trading_enabled"] else "FAIL", "; ".join(g["blocks"]))

    # 12: daily loss limit -> disable
    ks = ks_eval(KillSwitchState(day="2026-10-02", daily_pnl=-60.0), KillSwitchConfig(), 5000.0)
    g = gate("RANGE", ks, 0.0048, 0.3, True)
    record(12, "Daily loss limit -> disable trading",
           "PASS" if not g["trading_enabled"] else "FAIL", "; ".join(g["blocks"]))


# ---- 10, 13, 14: reconciliation (expected gaps) ----------------------
def reconciliation():
    clean()
    open_pos("BUY")
    # does ANY module reconcile local state against the broker?
    src = ""
    for f in ("watchdog.py", "execute.py", "position_monitor.py", "collect.py",
              os.path.join("strategies", "reconcile.py"), os.path.join("strategies", "live_exec.py")):
        p = os.path.join(".", f)
        if os.path.exists(p):
            src += open(p, encoding="utf-8").read()
    has_pos_sync = "/trading/info/portfolio" in src or "positions" in src.lower() and "broker" in src.lower()
    record(10, "Agent restart with position -> reconcile",
           "PASS" if has_pos_sync else "GAP",
           "no startup reconciliation against broker positions")
    record(14, "Partial fill -> reconcile",
           "GAP", "paper fills are atomic; no fill-quantity reconciliation")
    record(13, "Unexpected broker position -> emergency",
           "GAP", "no broker/local position diff or emergency state")


def main():
    exits()
    gates()
    reconciliation()
    print("=" * 72)
    print(f"{'#':>3} {'scenario':44s} {'result':>6}  detail")
    print("=" * 72)
    counts = {}
    for num, name, status, detail in sorted(RESULTS):
        counts[status] = counts.get(status, 0) + 1
        print(f"{num:>3} {name:44s} {status:>6}  {detail[:60]}")
    print("=" * 72)
    print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    missing = [n for n, _, s, _ in sorted(RESULTS) if s == "GAP"]
    if missing:
        print(f"  gaps found in scenarios: {missing}")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        shutil.rmtree(TMP, ignore_errors=True)
