"""Find existing Claude Code sessions by a spoken description.

Sources: the live registry (sidecar.sessions with a messaging socket) and transcripts
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl (the same files the VS Code extension lists per workspace).
Each transcript gives: cwd, title (ai-title / custom-title / summary), first and last user prompts, entrypoint, mtime.
"""
import json
import math
import os
import re
import time
from pathlib import Path

PROJECTS = Path.home() / '.claude' / 'projects'
HEAD, TAIL = 96_000, 256_000
STOP = set(('найди найти поищи покажи открой сессию сессия сессии чат разговор где мы с тобой работаем работали работаю '
            'над про по в на и с о об это тот та той ту который которая которой там тут делаем делали делал занимаемся '
            'занимались session find the a').split())
RU = dict(zip('абвгдезийклмнопрстуфыэ', 'abvgdeziiklmnoprstufye'))
RU.update({'ж': 'zh', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ь': '', 'ю': 'yu', 'я': 'ya', 'ё': 'e'})
# spoken words -> what they look like in paths / prompts
ALIASES = {'крабик': ['краб', 'mascot', 'маскот', 'clawd'], 'голосов': ['voice', 'голос'], 'виджет': ['widget'],
           'маскот': ['mascot', 'крабик'], 'лаунчер': ['launcher'], 'дрифт': ['drift']}


def translit(s: str) -> str:
    return ''.join(RU.get(ch, ch) for ch in s.lower())


def keywords(query: str) -> list[str]:
    words = [w for w in re.findall(r'[\w-]+', query.lower()) if w not in STOP and len(w) >= 3]
    out = []
    for w in words:
        stem = w[:max(4, len(w) - 2)] if re.match(r'[а-яё]', w) and len(w) > 5 else w
        out.append(stem)
        for k, v in ALIASES.items():
            if w.startswith(k): out.extend(v)
    return list(dict.fromkeys(out))


def _text(c) -> str:
    if isinstance(c, str): return c
    return ' '.join(p.get('text', '') for p in c or [] if isinstance(p, dict) and p.get('type') == 'text')


def read_meta(path: Path) -> dict | None:
    """Cheap metadata from the head and tail of a transcript."""
    try:
        st = path.stat()
        with open(path, 'rb') as f:
            head = f.read(HEAD).decode('utf-8', 'replace')
            tail = ''
            if st.st_size > HEAD:
                f.seek(max(HEAD, st.st_size - TAIL)); tail = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    m = {'id': path.stem, 'path': str(path), 'mtime': st.st_mtime, 'size': st.st_size, 'cwd': None, 'title': '', 'entrypoint': '', 'prompts': []}
    for chunk, is_tail in ((head, False), (tail, True)):
        lines = chunk.splitlines()
        if is_tail or len(chunk) >= HEAD: lines = lines[1:-1] if is_tail else lines[:-1]
        for ln in lines:
            try: d = json.loads(ln)
            except ValueError: continue
            t = d.get('type')
            if t in ('ai-title', 'custom-title', 'summary'):
                m['title'] = d.get('customTitle') or d.get('aiTitle') or d.get('summary') or m['title']
            if d.get('cwd') and not m['cwd']: m['cwd'] = d['cwd']
            if d.get('entrypoint') and not m['entrypoint']: m['entrypoint'] = d['entrypoint']
            if t == 'user' and not d.get('isSidechain') and not d.get('isMeta'):
                s = re.sub(r'<(?P<tag>[\w-]+)[^>]*>[\s\S]*?</(?P=tag)>', ' ', _text((d.get('message') or {}).get('content')))
                s = re.sub(r'\s+', ' ', s).strip()
                if s and not s.startswith('<'): m['prompts'].append(s[:300])
    if len(m['prompts']) > 8: m['prompts'] = m['prompts'][:3] + m['prompts'][-5:]
    return m


def scan(root: Path | None = None, max_files: int = 300, max_age_days: float = 30) -> list[dict]:
    root = root or PROJECTS
    files = []
    now = time.time()
    try:
        for d in root.iterdir():
            if not d.is_dir(): continue
            for f in d.glob('*.jsonl'):  # top level only: subagent transcripts live in subdirs
                try: mt = f.stat().st_mtime
                except OSError: continue
                if now - mt < max_age_days * 86400: files.append((mt, f))
    except OSError:
        return []
    files.sort(reverse=True)
    return [m for _, f in files[:max_files] if (m := read_meta(f))]


def fulltext(metas: list[dict], kws: list[str], limit: int = 150, max_bytes: int = 60_000_000) -> None:
    """Count keyword occurrences in the whole transcript of the most recent sessions (metas[i]['hits'])."""
    pats = {k: k.encode('utf-8') for k in kws if len(k) >= 4}
    for m in sorted(metas, key=lambda x: -x.get('mtime', 0))[:limit]:
        try:
            if os.path.getsize(m['path']) > max_bytes: continue
            b = Path(m['path']).read_bytes().lower()
        except (OSError, TypeError): continue
        m['hits'] = {k: b.count(p) for k, p in pats.items()}


def score(meta: dict, kws: list[str], project_hint: str | None = None, live: bool = False, now: float | None = None) -> float:
    now = time.time() if now is None else now
    if meta.get('entrypoint', '').startswith('sdk') and not live: return 0.0  # agents' -p runs: hidden in VS Code too
    title = (meta.get('title') or '').lower()
    prompts = ' '.join(meta.get('prompts') or []).lower()
    cwd = (meta.get('cwd') or '').lower().replace('\\', '/')
    s = cw = ft = 0.0
    full = meta.get('hits') or {}
    for k in kws:
        lat = translit(k)
        if k in title or lat in title: s += 3
        hits = prompts.count(k) + (prompts.count(lat) if lat != k else 0)
        s += min(3.0, 1.0 * hits)
        ft += 0.6 * math.log1p(full.get(k, 0))  # anywhere in the transcript (middle of a long session)
        if len(lat) >= 4 and lat in cwd: cw += 1.5
    s += min(cw, 2.0) + min(ft, 3.0)
    if len(meta.get('prompts') or []) <= 1 and meta.get('size', HEAD) < 200_000: s *= 0.5  # one-shot / test sessions
    if project_hint and project_hint.lower().replace('\\', '/') in cwd: s += 1.5
    if s <= 0: return 0.0
    age_h = max(0.0, (now - meta.get('mtime', 0)) / 3600)
    s += 2.0 * math.exp(-age_h / 48)          # recency: +2 now, ~+0.7 after two days
    if live: s += 1.5
    return round(s, 3)


def find(query: str, live: dict | None = None, metas: list[dict] | None = None, project_hint: str | None = None, now: float | None = None) -> list[dict]:
    """-> candidates best first: {id, score, live, cwd, title, mtime, prompts, path}."""
    live = live or {}
    kws = keywords(query)
    if not kws: return []
    metas = scan() if metas is None else metas
    fulltext(metas, kws)
    by_id = {m['id']: m for m in metas}
    for sid, s in live.items():  # live sessions without a transcript yet
        if sid not in by_id:
            tp = s.get('transcript_path')
            m = read_meta(Path(tp)) if tp and os.path.exists(tp) else None
            by_id[sid] = m or {'id': sid, 'path': tp, 'mtime': time.time(), 'cwd': s.get('cwd'), 'title': '', 'entrypoint': '', 'prompts': []}
    out = []
    for sid, m in by_id.items():
        sc = score(m, kws, project_hint, sid in live, now)
        if sc > 0: out.append({**m, 'score': sc, 'live': sid in live})
    out.sort(key=lambda x: (-x['score'], -x['mtime']))
    return out


def pick(cands: list[dict], margin: float = 0.15) -> tuple[str, list[dict]]:
    """-> ('none'|'one'|'ambiguous', candidates). Ambiguous when the runner-up is within `margin` of the best."""
    if not cands: return 'none', []
    if len(cands) > 1 and cands[1]['score'] >= cands[0]['score'] * (1 - margin): return 'ambiguous', cands[:2]
    return 'one', cands[:1]


def label(c: dict) -> str:
    proj = Path(c.get('cwd') or '?').name or '?'
    t = c.get('title') or (c.get('prompts') or [''])[-1][:60]
    return f'{proj}: {t}' if t else proj


def ago(ts: float, now: float | None = None) -> str:
    d = int(((time.time() if now is None else now) - ts) // 60)
    if d < 1: return 'только что'
    if d < 60: return f'{d} мин назад'
    if d < 48 * 60: return f'{d // 60} ч назад'
    return f'{d // 1440} дн назад'
