"""Glue between vscode_bridge (hooks sidecar) and the voice widget: speaks session events, holds permission/ask
prompts for a voice answer, routes new voice tasks to live Claude Code sessions (VS Code / CLI)."""
import collections, difflib, json, random, os, re, shutil, subprocess, tempfile, threading, time, uuid
from pathlib import Path

from dialog_state import NEG, Dialog, Pending, is_done_reply, is_question
from vscode_bridge import headless, sessions_index
from vscode_bridge.messaging import send_to_session
from vscode_bridge.sidecar import Sidecar

YES = re.compile(r'^(да|давай|разреш\w*|ок|окей|хорошо|подтвержда\w*|согласен|согласна|конечно|продолжай|делай)(\s|$)', re.I)
NO = re.compile(r'^(нет|не\s+надо|не\s+нужно|отмена|отмени\w*|запрети\w*|отклони\w*|не\s+разреша\w*)(\s|$)', re.I)
ORD = [('перв', 0), ('втор', 1), ('трет', 2), ('четв', 3), ('1', 0), ('2', 1), ('3', 2), ('4', 3)]
SYS_SUM = 'Ты озвучиваешь итог работы ИИ-агента. Перескажи по-русски в 1-2 коротких предложениях для голосового ассистента: без markdown, кода, путей и списков. Если агент о чём-то спрашивает пользователя, скажи что именно. Не начинай с «Понял», «Хорошо», «Окей» и т.п., сразу суть.'
SYS_ROUTE = ('Ты выбираешь сессию Claude Code для голосовой задачи. Дан список живых сессий (проект, последний ответ, недавние запросы) и новая задача. '
             'Если задача явно относится к проекту/теме одной из сессий, верни её номер в target и intent "session". '
             'Если ни одна не подходит: общий вопрос, факты, погода, болтовня, любая задача не про код проекта, то target=null и intent "new" (новая сессия, ничего не спрашивать); '
             'intent "ask" только если задача явно про конкретный код/проект, но непонятно какой. '
             'confidence 0..1 уверенность; clear=false если задача невнятная, обрывочная или похожа на искажённое распознавание речи; '
             'restatement: суть задачи одной короткой фразой по-русски, без вводных слов вроде «Понял», «Хорошо». '
             'ack: 2-4 слова, что ты сейчас начинаешь делать, по-русски, без «Понял»/«Хорошо» (например «Ищу погоду», «Смотрю»). '
             'complexity: "trivial" для погоды, факта, времени, болтовни, короткого вопроса без кода и файлов; иначе "normal". '
             'Ответ только JSON: {"target": <номер или null>, "intent": "<session|new|ask>", "confidence": <0..1>, "complexity": "<trivial|normal>", "ack": "<2-4 слова>", "clear": <true|false>, "restatement": "<фраза>"}')

SYS_DIRECT = 'Ответь по-русски коротко, не более двух предложений, для голосового ассистента, без вводных слов вроде «Понял», «Хорошо», сразу суть: без markdown и списков. Если нужны свежие данные (погода, новости), используй WebSearch.'
ANY = re.compile(r'люб\w*|нов\w*|без\s+разницы|неважно|не\s+важно|все\s+равно|всё\s+равно|никак\w*|ни\s+в\s+как\w*|созда\w*|обычн\w*|по\s+умолчанию|где\s+угодно|какая\s+разница', re.I)
STOP = set('в во на проект проекте проекта папке папка папку это сделай сделать давай да нет ну а и по с к у про для открой создай'.split())
RU = dict(zip('абвгдезийклмнопрстуфыэ', 'abvgdeziiklmnoprstufye'))
RU.update({'ж': 'zh', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ь': '', 'ю': 'yu', 'я': 'ya', 'ё': 'e'})


def translit(s): return ''.join(RU.get(ch, ch) for ch in s.lower())
def norm(s):  # latin phonetic-ish form: ru->lat, c->k, y->i, ...
    t = re.sub(r'[^a-z0-9]', '', translit(s))
    for a, b in (('ph', 'f'), ('ck', 'k'), ('ch', 'h'), ('c', 'k'), ('q', 'k'), ('y', 'i'), ('w', 'v'), ('x', 'ks'), ('z', 's'), ('j', 'i')): t = t.replace(a, b)
    return t
def skeleton(s): return re.sub(r'[aeiou]', '', norm(s))
def sim(q, name):
    nq, nn = norm(q), norm(name)
    if not nq or not nn: return 0.0
    r = max(difflib.SequenceMatcher(None, nq, nn).ratio(), 0.9 * difflib.SequenceMatcher(None, skeleton(q), skeleton(name)).ratio() if len(skeleton(q)) >= 3 else 0)
    if len(nq) >= 4 and (nq in nn or nn in nq): r = max(r, 0.85)
    return r


def match_projects(text, names):
    """-> list of (score, name), best first, scored by the best word/phrase of the answer."""
    words = [w for w in re.findall(r'[\w-]+', text.lower()) if w not in STOP]
    qs = words + ([' '.join(words)] if len(words) > 1 else [])
    out = [(max((sim(q, n) for q in qs), default=0.0), n) for n in names]
    return sorted(out, reverse=True)


def interpret_project(text, names):
    """-> ('default', None) | ('match', name) | ('ambiguous', [top3]). Never drops the answer."""
    ranked = match_projects(text, names)
    top = ranked[0] if ranked else (0.0, '')
    if top[0] >= 0.72 and (len(ranked) < 2 or top[0] - ranked[1][0] >= 0.08): return 'match', top[1]
    if ANY.search(text) and top[0] < 0.72: return 'default', None
    return 'ambiguous', [n for _, n in ranked[:3]]


def parse_route(raw, c):
    """Model reply -> (session_id | None, clear, restatement, intent, complexity)."""
    j = json.loads(re.search(r'\{[\s\S]*\}', raw).group(0))
    n = j.get('target')
    sid = c[n - 1]['id'] if isinstance(n, int) and 1 <= n <= len(c) else None
    try: conf = float(j.get('confidence', 1))
    except (TypeError, ValueError): conf = 0.0
    intent = j.get('intent') if j.get('intent') in ('session', 'new', 'ask') else ('session' if sid else 'new')
    if not sid and intent == 'session': intent = 'new'
    if sid: intent = 'session'
    cx = 'trivial' if str(j.get('complexity', '')).lower() == 'trivial' and not sid else 'normal'
    return sid, bool(j.get('clear', True)) and conf >= 0.6, unfill(str(j.get('restatement', '')))[:160], intent, cx, unfill(str(j.get('ack', '')))[:40].rstrip('.!?, ')


FILLER = re.compile(r'^\s*(?:(?:понял[аи]?|поняла|хорошо|окей|ок|ладно|ясно|принято|так|итак)\s*[:,.!\-—]*\s*)+', re.I)
def unfill(s):
    t = FILLER.sub('', str(s or ''))
    return t[:1].upper() + t[1:] if t else str(s or '').strip()


def make_ack(kind, ack='', proj=''):
    """Short spoken acknowledgement without an extra LLM call: the router's `ack`, else a varied canned phrase."""
    if kind == 'trivial': return ack or random.choice(['Сейчас посмотрю', 'Секунду', 'Смотрю'])
    if kind == 'session': return random.choice([f'Отправляю в {proj}', f'Передаю в {proj}'])
    return random.choice(['Создаю сессию', 'Запускаю новую сессию'])


REASK = re.compile(r'^(вернись к вопросу|какой был вопрос|что ты спрашивал)', re.I)



def plain(s): return re.sub(r'\s+', ' ', re.sub(r'[`*#>_|]', '', re.sub(r'```[\s\S]*?```', ' ', str(s or '')))).strip()
def clip(s, n): return s if len(s) <= n else re.sub(r'\s+\S*$', '', s[:n]) + '...'
def project(s): return Path(s.get('cwd') or '?').name or '?'


def haiku(system, user, timeout=40, tools=''):
    """One-shot `claude -p --model haiku`; VOICE_BRIDGE_OFF keeps our own hooks silent for this call."""
    exe = shutil.which('claude') or 'claude'
    env = {**os.environ, 'VOICE_BRIDGE_OFF': '1'}
    r = subprocess.run([exe, '-p', '--model', 'haiku', '--tools', tools, '--no-session-persistence', '--system-prompt', system, '--output-format', 'json'],
                       input=user, capture_output=True, text=True, encoding='utf-8', timeout=timeout, env=env, cwd=tempfile.gettempdir(),
                       creationflags=0x08000000 if os.name == 'nt' else 0)
    j = json.loads(r.stdout)
    if j.get('is_error'): raise RuntimeError(str(j.get('result'))[:80])
    return unfill(str(j.get('result', '')).strip())


def recent_prompts(path, n=3):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 200_000)); lines = f.read().decode('utf-8', 'replace').splitlines()[1:]
    except Exception: return []
    out = []
    for ln in lines:
        try: m = json.loads(ln)
        except Exception: continue
        if m.get('type') != 'user' or m.get('isSidechain'): continue
        c = (m.get('message') or {}).get('content')
        t = c if isinstance(c, str) else ' '.join(p.get('text', '') for p in c or [] if isinstance(p, dict) and p.get('type') == 'text')
        t = plain(t)
        if t and not t.startswith('<'): out.append(clip(t, 100))
    return out[-n:]


def is_headless(path):
    """True for `claude -p` / SDK sessions (entrypoint sdk-*) - agents' background runs, not something to announce."""
    try:
        with open(path, 'rb') as f: head = f.read(65536).decode('utf-8', 'replace')
    except Exception: return False
    m = re.search(r'"entrypoint":"([^"]+)"', head)
    return bool(m and m.group(1).startswith('sdk'))


FIND = re.compile(r'^(найди|найти|поищи|отыщи|ищи|открой)\s+(мне\s+)?(ту\s+)?(сесси|чат|разговор|диалог)', re.I)
SHOW = re.compile(r'^(покажи|назови|какая|какую)\s+(мне\s+)?(найденн\w*\s+|эту\s+)?сесси', re.I)
BG = re.compile(r'фон|продолж|давай|да\b', re.I)


def norm_low(text): return re.sub(r'[^\w\s]', ' ', str(text).lower()).strip()


class Bridge:
    def __init__(self, w):
        self.w = w; self.queue = collections.deque(); self.cur = None; self.lock = threading.Lock()
        self.chat = collections.deque(maxlen=10); self.chat_t = 0.0  # trivial-channel memory (Q/A turns)
        self.last_sid = None
        self.voice_sids = {}  # sid -> time a voice task was sent there (ask-more only for these)
        self.dlg = Dialog(ask_more=w.cfg.get('ask_more_after_done', True))
        self.found = None          # last session found by voice search
        self.stop_seen = {}        # sid -> time of its last Stop event
        self.cwds = {}             # sid -> cwd of sessions we created/resumed headless
        self._tick_stop = threading.Event()
        self.sc = Sidecar(on_event=self.on_event, hold_timeout=w.cfg['bridge_hold_sec'], hold_enabled=self.can_hold)

    def start(self):
        self.sc.start()
        threading.Thread(target=self._ticker, daemon=True).start()
        return self

    def stop(self): self._tick_stop.set(); self.sc.stop()

    def _ticker(self):
        while not self._tick_stop.wait(5):
            note = self.dlg.tick()
            if note:
                print('[dialog] clarification timeout', flush=True)
                try: self.w.ui.say(note, 'say', None, None, 15.0, None)
                except Exception: pass

    def can_hold(self):  # hold only when somebody is actually listening and speech is on
        w = self.w
        return bool(w.ready and not w.quit and w.cfg['bridge_hold'] and w.cfg['speak_mode'] == 'immediate')

    def say(self, text, ask=False, extra=None):
        if text: self.w.say_event(text, ask, extra or {})

    # ---------------------------------------------------------------- events
    def on_event(self, ev):
        k, b = ev['kind'], ev['body']; s = self.sc.sessions.get(b.get('session_id'), {})
        if k == 'Stop':
            if b.get('session_id'): self.stop_seen[b['session_id']] = time.time()
            threading.Thread(target=self.speak_stop, args=(b,), daemon=True).start()
        elif k == 'pending_permission':
            self.enqueue({'id': b['id'], 'kind': 'perm', 'proj': project(s or b), 'body': b})
        elif k == 'pending_ask':
            qs = (b.get('tool_input') or {}).get('questions') or []
            if qs: self.enqueue({'id': b['id'], 'kind': 'ask', 'proj': project(s or b), 'body': b, 'qs': qs, 'i': 0, 'ans': {}})
        elif k in ('timeout_permission', 'timeout_ask'): self.drop(b['id'])

    def speak_stop(self, b):
        msg = plain(b.get('last_assistant_message'))
        if not msg or b.get('stop_hook_active') or b.get('agent_id'): return
        sid = b.get('session_id'); s = self.sc.sessions.get(sid, {}); p = project(s or b)
        voice = sid in self.voice_sids
        if not voice:  # foreign session: summary only, never ask-more; skip headless -p / SDK runs
            if not self.w.cfg.get('announce_foreign_sessions', True) or is_headless(b.get('transcript_path') or s.get('transcript_path')): return
        q = is_question(msg)
        text = unfill(msg) if len(msg) < 200 else None
        if text is None:
            try: text = plain(haiku(SYS_SUM, clip(msg, 4000)))
            except Exception: text = clip(msg, 200)
        if q and not is_question(text): text = text.rstrip('.!') + '?'  # keep "the session asks something" visible to the dialog
        if not voice and not (q and sid in self.sc.live_sessions()):
            self.say(text, False, {'header': p}); return  # announce only, the dialog is not bound to it
        if voice and not q: self.voice_sids.pop(sid, None)
        with self.lock: queued = len(self.queue) + (1 if self.cur else 0)
        out = self.dlg.session_stop(sid, text, voice, queued)
        self.say(out, is_question(out), {'header': p})

    # ---------------------------------------------------------------- pending prompts (one at a time)
    def enqueue(self, item):
        with self.lock: self.queue.append(item)
        self.next()

    def drop(self, pid):
        with self.lock:
            self.queue = collections.deque(i for i in self.queue if i['id'] != pid)
            gone = bool(self.cur and self.cur['id'] == pid)
            if gone: self.cur = None
        if gone: self.w.ui.hide_bubble()
        self.next()

    def next(self):
        with self.lock:
            if self.cur or not self.queue: return
            self.cur = self.queue.popleft(); it = self.cur
        self.ask(it)

    def ask(self, it):
        if it['kind'] == 'perm':
            b = it['body']; ti = b.get('tool_input') or {}
            target = plain(ti.get('command') or ti.get('file_path') or ti.get('path') or ti.get('url') or ti.get('pattern') or '')
            text = f"Сессия {it['proj']} хочет {b.get('tool_name')}{': ' + clip(target, 80) if target else ''}. Разрешить?"
            opts, spoken = ['Разрешить', 'Отклонить'], text
        else:
            q = it['qs'][it['i']]; n = len(it['qs'])
            text = (f'Вопрос {it["i"] + 1} из {n}. ' if n > 1 else '') + q.get('question', '')
            opts = [o.get('label', '') for o in q.get('options') or []]
            spoken = text + (' Варианты: ' + ', '.join(opts) + '.' if opts else '')
        self.w.say_event(spoken, True, {'header': it['proj'], 'opts': opts, 'shown': text})

    def try_answer(self, text):
        """Voice/click answer to the current permission/AskUserQuestion prompt. False -> not such an answer
        (send_task then handles it: dialog clarifications, confirmations, new tasks)."""
        with self.lock: it = self.cur
        if not it: return False
        low = norm_low(text)
        if it['kind'] == 'perm':
            yes = bool(YES.search(low)) or low == 'разрешить'
            no = bool(NO.search(low)) or low == 'отклонить'
            if not (yes or no): return False
            self.sc.answer(it['id'], behavior='allow' if yes else 'deny', message='Denied by voice')
            self.done(); return True
        q = it['qs'][it['i']]; opts = q.get('options') or []
        hit = next((o for o in opts if str(o.get('label', '')).lower() in low), None)
        if not hit:
            o = next((x for x in ORD if re.search(r'(^|\s)' + x[0], low)), None)
            if o and o[1] < len(opts): hit = opts[o[1]]
        it['ans'][q['question']] = hit['label'] if hit else text
        it['i'] += 1
        if it['i'] < len(it['qs']): self.ask(it); return True
        self.sc.answer(it['id'], answers=it['ans']); self.done(); return True

    def done(self):
        with self.lock: self.cur = None
        self.next()

    # ---------------------------------------------------------------- routing
    def candidates(self):
        return [{'id': sid, 'proj': project(s), 'cwd': s.get('cwd'), 'last': clip(plain(s.get('last_assistant_message')), 150),
                 'prompts': recent_prompts(s.get('transcript_path')) if s.get('transcript_path') else []}
                for sid, s in self.sc.live_sessions().items()]

    def route(self, text):
        """-> parse_route tuple. Raises on model failure."""
        c = self.candidates()
        lst = '\n'.join(f"{i + 1}. проект={x['proj']} | путь={x['cwd']} | последний ответ: {x['last']} | запросы: {' / '.join(x['prompts'])}" for i, x in enumerate(c))
        hint = next((f'\nПоследняя активная сессия: {i + 1}. Если задача её продолжение, предпочти её.' for i, x in enumerate(c) if x['id'] == self.last_sid), '')
        return parse_route(haiku(SYS_ROUTE, f'Сессии:\n{lst}{hint}\n\nЗадача: {text}'), c)

    def project_dirs(self):
        """name -> path: subdirs of the projects root plus cwds of known sessions."""
        root = Path(self.w.cfg.get('projects_root') or Path.home()); d = {}
        try: d.update({x.name: str(x) for x in root.iterdir() if x.is_dir() and not x.name.startswith('.')})
        except OSError: pass
        for s in self.sc.sessions.values():
            if s.get('cwd'): d.setdefault(project(s), s['cwd'])
        return d

    # ---------------------------------------------------------------- sessions: new / continue
    def new_session(self, cwd, task):
        """Fresh session in cwd. headless (default): `claude -p` with a pre-set session id -> real transcript listed in
        VS Code, opened there afterwards; terminal: interactive CLI in a new console (legacy)."""
        mode = self.w.cfg.get('new_session_mode', 'headless')
        if not Path(str(cwd)).is_dir():
            cwd = str(self.w.cfg.get('default_project_dir') or '')
            if not Path(cwd).is_dir(): cwd = str(Path.home())
        p = Path(cwd).name or cwd; m = self.w.cfg.get('new_session_model') or 'sonnet'
        if re.search(r'сложн|опус|opus', task, re.I): m = 'opus'
        print('[new-session]', mode, cwd, flush=True)
        self.w.ui.say(f'+ {p}', 'user', hide=3); self.w.set_state(f'новая сессия «{p}»')
        if mode == 'terminal':
            exe = shutil.which('claude') or 'claude'
            subprocess.Popen([exe, '--model', m, task], cwd=cwd, creationflags=0x00000010 if os.name == 'nt' else 0)  # CREATE_NEW_CONSOLE
            self.dlg.start(None); self.say(f'Открыл сессию в {p} в отдельном окне.', False); return
        self.run_headless(task, cwd, model=m, resume=None, background=True)

    def run_headless(self, task, cwd, model=None, resume=None, background=True):
        sid = resume or str(uuid.uuid4())
        self.voice_sids[sid] = time.time(); self.cwds[sid] = cwd; self.last_sid = sid; self.dlg.start(sid)
        t = threading.Thread(target=self._headless_worker, args=(task, cwd, model, resume, sid), daemon=True)
        t.start()
        if not background: t.join()
        return sid

    def _headless_worker(self, task, cwd, model, resume, sid):
        p = Path(cwd).name or cwd; t0 = time.time()
        try:
            res = headless.run(task, cwd, model=model, resume=resume, session_id=None if resume else sid,
                               entrypoint=self.w.cfg.get('new_session_entrypoint', 'claude-vscode'))
        except Exception as e:
            print('[headless] error', e, flush=True); self.voice_sids.pop(sid, None)
            self.say(self.dlg.fail(f'Не смог запустить сессию в {p}: {clip(str(e), 80)}'), False); return
        print('[headless] done', res['session_id'], 'error' if res['is_error'] else 'ok', '%.0fs' % (time.time() - t0), flush=True)
        if res['is_error']:
            self.voice_sids.pop(sid, None)
            self.say(self.dlg.fail(f'Сессия в {p} завершилась с ошибкой: {clip(plain(res["result"]), 120)}'), False, {'header': p})
        else:
            for _ in range(30):  # the Stop hook normally arrives before the process exits
                if self.stop_seen.get(sid, 0) >= t0: break
                time.sleep(0.1)
            if self.stop_seen.get(sid, 0) < t0:  # hooks not delivered (widget hooks missing): speak the result ourselves
                self.speak_stop({'session_id': sid, 'cwd': cwd, 'last_assistant_message': res['result']})
        if not resume and self.w.cfg.get('open_new_in_vscode', True):
            try: print('[headless] open in vscode', headless.open_in_vscode(cwd, sid), flush=True)
            except Exception as e: print('[headless] vscode open failed', e, flush=True)

    def session_cwd(self, sid):
        if sid in self.cwds: return self.cwds[sid]
        s = self.sc.sessions.get(sid) or {}
        if s.get('cwd'): return s['cwd']
        if self.found and self.found.get('id') == sid: return self.found.get('cwd')
        for f in sessions_index.PROJECTS.glob(f'*/{sid}.jsonl'):
            m = sessions_index.read_meta(f)
            if m and m.get('cwd'): return m['cwd']
        return None

    def has_transcript(self, sid, cwd):
        paths = [headless.transcript_path(cwd, sid), (self.sc.sessions.get(sid) or {}).get('transcript_path'),
                 (self.found or {}).get('path') if (self.found or {}).get('id') == sid else None]
        return any(p and Path(p).exists() for p in paths) or any(sessions_index.PROJECTS.glob(f'*/{sid}.jsonl'))

    def continue_session(self, sid, text):
        """Same task, same session: live socket if possible, else a headless resume of its transcript."""
        if sid in self.sc.live_sessions() and self.dispatch(sid, text): return True
        cwd = self.session_cwd(sid)
        if cwd and self.has_transcript(sid, cwd):
            p = Path(cwd).name; self.say(random.choice([f'Продолжаю в {p}', f'Передаю в {p}']), False)
            self.run_headless(text, cwd, resume=sid); return True
        self.say(self.dlg.fail('Сессия недоступна, и её историю я не нашёл.'), False); return False

    def dispatch(self, sid, text):
        w = self.w
        try:
            send_to_session(self.sc, sid, text); self.last_sid = sid; self.voice_sids[sid] = time.time(); p = project(self.sc.sessions[sid])
            self.dlg.start(sid)
            w.ui.say(f'→ {p}', 'user', hide=3); w.set_state(f'отправил в «{p}»'); print('[route]', p, flush=True); return True
        except Exception as e:
            print('send error', e, flush=True); self.sc.drop_session(sid); return False

    # ---------------------------------------------------------------- trivial chat
    def direct(self, text):
        """Trivial question answered by one Haiku call, with ~5 turns of memory (reset after 2 min idle)."""
        if time.time() - self.chat_t > 120: self.chat.clear()
        hist = '\n'.join(f'{r}: {t}' for r, t in self.chat)
        self.dlg.start(None)
        ans = plain(haiku(SYS_DIRECT, (f'Предыдущий диалог:\n{hist}\n\n' if hist else '') + f'Пользователь: {text}', timeout=60, tools='WebSearch'))
        self.chat.append(('Пользователь', text)); self.chat.append(('Ты', ans)); self.chat_t = time.time()
        if is_question(ans): self.dlg.ask('chat', ans); out = ans
        else: out = self.dlg.complete(ans, voice=True)
        self.say(out, is_question(out), {'header': 'Клод'})

    def ack(self, kind, ack='', proj=''):
        self.say(make_ack(kind, ack, proj), False); self.w.set_state('отправил запрос')

    # ---------------------------------------------------------------- session search
    def find_session(self, query):
        self.w.set_state('ищу сессию')
        try: cands = sessions_index.find(query, live=self.sc.live_sessions())
        except Exception as e: print('[find] error', e, flush=True); cands = []
        kind, top = sessions_index.pick(cands)
        print('[find]', kind, [(c['id'][:8], c['score']) for c in cands[:3]], flush=True)
        if kind == 'none':
            self.say(self.dlg.fail('Не нашёл подходящую сессию. Уточни, о чём она.'), False); return
        if kind == 'ambiguous':
            a, b = top
            text = f'Нашёл две: первая {sessions_index.label(a)}, {sessions_index.ago(a["mtime"])}; вторая {sessions_index.label(b)}, {sessions_index.ago(b["mtime"])}. Какую?'
            if self.dlg.ask('pick_session', text, data={'cands': top}):
                self.say(text, True, {'opts': ['Первая', 'Вторая'], 'shown': text, 'header': 'Поиск'})
            return
        self.use_found(top[0])

    def use_found(self, c):
        self.found = c; lab = sessions_index.label(c); sid = c['id']
        if c.get('live') and sid in self.sc.live_sessions():
            self.last_sid = sid
            text = f'Нашёл сессию {lab}, она на связи. Что передать?'
            if self.dlg.ask('what_to_send', text, sid=sid): self.say(text, True, {'header': project(c)})
            return
        text = (f'Нашёл сессию {lab}, {sessions_index.ago(c["mtime"])}, но она запущена до подключения, и напрямую писать в неё я не могу. '
                'Перезапусти её в VS Code, тогда смогу. Или продолжить её в фоне? Если она сейчас открыта в VS Code, лучше не надо.')
        if self.dlg.ask('resume_offer', text, sid=sid, data={'cwd': c.get('cwd')}, confirm=True):
            self.say(text, True, {'header': project(c), 'opts': ['Продолжить в фоне', 'Нет'], 'shown': text})

    def show_found(self):
        c = self.found
        if not c: self.say('Я ещё не искал сессию. Скажи, например: найди сессию, где мы делали виджет.', False); return
        live = c['id'] in self.sc.live_sessions()
        how = 'на связи, отправляю напрямую' if live else 'без связи, только продолжение в фоне'
        self.say(f'{project(c)}, сессия {c["id"][:8]}, {how}, последняя активность {sessions_index.ago(c["mtime"])}.', False,
                 {'header': sessions_index.label(c)[:60]})

    # ---------------------------------------------------------------- answers to our own questions
    def answer_pending(self, p, text, stt_unclear):
        low = norm_low(text)
        yes = bool(YES.search(low)) or low in ('верно', 'правильно', 'отправляй')
        no = bool(NO.search(low)) and len(low.split()) <= 3
        if p.kind == 'project': return self.answer_project(text, p.data['task'])
        if p.kind == 'confirm_send':
            if yes:
                if not self.continue_session(p.sid, p.data['text']): pass
            elif no: self.dlg.reset(); self.say('Хорошо, скажи задачу заново.', True)
            else: return self.route_task(text, stt_unclear)  # a correction: route the corrected task again
            return True
        if p.kind in ('session', 'what_to_send'): return self.continue_session(p.sid, text)
        if p.kind == 'chat':
            try: self.direct(text)
            except Exception as e: print('direct error', e, flush=True); self.say(self.dlg.fail('Не получилось ответить, попробуй ещё раз.'), False)
            return True
        if p.kind == 'pick_session':
            cands = p.data['cands']
            o = next((i for k, i in ORD if re.search(r'(^|\s)' + k, low)), None)
            if o is None:
                ranked = match_projects(text, [sessions_index.label(c) for c in cands])
                o = next((i for i, c in enumerate(cands) if sessions_index.label(c) == ranked[0][1]), None) if ranked and ranked[0][0] >= 0.6 else None
            if o is None or o >= len(cands):
                if self.dlg.ask('pick_session', 'Первая или вторая?', data=p.data): self.say('Первая или вторая?', True, {'opts': ['Первая', 'Вторая']})
                return True
            self.use_found(cands[o]); return True
        if p.kind == 'resume_offer':
            if no: self.dlg.reset(); self.say('Хорошо, не трогаю её.', False); return True
            if yes or BG.search(low):
                if p.data.get('text'): return self.continue_session(p.sid, p.data['text'])
                self.cwds.setdefault(p.sid, p.data.get('cwd'))
                if self.dlg.ask('what_to_send', 'Что передать?', sid=p.sid): self.say('Что передать?', True)
                return True
            self.cwds.setdefault(p.sid, p.data.get('cwd'))
            return self.continue_session(p.sid, text)  # the answer already is the task
        return False

    def answer_project(self, text, task):
        dirs = self.project_dirs()
        kind, v = interpret_project(text, list(dirs))
        if kind == 'default': self.ack('new'); self.new_session((self.w.cfg.get('default_project_dir') or str(Path.home())), task)
        elif kind == 'match': self.ack('new'); self.new_session(dirs[v], task)
        else:
            q = 'Не уверен, какой проект. ' + (f'Может, {", ".join(v)}? ' if v else '') + 'Назови проект или скажи «любой».'
            if self.dlg.ask('project', q, data={'task': task}): self.say(q, True)
        return True

    # ---------------------------------------------------------------- entry point
    def send_task(self, text, stt_unclear=False):
        """True when handled here (sent, asked, or the user was told what to do); False -> caller falls back to agent-ui."""
        self.dlg.tick()
        low = norm_low(text)
        if SHOW.match(low): self.show_found(); return True  # does not touch a pending question
        if FIND.match(low):
            self.dlg.reset()
            threading.Thread(target=self.find_session, args=(text,), daemon=True).start(); self.say('Ищу.', False); return True
        p = self.dlg.take()
        if p: return self.answer_pending(p, text, stt_unclear) or True
        if self.dlg.answering_more() and is_done_reply(low): self.dlg.reset(); return True  # "no, thanks": go idle quietly
        if REASK.match(low):
            st = self.dlg.reask()
            if st: self.say(st.text, True); return True
        return self.route_task(text, stt_unclear)

    def route_task(self, text, stt_unclear=False):
        w = self.w
        try: sid, clear, restated, intent, cx, ack = self.route(text)
        except Exception as e: print('route error', e, flush=True); sid, clear, restated, intent, cx, ack = None, True, '', 'new', 'normal', ''
        if sid:
            p = project(self.sc.sessions[sid]); mode = w.cfg['confirm_mode']
            if mode == 'always' or (mode == 'unclear' and (stt_unclear or not clear)):
                q = f'{(restated or text).rstrip(".!? ")}. Отправить в {p}?'
                if self.dlg.ask('confirm_send', q, sid=sid, data={'text': text}, confirm=True):
                    self.say(q, True, {'header': p, 'opts': ['Да', 'Нет']})
                return True
            self.ack('session', proj=p)
            if self.dispatch(sid, text): return True
            q = f'Не смог отправить в {p}: сессия не отвечает. Продолжить её в фоне?'
            self.say(self.dlg.fail(q, Pending('resume_offer', q, sid, {'cwd': self.session_cwd(sid), 'text': text})), True, {'opts': ['Да', 'Нет']})
            return True
        if cx == 'trivial' and intent == 'new' and w.cfg.get('trivial_direct', True):
            self.ack('trivial', ack)
            try: self.direct(text); return True
            except Exception as e: print('direct error', e, flush=True)
        if intent == 'ask':
            projects = sorted(self.project_dirs())
            q = 'В каком проекте это делать?' + (' Есть: ' + ', '.join(projects[:5]) + '.' if projects else '')
            if self.dlg.ask('project', q, data={'task': text}): self.say(q, True)
        else: self.ack('new'); self.new_session((w.cfg.get('default_project_dir') or str(Path.home())), text)
        return True
