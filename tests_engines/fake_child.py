"""Stand-in for web_child.py: speaks the same JSON-lines protocol. Mode comes from argv[1]: ok | error | die | hang."""
import json, sys

mode = sys.argv[1]


def out(o): sys.stdout.write(json.dumps(o) + '\n'); sys.stdout.flush()


out({'ev': 'ready'})
if mode == 'die': sys.exit(3)
if mode == 'hang':
    sys.stdin.read(); sys.exit(0)
out({'ev': 'page'})
for line in sys.stdin:
    m = json.loads(line)
    if m['cmd'] == 'init':
        if mode == 'error': out({'ev': 'error', 'msg': 'Cubism Core не загрузился'})
        else: out({'ev': 'loaded', 'info': {'mouth': True, 'blink': True}})
    elif m['cmd'] == 'emotion': out({'ev': 'echo', 'emotion': m['name']})
    elif m['cmd'] in ('serve', 'place', 'clickthrough'): out({'ev': 'echo', 'cmd': m['cmd'], 'args': m})
    elif m['cmd'] == 'unload': out({'ev': 'echo', 'cmd': 'unload'}); out({'ev': 'page'})     # the real child reloads a fresh idle page
    elif m['cmd'] == 'quit': break
