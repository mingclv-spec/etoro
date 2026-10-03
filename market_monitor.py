"""market_monitor - service 1 of 5.

Maintains the eToro WebSocket connection, reconnects on failure, and publishes:
  * journal/market_state.json        - latest price + spread + connection status
  * journal/ticks.jsonl              - throttled tick history (for backtests)
  * journal/market_monitor.heartbeat - liveness stamp for the watchdog

READ-ONLY. This service can never place an order.

    python market_monitor.py --seconds 45      # bounded test run
    python market_monitor.py                   # run until stopped (Ctrl+C)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import ssl
import struct
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from etoro.config import load_settings

ROOT = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "journal")
STATE_PATH = os.path.join(JOURNAL_DIR, "market_state.json")
TICKS_PATH = os.path.join(JOURNAL_DIR, "ticks.jsonl")
HEARTBEAT_PATH = os.path.join(JOURNAL_DIR, "market_monitor.heartbeat")

HOST, URL_PATH = "ws.etoro.com", "/ws"
SERVICE = "market_monitor"

_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def write_state(**fields):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(fields, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def touch_heartbeat():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fh:
        fh.write(now_iso())


def mask_frame(opcode, payload: bytes) -> bytes:
    mask = os.urandom(4)
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    header += mask
    return bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed by peer")
        buf += chunk
    return buf


def read_frame(sock):
    b1, b2 = recv_exact(sock, 2)
    opcode, length = b1 & 0x0F, b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    return opcode, (recv_exact(sock, length) if length else b"")


def connect():
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {URL_PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\nOrigin: https://www.etoro.com\r\n\r\n"
    )
    ctx = ssl.create_default_context()
    raw = socket.create_connection((HOST, 443), timeout=20)
    sock = ctx.wrap_socket(raw, server_hostname=HOST)
    sock.settimeout(30)
    sock.sendall(req.encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += sock.recv(4096)
    if "101" not in head.split(b"\r\n", 1)[0].decode(errors="replace"):
        raise ConnectionError("websocket upgrade rejected")
    return sock


def authenticate(sock, settings):
    payload = {
        "id": os.urandom(16).hex(),
        "operation": "Authenticate",
        "data": {"userKey": settings.user_key, "apiKey": settings.api_key},
    }
    sock.sendall(mask_frame(0x1, json.dumps(payload).encode()))


def subscribe(sock, instrument_id):
    payload = {
        "id": os.urandom(16).hex(),
        "operation": "Subscribe",
        "data": {"topics": [f"instrument:{instrument_id}"], "snapshot": True},
    }
    sock.sendall(mask_frame(0x1, json.dumps(payload).encode()))


def append_tick(record):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(TICKS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def stream(sock, instrument_id, tick_interval, seconds_deadline):
    """Read frames until an error, the deadline, or a stop signal."""
    last_write = 0.0
    ticks = 0
    while not _stop:
        if seconds_deadline and time.time() >= seconds_deadline:
            return ticks, "deadline"
        opcode, payload = read_frame(sock)
        if opcode == 0x9:
            sock.sendall(mask_frame(0xA, payload))
            continue
        if opcode == 0x8:
            raise ConnectionError("server sent close frame")
        if opcode not in (0x1, 0x2):
            continue
        text = payload.decode(errors="replace")
        if "errorMessage" in text:
            raise ConnectionError(f"stream error: {text[:160]}")
        try:
            messages = json.loads(text).get("messages", [])
        except Exception:
            continue
        for m in messages:
            try:
                c = json.loads(m.get("content", "{}"))
            except Exception:
                continue
            ask = float(c.get("Ask") or 0)
            bid = float(c.get("Bid") or 0)
            if not ask or not bid:
                continue
            ticks += 1
            spread = ask - bid
            spread_pct = spread / ask * 100.0 if ask else 0.0
            stamp = c.get("Date") or now_iso()
            write_state(
                service=SERVICE,
                connected=True,
                instrument_id=instrument_id,
                ask=ask,
                bid=bid,
                spread=round(spread, 4),
                spread_pct=round(spread_pct, 5),
                price_time=stamp,
                updated_at=now_iso(),
            )
            touch_heartbeat()
            if time.time() - last_write >= tick_interval:
                append_tick({
                    "ts": now_iso(), "price_time": stamp, "instrument_id": instrument_id,
                    "ask": ask, "bid": bid, "spread": round(spread, 4),
                    "spread_pct": round(spread_pct, 5),
                })
                last_write = time.time()
    return ticks, "stopped"


def run(instrument_id, seconds=None, tick_interval=5.0, max_backoff=60.0):
    settings = load_settings()
    deadline = (time.time() + seconds) if seconds else None
    backoff = 1.0
    total_ticks = 0
    attempt = 0

    while not _stop:
        if deadline and time.time() >= deadline:
            break
        attempt += 1
        try:
            print(f"[{SERVICE}] connect attempt {attempt}", flush=True)
            sock = connect()
            authenticate(sock, settings)
            subscribe(sock, instrument_id)
            backoff = 1.0
            print(f"[{SERVICE}] streaming instrument:{instrument_id}", flush=True)
            ticks, why = stream(sock, instrument_id, tick_interval, deadline)
            total_ticks += ticks
            print(f"[{SERVICE}] stream ended ({why}) after {ticks} ticks", flush=True)
            try:
                sock.close()
            except Exception:
                pass
            if why == "deadline":
                break
        except Exception as exc:
            write_state(service=SERVICE, connected=False, instrument_id=instrument_id,
                        error=str(exc)[:200], updated_at=now_iso())
            print(f"[{SERVICE}] connection lost: {exc} - reconnecting in {backoff:.0f}s", flush=True)
            slept = 0.0
            while slept < backoff and not _stop:
                time.sleep(min(0.5, backoff - slept))
                slept += 0.5
                if deadline and time.time() >= deadline:
                    return total_ticks
            backoff = min(backoff * 2.0, max_backoff)

    write_state(service=SERVICE, connected=False, instrument_id=instrument_id,
                stopped_at=now_iso())
    return total_ticks


def main(argv=None):
    p = argparse.ArgumentParser(prog="market_monitor.py", description="eToro WebSocket market monitor (read-only)")
    p.add_argument("--instrument", type=int, default=559, help="instrumentId (default 559 = GOLD)")
    p.add_argument("--seconds", type=float, default=None, help="bounded test run length")
    p.add_argument("--tick-interval", type=float, default=5.0, help="seconds between journaled ticks")
    p.add_argument("--max-backoff", type=float, default=60.0)
    args = p.parse_args(argv)

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except Exception:
        pass

    ticks = run(args.instrument, args.seconds, args.tick_interval, args.max_backoff)
    print(f"[{SERVICE}] total ticks this run: {ticks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
