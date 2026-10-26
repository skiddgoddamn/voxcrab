"""Offline tests: hook.py behaviour with sidecar down/up, hold_enabled, Bridge answer logic (no audio, no claude)."""
import json, os, subprocess, sys, threading, time, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vscode_bridge import sidecar as S
from bridge_voice import Bridge

HOOK = ROOT / "vscode_bridge" / "hook.py"
def run_hook(ep, body, **env):
    t = time.time()
    r = subprocess.run([sys.executable, str(HOOK), ep], input=json.dumps(body), capture_output=True, text=True, env={**os.environ, **env})
    return r.returncode, r.stdout, time.time() - t

class FakeUI:
    def say(self, *a, **k): pass
    def hide_bubble(self): pass
class FakeW:
    cfg = {"bridge_hold_sec": 5, "bridge_hold": True, "speak_mode": "immediate"}
    ready = True; quit = False; ui = FakeUI()
    def __init__(self): self.said = []
    def say_event(self, t, ask=False, extra=None): self.said.append((t, ask, extra or {}))

def test_hook_silent_when_sidecar_down():
    (S.BASE / "alive").unlink(missing_ok=True)
    rc, out, dt = run_hook("permission", {"session_id": "t1", "tool_name": "Bash"})
    assert rc == 0 and out == "" and dt < 1.5

def test_hook_off_env():
    rc, out, _ = run_hook("permission", {}, VOICE_BRIDGE_OFF="1")
    assert rc == 0 and out == ""

def test_permission_roundtrip_and_no_hold_when_disabled():
    w = FakeW(); b = Bridge(w).start(); time.sleep(0.5)
    try:
        res = {}
        th = threading.Thread(target=lambda: res.update(zip(("rc", "out", "dt"), run_hook("permission", {"session_id": "t2", "cwd": "D:/x/proj", "tool_name": "Bash", "tool_input": {"command": "ls"}}))))
        th.start(); time.sleep(1.5)
        assert w.said and "proj" in w.said[0][0] and w.said[0][1] is True
        assert b.try_answer("да, разрешаю") is True
        th.join(10); assert json.loads(res["out"])["hookSpecificOutput"]["decision"]["behavior"] == "allow"
        w.cfg["speak_mode"] = "on_request"
        rc, out, dt = run_hook("permission", {"session_id": "t2", "tool_name": "Bash", "tool_input": {}})
        assert out == "" and dt < 1.5  # not held
        w.cfg["speak_mode"] = "immediate"
        assert b.try_answer("привет") is False
    finally:
        b.stop()

def test_ask_two_questions():
    w = FakeW(); b = Bridge(w).start(); time.sleep(0.5)
    try:
        qs = [{"question": "Цвет?", "options": [{"label": "Красный"}, {"label": "Синий"}]}, {"question": "Размер?", "options": [{"label": "S"}, {"label": "M"}]}]
        res = {}
        th = threading.Thread(target=lambda: res.update(zip(("rc", "out", "dt"), run_hook("pretooluse", {"session_id": "t3", "tool_name": "AskUserQuestion", "tool_input": {"questions": qs}}))))
        th.start(); time.sleep(1.5)
        assert b.try_answer("синий"); assert b.try_answer("второй")
        th.join(10); ans = json.loads(res["out"])["hookSpecificOutput"]["updatedInput"]["answers"]
        assert ans == {"Цвет?": "Синий", "Размер?": "M"}
    finally:
        b.stop()

def test_no_secret_in_events():
    seen = []
    sc = S.Sidecar(on_event=seen.append).start(); time.sleep(0.3)
    try:
        run_hook("session_start", {"session_id": "t4", "cwd": "."}, CLAUDE_CODE_MESSAGING_SOCKET="pipe-x", CLAUDE_CODE_MESSAGING_TOKEN="SECRET123")
        time.sleep(0.5)
        assert seen and "SECRET123" not in json.dumps(seen)
        assert sc.sessions["t4"]["token"] == "SECRET123"
    finally:
        sc.drop_session("t4"); sc.stop()

def test_confirm_flow(monkeypatch=None):
    import bridge_voice as bv
    w = FakeW(); w.cfg = dict(FakeW.cfg, confirm_mode="unclear"); w.set_state = lambda s: None
    b = Bridge(w)
    b.sc.sessions.clear(); b.sc.sessions["c1"] = {"cwd": "D:/x/alpha", "socket": "s", "token": "t"}
    sent = []; b.dispatch = lambda sid, text: sent.append((sid, text)) or True
    bv.haiku = lambda *a, **k: '{"target": 1, "confidence": 0.9, "clear": true, "restatement": "починить тест"}'
    assert b.send_task("почини тест", stt_unclear=False) is True and sent == [("c1", "почини тест")]
    sent.clear()
    assert b.send_task("почини тэст", stt_unclear=True) is True and not sent
    assert "Отправить в alpha?" in w.said[-1][0] and b.dlg.state == "awaiting_confirmation"
    assert b.try_answer("да") is False  # not a permission prompt: the dialog handles it in send_task
    assert b.send_task("да") is True and sent == [("c1", "почини тэст")]
    b.send_task("мумбль", stt_unclear=True); assert b.send_task("нет") is True and not sent[1:]
    b.send_task("мумбль", stt_unclear=True); assert b.send_task("почини тест в alpha", stt_unclear=True) is True
    assert b.send_task("да") and sent[-1][1] == "почини тест в alpha"
    w.cfg["confirm_mode"] = "never"; sent.clear()
    b.send_task("мумбль", stt_unclear=True); assert len(sent) == 1
