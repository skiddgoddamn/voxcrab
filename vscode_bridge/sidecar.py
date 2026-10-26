"""Sidecar: local HTTP server receiving Claude Code hooks + registry + voice decision hold.

API for the widget:
    from vscode_bridge.sidecar import Sidecar
    sc = Sidecar(on_event=cb).start()   # cb(event: dict) on every hook event
    sc.sessions                         # {session_id: {cwd, transcript_path, socket, token, last_assistant_message, ...}}
    sc.pending                          # {id: {kind, session_id, payload}} awaiting a voice answer
    sc.answer(id, **kw)                 # resolve a pending AskUserQuestion / PermissionRequest
Config (port/token) lives in ~/.voice-bridge/sidecar.json so hook settings can be static.
"""
import json, os, secrets, subprocess, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path.home() / ".voice-bridge"
CFG = BASE / "sidecar.json"
REG = BASE / "sessions"  # one file per live session (messaging socket+token): per-user, ACL-restricted dir
DEFAULT_PORT = 47821


def lock_dir(d):
    """Windows: drop inherited ACEs, grant only the current user (secrets live here)."""
    if os.name != "nt": os.chmod(d, 0o700); return
    try:
        subprocess.run(["icacls", str(d), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:(OI)(CI)F"],
                       capture_output=True, timeout=10, creationflags=0x08000000)
    except Exception: pass


def load_cfg():
    if CFG.exists():
        return json.loads(CFG.read_text())
    BASE.mkdir(parents=True, exist_ok=True); REG.mkdir(exist_ok=True); lock_dir(BASE)
    cfg = {"port": DEFAULT_PORT, "token": secrets.token_urlsafe(24)}
    CFG.write_text(json.dumps(cfg))
    return cfg


class Sidecar:
    def __init__(self, on_event=None, hold_timeout=45.0, log_path=None, hold_enabled=None):
        """hold_enabled: callable -> bool; when False permission/ask requests return at once (normal UI prompt)."""
        self.cfg = load_cfg()
        self.hold_enabled = hold_enabled or (lambda: True)
        self.on_event = on_event or (lambda e: None)
        self.hold_timeout = hold_timeout = min(float(hold_timeout), 50.0)  # hook timeout in settings is 60s
        if self.cfg.get("hold_sec") != hold_timeout:
            self.cfg["hold_sec"] = hold_timeout; CFG.write_text(json.dumps(self.cfg))
        self.sessions, self.pending, self.log = {}, {}, []
        self.log_path = log_path
        self._lock = threading.Lock()
        self._httpd = None
        self._load_registry()

    def _load_registry(self, max_age=24 * 3600):
        """Sessions registered by the command hook while the sidecar was down."""
        REG.mkdir(parents=True, exist_ok=True)
        for f in REG.glob("*.json"):
            try:
                if time.time() - f.stat().st_mtime > max_age: f.unlink(); continue
                d = json.loads(f.read_text(encoding="utf-8"))
                self.sessions.setdefault(d["session_id"], {}).update({k: v for k, v in d.items() if k != "session_id"})
            except Exception: pass

    def drop_session(self, sid):
        self.sessions.pop(sid, None)
        try: (REG / f"{sid}.json").unlink()
        except OSError: pass

    def live_sessions(self):
        return {k: v for k, v in self.sessions.items() if v.get("socket") and v.get("token")}

    def answer(self, pid, **kw):
        """Ask: answer(pid, answers={"question text": "label"}). Permission: answer(pid, behavior="allow"|"deny", message="")."""
        with self._lock:
            p = self.pending.get(pid)
        if not p:
            return False
        p["result"] = kw
        p["event"].set()
        return True

    def start(self):
        sc = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                if self.headers.get("X-Voice-Token") != sc.cfg["token"]:
                    self.send_response(403); self.end_headers(); return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                out = sc._handle(self.path, body)
                data = json.dumps(out).encode() if out is not None else b""
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.cfg["port"]), H)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._beat, daemon=True).start()
        return self

    def _beat(self):
        """Heartbeat file: hooks skip the connect entirely (Windows refused-connect is slow) when it is stale."""
        f = BASE / "alive"
        while self._httpd:
            try: f.write_text(str(time.time()))
            except OSError: pass
            time.sleep(4)

    def stop(self):
        if self._httpd:
            h, self._httpd = self._httpd, None
            h.shutdown(); h.server_close()
            try: (BASE / "alive").unlink()
            except OSError: pass

    def _record(self, kind, body):
        body = {k: v for k, v in body.items() if k not in ("token", "socket", "env")}  # never log/emit secrets
        ev = {"kind": kind, "t": time.time(), "body": body}
        self.log.append(ev)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        try: self.on_event(ev)
        except Exception: pass

    def _handle(self, path, body):
        sid = body.get("session_id")
        s = self.sessions.setdefault(sid, {}) if sid else {}
        for k in ("cwd", "transcript_path"):
            if body.get(k): s[k] = body[k]
        if path == "/session_start":  # posted by the command hook (it sees env)
            s.update(socket=body.get("socket"), token=body.get("token"), env_seen=body.get("env"))
            self._record("SessionStart", body)
        elif path == "/session_end":
            self.drop_session(sid); self._record("SessionEnd", body)
        elif path == "/stop":
            s["last_assistant_message"] = body.get("last_assistant_message")
            self._record("Stop", body)
        elif path == "/notification":
            self._record("Notification", body)
        elif path == "/pretooluse":
            if body.get("tool_name") == "AskUserQuestion":
                return self._hold("ask", sid, body, self._ask_out)
            self._record("PreToolUse", body)
        elif path == "/permission":
            return self._hold("permission", sid, body, self._perm_out)
        return None

    def _hold(self, kind, sid, body, build):
        try: enabled = self.hold_enabled()
        except Exception: enabled = False
        if not enabled: return None  # widget off / not listening: normal UI prompt immediately
        pid = uuid.uuid4().hex[:8]
        p = {"kind": kind, "session_id": sid, "payload": body, "event": threading.Event(), "result": None}
        with self._lock: self.pending[pid] = p
        self._record("pending_" + kind, {"id": pid, **body})
        p["event"].wait(self.hold_timeout)
        with self._lock: self.pending.pop(pid, None)
        if not p["result"]:
            self._record("timeout_" + kind, {"id": pid})
            return None  # empty 200 -> normal UI prompt
        return build(body, p["result"])

    @staticmethod
    def _ask_out(body, r):
        ti = dict(body["tool_input"]); ti["answers"] = r["answers"]
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": ti}}

    @staticmethod
    def _perm_out(body, r):
        d = {"behavior": r["behavior"]}
        if r["behavior"] == "deny": d["message"] = r.get("message", "Denied by voice")
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


if __name__ == "__main__":
    import sys
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    sc = Sidecar(hold_timeout=hold, log_path=Path(__file__).with_name("events.jsonl"),
                 on_event=lambda e: print(e["kind"], json.dumps(e["body"], ensure_ascii=False)[:300], flush=True)).start()
    print("sidecar on", sc.cfg["port"], flush=True)
    while True: time.sleep(3600)
