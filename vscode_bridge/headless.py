"""New / resumed Claude Code sessions run headless (`claude -p`) so they get a real session id and a transcript
in ~/.claude/projects/<encoded-cwd>/<id>.jsonl, then opened in the VS Code panel via the extension's URI handler.

Findings (extension anthropic.claude-code 2.1.310, extension.js):
- handleUri '/open': params `session` (uuid, validated) and `prompt`; runs claude-vscode.primaryEditor.open(session, prompt).
  `prompt` only PRE-FILLS the input box (webview: setInputText) - it is never submitted, hence "didn't register".
  `session` activates an existing session from the workspace's transcript list (activateSessionFromServer).
- The list hides transcripts whose entrypoint is sdk-cli / sdk-ts / sdk-py (function xP). `claude -p` writes
  entrypoint "sdk-cli" (CLAUDE_CODE_ENTRYPOINT=cli is coerced back to sdk-cli in print mode), so we set
  CLAUDE_CODE_ENTRYPOINT=claude-vscode (configurable) to make the session listable/openable in VS Code.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
PROJECTS = Path.home() / '.claude' / 'projects'


def encode_cwd(cwd: str) -> str:
    """Directory name the CLI uses under ~/.claude/projects (every non-alphanumeric char -> '-')."""
    return re.sub(r'[^A-Za-z0-9]', '-', str(cwd))


def transcript_path(cwd: str, sid: str) -> Path:
    return PROJECTS / encode_cwd(cwd) / f'{sid}.jsonl'


def build_cmd(task: str, model: str | None = None, resume: str | None = None, session_id: str | None = None, exe: str | None = None) -> list[str]:
    cmd = [exe or shutil.which('claude') or 'claude', '-p', '--output-format', 'json']
    if model: cmd += ['--model', model]
    if resume: cmd += ['--resume', resume]
    elif session_id: cmd += ['--session-id', session_id]
    return cmd + [task]


def run(task: str, cwd: str, model: str | None = None, resume: str | None = None, session_id: str | None = None, entrypoint: str = 'claude-vscode',
        timeout: float = 1800, runner=subprocess.run) -> dict:
    """Run one headless turn. -> {'session_id', 'result', 'is_error', 'transcript'}. Raises on spawn/parse failure.
    Hooks stay ON (voice permission prompts and the Stop event still reach the widget)."""
    env = {k: v for k, v in os.environ.items() if k not in ('CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'VOICE_BRIDGE_OFF')}
    if entrypoint: env['CLAUDE_CODE_ENTRYPOINT'] = entrypoint
    else: env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    r = runner(build_cmd(task, model, resume, session_id), cwd=cwd, capture_output=True, text=True, encoding='utf-8',
               timeout=timeout, env=env, creationflags=NO_WINDOW)
    try: j = json.loads(r.stdout)
    except (ValueError, TypeError): raise RuntimeError(f'claude -p failed (rc={r.returncode}): {(r.stderr or r.stdout or "")[:200]}')
    sid = j.get('session_id') or resume or session_id
    return {'session_id': sid, 'result': str(j.get('result') or ''), 'is_error': bool(j.get('is_error')),
            'transcript': str(transcript_path(cwd, sid)) if sid else None}


def vscode_uri(sid: str) -> str:
    return 'vscode://anthropic.claude-code/open?session=' + quote(sid, safe='')


def open_in_vscode(cwd: str, sid: str, wait: float = 4.0, popen=subprocess.Popen, start=None) -> bool:
    """Focus/open the VS Code window of cwd, then ask the extension to open the session tab.
    The URI goes to the focused window, whose workspace must be cwd for the session to be found."""
    code = shutil.which('code')
    if not code: return False
    popen([code, cwd], creationflags=NO_WINDOW)
    time.sleep(wait)
    (start or os.startfile)(vscode_uri(sid))
    return True
