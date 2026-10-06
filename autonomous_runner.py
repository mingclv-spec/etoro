"""10-minute autonomous eToro runner."""
from __future__ import annotations
import json, os, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "autonomous_state.json"
LOG_FILE = ROOT / "autonomous.log"
PYTHON = sys.executable
SYMBOLS = ("GOLD", "BTC")

def load_local_env():
    try:
        from etoro.config import load_env_values
        values = load_env_values(root=ROOT)
        for key, value in values.items():
            if key.startswith(("ETORO_", "TELEGRAM_")) and key not in os.environ and value:
                os.environ[key] = value
    except Exception:
        pass

def log(message):
    line = time.strftime("%Y-%m-%d %H:%M:%S %z") + " " + message
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)

def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("TELEGRAM not configured; result kept in log")
        return False
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message[:4000]}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status == 200
    except Exception as exc:
        log("TELEGRAM send failed: " + str(exc))
        return False

def run_signal(symbol):
    cmd = [PYTHON, "-m", "etoro.cli", "--account", "real", "signal", "--symbol", symbol, "--json"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    output = result.stdout + ("\n" + result.stderr if result.stderr else "")
    analysis = None
    marker = "RAW ANALYSIS\n"
    if marker in output:
        try:
            analysis = json.loads(output.split(marker, 1)[1].strip()).get("analysis")
        except Exception:
            pass
    return result.returncode, output, analysis

def state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"open_strategies": {}, "last_run": None}

def save_state(value):
    STATE_FILE.write_text(json.dumps(value, indent=2), encoding="utf-8")

def format_result(symbol, output, analysis):
    if not analysis:
        return "eToro AutoTrader\n" + symbol + ": ERROR / NO ANALYSIS\n" + output[-1200:]
    action = analysis.get("action") or (analysis.get("signal") or {}).get("action") or "WAIT"
    reason = analysis.get("reason") or (analysis.get("signal") or {}).get("reason") or ""
    price = analysis.get("price", analysis.get("live_price"))
    stop = analysis.get("stop") or (analysis.get("signal") or {}).get("stop")
    pm = analysis.get("polymarket") or {}
    pm_line = ""
    if symbol == "BTC":
        pm_line = "\nPolymarket: " + str(pm.get("up_probability")) + " UP / " + str(pm.get("down_probability")) + " DOWN | " + str(pm.get("bias")) + " | available=" + str(pm.get("available"))
    return "eToro AutoTrader\n" + symbol + " | " + str(action) + "\nPrice: " + str(price) + "\nStop: " + str(stop) + "\nReason: " + str(reason) + pm_line

def maybe_order(symbol, analysis, st):
    if not analysis:
        return None
    action = analysis.get("action")
    if action not in {"BUY", "SELL"}:
        return None
    key = symbol.upper()
    if st["open_strategies"].get(key):
        return symbol + ": " + action + " detected, but existing strategy position state blocks another entry."
    live_enabled = os.getenv("ETORO_AUTONOMOUS_LIVE", "false").strip().lower() in {"1","true","yes","on"}
    if not live_enabled:
        return symbol + ": " + action + " detected; autonomous live execution is OFF."
    amount, leverage = (20, 1) if symbol == "BTC" else (50, 20)
    stop = analysis.get("stop") or (analysis.get("signal") or {}).get("stop")
    if stop is None:
        return symbol + ": " + action + " blocked — no calculated stop-loss."
    cmd = [PYTHON, "-m", "etoro.cli", "--account", "real", "order", "--symbol", symbol, "--side", action, "--amount", str(amount), "--leverage", str(leverage), "--stop-loss", str(stop), "--yes", "--live"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        st["open_strategies"][key] = {"opened_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, "amount": amount, "leverage": leverage, "stop": stop}
        save_state(st)
        return symbol + ": LIVE ORDER SENT — " + action + " $" + str(amount) + " @ " + str(leverage) + "x, stop " + str(stop)
    return symbol + ": LIVE ORDER FAILED\n" + (result.stdout + result.stderr)[-1800:]

def main():
    load_local_env()
    st = state()
    messages = []
    log("AUTONOMOUS RUN START")
    for symbol in SYMBOLS:
        try:
            rc, output, analysis = run_signal(symbol)
            msg = format_result(symbol, output, analysis)
            order_msg = maybe_order(symbol, analysis, st)
            if order_msg:
                msg += "\n" + order_msg
            if rc != 0:
                msg += "\nCLI return code: " + str(rc)
            messages.append(msg)
            log(symbol + ": rc=" + str(rc))
        except Exception as exc:
            messages.append("eToro AutoTrader\n" + symbol + ": EXCEPTION\n" + str(exc))
            log(symbol + ": exception=" + repr(exc))
    st["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_state(st)
    telegram_send("\n\n".join(messages))
    log("AUTONOMOUS RUN END")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
