"""Reconciliation - broker positions vs local state.

Closes gaps 10 and 13 from the scenario suite:
  10  agent restart with an open position
  13  a position exists at the broker that the agent does not know about

If the two views disagree, we raise EMERGENCY and refuse to trade rather than
guess. Never trades blind.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.notifier import emergency, notify


def broker_positions(client, settings=None) -> list:
    """Open positions as the BROKER sees them (demo or real)."""
    if settings is None:
        try:
            from etoro.config import load_settings
            settings = load_settings()
        except Exception:
            settings = None
    path = "/trading/info/demo/portfolio" if (settings is not None and settings.is_demo) \
        else "/trading/info/portfolio"
    cp = client.get(path).get("clientPortfolio", {}) or {}
    return cp.get("positions") or []


def summarise_broker(positions: list) -> list:
    out = []
    for p in positions:
        out.append({
            "position_id": p.get("positionID") or p.get("positionId") or p.get("id"),
            "instrument_id": p.get("instrumentID") or p.get("instrumentId"),
            "is_buy": p.get("isBuy"),
            "units": p.get("units"),
            "amount": p.get("amount") or p.get("investment"),
            "open_rate": p.get("openRate") or p.get("openPrice"),
            "stop_loss_rate": p.get("stopLossRate"),
            "take_profit_rate": p.get("takeProfitRate"),
        })
    return out


def reconcile(client, local_open: list, dry_run: bool = False) -> dict:
    """Compare local open positions against the broker. Returns a report."""
    try:
        bp = broker_positions(client)
    except Exception as exc:
        return {"ok": False, "error": f"broker read failed: {str(exc)[:160]}",
                "action": "refuse_to_trade"}

    broker = summarise_broker(bp)
    broker_ids = {str(b.get("position_id")) for b in broker}
    local_ids = {str(p.get("broker_position_id")) for p in local_open if p.get("broker_position_id")}

    unknown_at_broker = [b for b in broker if str(b.get("position_id")) not in local_ids]
    missing_at_broker = [p for p in local_open
                         if p.get("broker_position_id")
                         and str(p.get("broker_position_id")) not in broker_ids]
    unprotected = [b for b in broker if not b.get("stop_loss_rate")]

    # On the DEMO account the human also trades by hand, so an unknown
    # position is expected there - report it, do not block on it. On a real
    # account an unknown position IS a red flag and must block.
    demo = False
    try:
        from etoro.config import load_settings
        demo = load_settings().is_demo
    except Exception:
        pass

    problems = []
    if unknown_at_broker and not demo:
        problems.append(f"{len(unknown_at_broker)} position(s) at the broker that we do not know about")
    if missing_at_broker:
        problems.append(f"{len(missing_at_broker)} local position(s) no longer at the broker")
    if unprotected:
        problems.append(f"{len(unprotected)} broker position(s) with NO stop loss")

    report = {
        "ok": not problems,
        "broker_count": len(broker),
        "local_count": len(local_open),
        "unknown_at_broker": unknown_at_broker,
        "external_at_broker": [b.get("position_id") for b in unknown_at_broker] if demo else [],
        "account_mode": "demo" if demo else "real",
        "missing_at_broker": [p.get("id") for p in missing_at_broker],
        "unprotected_at_broker": unprotected,
        "problems": problems,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if problems and not dry_run:
        body = "Position reconciliation failed:\n" + "\n".join(f"  - {p}" for p in problems)
        notify(emergency(body), kind="emergency")
        report["action"] = "EMERGENCY - trading disabled until reconciled"
    elif problems:
        report["action"] = "would raise EMERGENCY (dry run)"
    else:
        report["action"] = "in sync"
    return report


def check_protection(client) -> tuple:
    """Watchdog check: is every broker position protected by a PLAUSIBLE stop?

    Presence alone is not enough. If stopLossRate is a price rather than a
    percentage, a stop of 1.0 on gold at ~4150 would "exist" but be useless.
    So we also validate the distance is somewhere sane.
    """
    try:
        bp = summarise_broker(broker_positions(client))
    except Exception as exc:
        return False, f"broker read failed: {str(exc)[:120]}"

    problems = []
    for b in bp:
        pid = b.get("position_id")
        sl, entry = b.get("stop_loss_rate"), b.get("open_rate")
        if not sl:
            problems.append(f"position {pid} has NO stop loss")
            continue
        try:
            slf, enf = float(sl), float(entry) if entry else None
        except (TypeError, ValueError):
            continue
        if enf:
            dist = abs(slf - enf) / enf * 100.0
            # eToro allows stops up to 50% (maxStopLossPercentage). Allow
            # that, but still catch a price-vs-percent error (which would be
            # ~99% away).
            if dist < 0.05 or dist > 50.0:
                problems.append(
                    f"position {pid} stop is {dist:.2f}% from entry - implausible "
                    f"(entry {enf}, stop {slf})")
    if problems:
        return False, "; ".join(problems)[:200]
    return True, f"{len(bp)} broker position(s), all with plausible stops"


if __name__ == "__main__":
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from etoro import build_client
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    from strategies import paper
    local = paper.open_positions() if not a.dry_run else []
    rep = reconcile(build_client(), local, dry_run=True)
    print(json.dumps(rep, indent=2, default=str))
