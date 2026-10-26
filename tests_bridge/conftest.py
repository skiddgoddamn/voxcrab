"""Isolate the bridge from the real ~/.voice-bridge (a running widget owns the real sidecar port)."""
import json, os, socket, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_home = Path(tempfile.mkdtemp(prefix='vb-home-'))
os.environ.setdefault('VB_REAL_HOME', str(Path.home()))
os.environ['USERPROFILE'] = os.environ['HOME'] = str(_home)  # hook.py subprocesses use Path.home() too
from vscode_bridge import sidecar as S  # noqa: E402

S.BASE = _home / '.voice-bridge'; S.CFG = S.BASE / 'sidecar.json'; S.REG = S.BASE / 'sessions'
S.REG.mkdir(parents=True, exist_ok=True)
with socket.socket() as _s:
    _s.bind(('127.0.0.1', 0)); _port = _s.getsockname()[1]
S.CFG.write_text(json.dumps({'port': _port, 'token': 'test-token'}))

# live spike scripts (run manually: python tests_bridge/test_hooks.py), not pytest tests
collect_ignore = ['test_messaging.py', 'test_hooks.py']
