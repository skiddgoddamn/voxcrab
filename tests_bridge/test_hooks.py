"""Live hook test through `claude -p` in testproj with in-process Sidecar answering by 'voice'."""
import subprocess, sys, threading, time, json
from pathlib import Path
B = Path(__file__).resolve().parents[1] / "vscode_bridge"
sys.path.insert(0, str(B.parent))
from vscode_bridge.sidecar import Sidecar

mode = {"perm": "allow", "ask": "Blue"}
def on_event(e):
    k, b = e["kind"], e["body"]
    print("EVENT", k, {x: b.get(x) for x in ("tool_name", "message", "notification_type", "last_assistant_message") if b.get(x)}, flush=True)
    if k == "pending_permission" and mode["perm"]:
        print("  tool_input", b["tool_input"], "mode", b.get("permission_mode"))
        sc.answer(b["id"], behavior=mode["perm"], message="no way")
    if k == "pending_ask" and mode["ask"]:
        qs = b["tool_input"]["questions"]
        sc.answer(b["id"], answers={q["question"]: mode["ask"] for q in qs})
sc = Sidecar(on_event=on_event, hold_timeout=8).start()
def run(prompt, extra=()):
    r = subprocess.run(["claude", "-p", prompt, *extra], cwd=B / "testproj", capture_output=True, text=True, encoding="utf-8", timeout=180)
    print("OUT:", r.stdout.strip()[:400]); return r
import sys as _s; mode["perm"]="allow"
print("== AskUserQuestion"); mode["ask"] = "Blue"
run("Use the AskUserQuestion tool to ask me my favorite color with options Red, Blue, Green (header Color). Then tell me which answer you got.", ["--tools","AskUserQuestion,Bash"])
(B/"testproj"/"allowed.txt").unlink(missing_ok=True)
