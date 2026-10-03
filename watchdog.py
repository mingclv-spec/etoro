"""watchdog - service 5 of 5.

Checks the health of the running system and repairs what it can:
  * market_monitor heartbeat freshness   (is it alive?)
  * price freshness                      (is data still flowing?)
  * eToro REST API reachability          (is the venue reachable?)
  * journal writability                  (can we still persist?)
  * signal collector progress            (is the 1000-signal job advancing?)

On failure it raises an EMERGENCY notification and, for a dead/stalled
market monitor, restarts it.

Designed to be run as a periodic job (`--once`) rather than a daemon, so the
watchdog itself is supervised by the scheduler. That avoids the classic
"who watches the watchdog" bootstrapping problem.

    python watchdog.py --once            # one check, repair, exit
    python watchdog.py --once --dry-run  # report only, change nothing
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from etoro import build_client, EtoroError
from strategies.notifier import emergency, notify

ROOT = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(ROOT, "journal")
HEARTBEAT = os.path.join(JOURNAL, "market_monitor.heartbeat")
STATE = os.path.join(JOURNAL, "market_state.json")
SIGNALS = os.path.join(JOURNAL, "signals.jsonl")
LOG_PATH = os.path.join(ROOT, "logs", "market_monitor.out.log")
WATCHDOG_LOG = os.path.join(JOURNAL, "watchdog.jsonl")
WATCHDOG_STATE = os.path.join(JOURNAL, "watchdog_state.json")

DEFAULTS = {
    "monitor_heartbeat_max_age": 120.0,   # seconds; 30s throttle -> 2 min is generous
    "price_max_age": 120.0,
    "signal_max_age": 3600.0,             # signals advance every 15m; 1h is generous
    "restart_monitor": True,
}


def _now():
    return datetime.now(timezone.utc)


def _age_seconds(path):
    if not os.path.exists(path):
        return None
    return (_now() - datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)).total_seconds()


def _parse_iso_age(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds()
    except Exception:
        return None


def check_monitor(cfg):
    age = _age_seconds(HEARTBEAT)
    if age is not None:
        age = max(0.0, age)
    ok = age is not None and age <= cfg["monitor_heartbeat_max_age"]
    return ok, (f"market_monitor heartbeat {age:.0f}s old" if age is not None
                else "market_monitor heartbeat missing")


def check_price(cfg):
    if not os.path.exists(STATE):
        return False, "market_state.json missing"
    try:
        with open(STATE, encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception as exc:
        return False, f"market_state.json unreadable: {exc}"
    if not st.get("connected"):
        return False, "monitor reports connected=false"
    age = _parse_iso_age(st.get("price_time"))
    if age is None:
        age = _parse_iso_age(st.get("updated_at"))
    if age is None:
        return False, "no usable price timestamp"
    age = max(0.0, age)
    return age <= cfg["price_max_age"], f"price {age:.0f}s old"


def check_api():
    try:
        result = build_client().ping()
        return True, f"API ok ({result.get('status')}, {result.get('elapsed_ms')}ms)"
    except EtoroError as exc:
        return False, f"API unreachable: {exc}"
    except Exception as exc:
        return False, f"API check failed: {exc}"


def check_journal():
    os.makedirs(JOURNAL, exist_ok=True)
    probe = os.path.join(JOURNAL, ".write_probe")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True, "journal writable"
    except Exception as exc:
        return False, f"journal NOT writable: {exc}"


def check_collector(cfg):
    age = _age_seconds(SIGNALS)
    if age is None:
        return False, "signals.jsonl missing"
    return age <= cfg["signal_max_age"], f"last signal {age/60:.1f} min ago"


def _check_positions():
    """Every broker position must carry a stop loss. If not, shout."""
    try:
        from strategies import reconcile
        return reconcile.check_protection(build_client())
    except Exception as exc:
        return False, f"position check failed: {str(exc)[:120]}"


def restart_monitor():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            subprocess.Popen(
                [sys.executable, "market_monitor.py", "--tick-interval", "30"],
                cwd=ROOT, creationflags=flags, stdout=log, stderr=subprocess.STDOUT,
                close_fds=True,
            )
        return True, "market_monitor restarted"
    except Exception as exc:
        return False, f"restart failed: {exc}"


def run_once(cfg, dry_run=False):
    results = {}
    for name, fn in (("monitor", lambda: check_monitor(cfg)),
                     ("price", lambda: check_price(cfg)),
                     ("api", check_api),
                     ("journal", check_journal),
                     ("collector", lambda: check_collector(cfg)),
                     ("positions", lambda: _check_positions())):
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results[name] = {"ok": ok, "detail": detail}

    actions = []
    failures = [k for k, v in results.items() if not v["ok"]]

    if not results["monitor"]["ok"] and cfg["restart_monitor"]:
        if dry_run:
            actions.append("would restart market_monitor")
        else:
            ok, detail = restart_monitor()
            actions.append(detail)

    if failures:
        body = "Watchdog detected problems:\n" + "\n".join(
            f"  - {k}: {results[k]['detail']}" for k in failures
        )
        if dry_run:
            actions.append("would send EMERGENCY notification")
        else:
            notify(emergency(body), kind="emergency")
            actions.append("EMERGENCY notification queued")

    record = {
        "ts": _now().isoformat(),
        "healthy": not failures,
        "results": results,
        "actions": actions,
    }
    os.makedirs(JOURNAL, exist_ok=True)
    # Always keep the latest snapshot; only append history when something
    # happened, so a healthy system does not write 1,440 rows a day.
    with open(WATCHDOG_STATE, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    if failures or actions:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    return record


def main(argv=None):
    p = argparse.ArgumentParser(prog="watchdog.py", description="Health watchdog (service 5)")
    p.add_argument("--once", action="store_true", help="run a single check (for cron)")
    p.add_argument("--interval", type=float, default=30.0, help="loop interval seconds")
    p.add_argument("--dry-run", action="store_true", help="report only; no restart, no alerts")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    cfg = dict(DEFAULTS)

    if args.once:
        rec = run_once(cfg, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(rec, indent=2, default=str))
        else:
            print(f"watchdog: {'HEALTHY' if rec['healthy'] else 'PROBLEMS FOUND'}")
            for k, v in rec["results"].items():
                print(f"  [{'ok  ' if v['ok'] else 'FAIL'}] {k:10s} {v['detail']}")
            for a in rec["actions"]:
                print(f"  -> {a}")
        return 0

    while True:
        rec = run_once(cfg, dry_run=args.dry_run)
        print(f"[{rec['ts']}] {'healthy' if rec['healthy'] else 'PROBLEMS: ' + ', '.join(k for k,v in rec['results'].items() if not v['ok'])}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
