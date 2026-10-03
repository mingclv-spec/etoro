"""Authenticated WebSocket probe: auth -> subscribe -> live rates + real spread.

Credentials are read from the existing env file via etoro.config and are
NEVER printed. Prints only public market data (ask/bid/spread).
"""
import base64, json, os, socket, ssl, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from etoro.config import load_settings

HOST, PATH = "ws.etoro.com", "/ws"
INSTRUMENT = int(sys.argv[1]) if len(sys.argv) > 1 else 559
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0


def mask_frame(opcode, payload: bytes) -> bytes:
    mask = os.urandom(4)
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126); header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127); header += struct.pack("!Q", n)
    header += mask
    return bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise EOFError("closed")
        buf += c
    return buf


def read_frame(sock):
    b1, b2 = recv_exact(sock, 2)
    opcode, length = b1 & 0x0F, b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    return opcode, (recv_exact(sock, length) if length else b"")


settings = load_settings()
print("creds loaded:", bool(settings.api_key), bool(settings.user_key))

key = base64.b64encode(os.urandom(16)).decode()
req = (f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\n"
       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
       f"Sec-WebSocket-Version: 13\r\nOrigin: https://www.etoro.com\r\n\r\n")
ctx = ssl.create_default_context()
s = ctx.wrap_socket(socket.create_connection((HOST, 443), timeout=20), server_hostname=HOST)
s.settimeout(20)
s.sendall(req.encode())
head = b""
while b"\r\n\r\n" not in head:
    head += s.recv(4096)
print("handshake:", head.split(b"\r\n", 1)[0].decode(errors="replace"))

# 1) Authenticate (credentials never printed)
auth = {"id": os.urandom(16).hex(), "operation": "Authenticate",
        "data": {"userKey": settings.user_key, "apiKey": settings.api_key}}
s.sendall(mask_frame(0x1, json.dumps(auth).encode()))
print("sent Authenticate")

# 2) Subscribe
topics = [f"instrument:{INSTRUMENT}"]
s.sendall(mask_frame(0x1, json.dumps({
    "id": os.urandom(16).hex(), "operation": "Subscribe",
    "data": {"topics": topics, "snapshot": True}}).encode()))
print("subscribed:", topics)

deadline, rates = time.time() + SECONDS, 0
while time.time() < deadline:
    try:
        opcode, payload = read_frame(s)
    except (socket.timeout, EOFError) as e:
        print("read stop:", type(e).__name__)
        break
    if opcode == 0x9:
        s.sendall(mask_frame(0xA, payload))
        continue
    if opcode == 0x8:
        print("server close:", payload[:200]); break
    if opcode in (0x1, 0x2):
        text = payload.decode(errors="replace")
        if '"success":false' in text or "errorMessage" in text:
            print("  error frame:", text[:300])
            continue
        try:
            for m in json.loads(text).get("messages", []):
                c = json.loads(m.get("content", "{}"))
                ask = float(c.get("Ask", 0) or 0)
                bid = float(c.get("Bid", 0) or 0)
                if ask and bid:
                    sp = ask - bid
                    print(f"  LIVE ask={ask} bid={bid} spread={sp:.2f} ({sp/ask*100:.4f}%) {c.get('Date')}")
                    rates += 1
                else:
                    print("  msg type:", m.get("type"), text[:160])
        except Exception:
            print("  raw:", text[:200])
    if rates >= 6:
        break
print("live rate updates:", rates)
try: s.close()
except Exception: pass
