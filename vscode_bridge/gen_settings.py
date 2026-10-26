"""Hooks block for ~/.claude/settings.json (command hooks; no secret in settings).
  python gen_settings.py                 print the block
  python gen_settings.py --apply FILE    MERGE into FILE (appends to existing arrays, replaces only our own earlier entries)"""
import json, sys
from pathlib import Path
HOOK = (Path(__file__).parent / "hook.py").as_posix()
MARK = "vscode_bridge/hook.py"


def entry(ep, timeout, matcher=None):
    e = {"hooks": [{"type": "command", "command": f'python "{HOOK}" {ep}', "timeout": timeout}]}
    if matcher: e = {"matcher": matcher, **e}
    return e


BLOCK = {
    "SessionStart": [entry("session_start", 5)],
    "SessionEnd": [entry("session_end", 5)],
    "Stop": [entry("stop", 5)],
    "PreToolUse": [entry("pretooluse", 60, "AskUserQuestion")],
    "PermissionRequest": [entry("permission", 60)],
}


def merge(settings):
    hooks = settings.setdefault("hooks", {})
    for ev, entries in BLOCK.items():
        keep = [e for e in hooks.get(ev, []) if MARK not in json.dumps(e)]
        hooks[ev] = keep + entries
    return settings


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--apply":
        f = Path(sys.argv[2]); d = merge(json.loads(f.read_text(encoding="utf-8")))
        f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(json.dumps({"hooks": BLOCK}, indent=2))
