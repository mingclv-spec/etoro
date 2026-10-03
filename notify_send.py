"""Drains the notification queue and prints it for OpenClaw to deliver.

Wired to an OpenClaw automation job with --announce --channel telegram.
Prints NO_REPLY when the queue is empty so the job stays silent.

NOTE: stdout is forced to UTF-8. The Windows console defaults to cp1252 and
would raise UnicodeEncodeError on the status emoji, which would break delivery.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from strategies.notifier import drain


def main():
    pending = drain()
    if not pending:
        print("NO_REPLY")
        return 0
    print("\n\n".join(r["text"] for r in pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
