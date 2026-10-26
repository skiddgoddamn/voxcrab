"""Client for Claude Code cross-session messaging inbox (Windows named pipe, auth line first).

    send_prompt(socket, token, "text")  -> None (raises OSError if pipe is gone)
Wire format (from claude.exe's own recipe): line1 {"type":"auth","token":T}, line2 {"type":"user","message":{"role":"user","content":TEXT}}
Idle session: starts a new turn. Busy: read between tool calls.
"""
import json, os


def send_prompt(socket, token, text):
    sock = socket[4:] if socket.startswith("uds:") else socket
    payload = (json.dumps({"type": "auth", "token": token}) + "\n" +
               json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n").encode("utf-8")
    if os.name == "nt":
        with open(sock, "r+b", buffering=0) as f:
            f.write(payload)
    else:
        import socket as s
        c = s.socket(s.AF_UNIX); c.connect(sock); c.sendall(payload); c.close()


def send_to_session(sidecar, session_id, text):
    s = sidecar.sessions[session_id]
    send_prompt(s["socket"], s["token"], text)
