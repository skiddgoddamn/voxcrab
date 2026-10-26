"""Command hook for every bridge event: python hook.py <endpoint>  (session_start|session_end|stop|permission|pretooluse)
Fast and silent: always exits 0, prints to stdout only a decision JSON received from the sidecar (permission/pretooluse).
Does nothing if the widget never ran (no ~/.voice-bridge/sidecar.json) or VOICE_BRIDGE_OFF is set (widget's own claude -p calls).
Secrets: the messaging token goes only to the ACL-restricted ~/.voice-bridge/sessions/<sid>.json and the loopback sidecar."""
import http.client, json, os, sys
from pathlib import Path

HOLD_EP = ("permission", "pretooluse")


def main():
    ep = sys.argv[1]
    if os.environ.get("VOICE_BRIDGE_OFF"): return
    base = Path.home() / ".voice-bridge"
    cfg = json.loads((base / "sidecar.json").read_text())
    body = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    sid = body.get("session_id")
    if ep == "pretooluse" and body.get("tool_name") != "AskUserQuestion": return
    if ep == "session_start":
        body["socket"] = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        body["token"] = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN")
        if sid and body["socket"] and body["token"]:
            reg = base / "sessions"; reg.mkdir(exist_ok=True)
            (reg / f"{sid}.json").write_text(json.dumps({k: body.get(k) for k in ("session_id", "cwd", "transcript_path", "socket", "token")}))
    elif ep == "session_end" and sid:
        try: (base / "sessions" / f"{sid}.json").unlink()
        except OSError: pass
    import time
    if time.time() - (base / "alive").stat().st_mtime > 12: return  # sidecar down: skip the (slow on Windows) refused connect
    c = http.client.HTTPConnection("127.0.0.1", cfg["port"], timeout=1.5)
    c.connect()  # refused instantly when the sidecar is down
    c.sock.settimeout(float(cfg.get("hold_sec", 45)) + 10 if ep in HOLD_EP else 2)
    c.request("POST", "/" + ep, json.dumps(body), {"X-Voice-Token": cfg["token"], "Content-Type": "application/json"})
    out = c.getresponse().read().decode("utf-8", "replace").strip()
    if out and ep in HOLD_EP: sys.stdout.write(out)


try: main()
except BaseException: pass
sys.exit(0)
