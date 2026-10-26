"""Live test: long-running `claude -p` stream-json session in testproj, inject prompts via pipe.
Needs sidecar running (python vscode_bridge/sidecar.py). Prints evidence."""
import json, subprocess, sys, time
from pathlib import Path
B = Path(__file__).resolve().parents[1] / "vscode_bridge"
sys.path.insert(0, str(B.parent))
from vscode_bridge import messaging

proj = B / "testproj"
p = subprocess.Popen(["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                      "--dangerously-skip-permissions"], cwd=proj, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
lines = []
import threading
def rd():
    for l in p.stdout: lines.append((time.time(), l.strip()))
threading.Thread(target=rd, daemon=True).start()
def user(t): p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": t}}) + "\n"); p.stdin.flush()
user("Reply with exactly: READY")
# wait for sessionstart in events.jsonl
time.sleep(25)
ev = [json.loads(l) for l in (B / "events.jsonl").read_text(encoding="utf-8").splitlines()]
ss = [e["body"] for e in ev if e["kind"] == "SessionStart"][-1]
print("socket", ss["socket"])
t0 = time.time()
messaging.send_prompt(ss["socket"], ss["token"], "[voice] Reply with exactly: IDLE-INJECT-OK")
time.sleep(25)
print("--- idle test: lines after", t0)
for t, l in lines:
    if t >= t0 and ("IDLE-INJECT-OK" in l): print("GOT", l[:300])
# busy test
user("Run the bash command `ping -n 25 127.0.0.1` then say DONE-BUSY")
time.sleep(6)
t1 = time.time()
messaging.send_prompt(ss["socket"], ss["token"], "[voice] Also after that, say exactly: BUSY-INJECT-OK")
time.sleep(60)
print("--- busy test")
for t, l in lines:
    if t >= t1 and ("BUSY-INJECT-OK" in l or "DONE-BUSY" in l): print(round(t - t1, 1), l[:300])
p.stdin.close(); time.sleep(2); p.kill()
json.dump([l for _, l in lines], open(B / "test_messaging_stream.json", "w"))
