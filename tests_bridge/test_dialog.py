"""Dialog state machine + Bridge glue: clarifications, ask-more, failures, session search, headless sessions."""
import json, os, shutil, time
from pathlib import Path

import pytest

import bridge_voice as bv
from bridge_voice import Bridge
from dialog_state import CLARIFY, COMPLETED, CONFIRM, FAILED, IDLE, WORKING, Dialog
from vscode_bridge import headless, sessions_index as SI

MORE_WORDS = ('ещё', 'еще')


def has_more(t): return any(w in t.lower() for w in MORE_WORDS) and t.rstrip().endswith('?')


class FakeUI:
    def say(self, *a, **k): pass
    def hide_bubble(self): pass


class FakeW:
    def __init__(self):
        self.cfg = {'bridge_hold_sec': 5, 'bridge_hold': True, 'speak_mode': 'immediate', 'confirm_mode': 'never',
                    'ask_more_after_done': True, 'trivial_direct': True, 'default_project_dir': str(Path.cwd()), 'open_new_in_vscode': False}
        self.ready, self.quit, self.ui, self.said = True, False, FakeUI(), []
    def say_event(self, t, ask=False, extra=None): self.said.append((t, ask, extra or {}))
    def set_state(self, s): pass


@pytest.fixture
def bridge(monkeypatch):
    w = FakeW(); b = Bridge(w); b.sc.sessions.clear()
    b.sc.sessions['s1'] = {'cwd': 'D:/x/alpha', 'socket': 'p', 'token': 't'}
    b.sent = []
    monkeypatch.setattr(bv, 'send_to_session', lambda sc, sid, text: b.sent.append((sid, text)))
    monkeypatch.setattr(bv, 'haiku', lambda *a, **k: '{"target": 1, "intent": "session", "confidence": 0.9, "clear": true}')
    return b


def stop(b, sid, text):  # Stop hook of a session (synchronous)
    b.speak_stop({'session_id': sid, 'last_assistant_message': text, 'cwd': 'D:/x/alpha'})


# ---------------------------------------------------------------- pure state machine
def test_dialog_rules():
    d = Dialog(); d.start('s')
    assert d.session_stop('s', 'Какой файл?', voice=True) == 'Какой файл?' and d.state == CLARIFY
    assert d.session_stop('s', 'Какой файл?', voice=True) == ''  # no duplicate question
    p = d.take(); assert p.sid == 's' and d.state == WORKING
    out = d.session_stop('s', 'Готово.', voice=True); assert has_more(out) and d.state == COMPLETED
    d.start('s'); assert d.complete('Готово.', queued=1) == 'Готово.'  # queued prompts: no ask-more
    d2 = Dialog(); d2.start(); assert d2.complete('Готово.', voice=False) == 'Готово.'  # not voice-started
    assert d2.fail('Сессия недоступна.') == 'Сессия недоступна.' and d2.state == FAILED


def test_timeout_and_reask():
    d = Dialog(clarify_timeout=60); d.ask('session', 'Какой файл?', 's')
    assert d.tick(now=time.time() + 61) and d.state == IDLE and d.pending is None
    p = d.reask(); assert p and p.text == 'Какой файл?' and d.state == CLARIFY
    d.take(); assert d.reask() is None  # only once


# ---------------------------------------------------------------- bridge glue
def test_clarification_answer_continues_same_session(bridge):
    b = bridge
    b.send_task('почини тест в alpha'); assert b.sent == [('s1', 'почини тест в alpha')]
    stop(b, 's1', 'Какой именно тест чинить?')
    assert b.dlg.state == CLARIFY and not has_more(b.w.said[-1][0]) and b.w.said[-1][1] is True
    bv.haiku = lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not route an answer'))
    b.send_task('тот, что про логин')
    assert b.sent[-1] == ('s1', 'тот, что про логин') and b.dlg.state == WORKING


def test_multiple_clarifications_then_completion(bridge):
    b = bridge
    b.send_task('сделай деплой')
    stop(b, 's1', 'На какой сервер?'); b.send_task('на прод')
    stop(b, 's1', 'С миграциями?'); b.send_task('да, с миграциями')
    assert [t for _, t in b.sent] == ['сделай деплой', 'на прод', 'да, с миграциями']
    assert not any(has_more(t) for t, _, _ in b.w.said)
    stop(b, 's1', 'Задеплоил.')
    assert has_more(b.w.said[-1][0]) and b.dlg.state == COMPLETED
    n = len(b.w.said); assert b.send_task('нет, спасибо') is True and len(b.w.said) == n and b.dlg.state == IDLE


def test_new_command_after_completion(bridge):
    b = bridge
    b.send_task('почини тест'); stop(b, 's1', 'Починил.')
    assert has_more(b.w.said[-1][0])
    b.send_task('теперь запусти линтер'); assert b.sent[-1] == ('s1', 'теперь запусти линтер')
    stop(b, 's1', 'Линтер чистый.')
    assert not has_more(b.w.said[-1][0])  # ask-more at most once per ~2 min


def test_foreign_session_no_ask_more(bridge):
    b = bridge
    b.sc.sessions['s2'] = {'cwd': 'D:/x/beta'}
    stop(b, 's2', 'Сделал рефакторинг.')
    assert b.w.said[-1][0] == 'Сделал рефакторинг.' and b.dlg.state == IDLE


def test_failure_no_false_completion(bridge, monkeypatch, tmp_path):
    b = bridge
    def boom(sc, sid, text): raise OSError('pipe gone')
    monkeypatch.setattr(bv, 'send_to_session', boom)
    monkeypatch.setattr(SI, 'PROJECTS', tmp_path)
    b.send_task('почини тест')
    t = b.w.said[-1][0]
    assert 'не отвечает' in t and not has_more(t) and b.dlg.state == CONFIRM
    b.send_task('да')  # resume offered, but no transcript anywhere -> honest failure
    assert 'не нашёл' in b.w.said[-1][0] and b.dlg.state == FAILED and not has_more(b.w.said[-1][0])


def test_trivial_chat_clarification(bridge, monkeypatch):
    b = bridge; b.sc.sessions.clear()
    answers = iter(['{"target": null, "intent": "new", "complexity": "trivial", "confidence": 0.9}', 'В каком городе?', 'В Москве плюс пять.'])
    seen = []
    monkeypatch.setattr(bv, 'haiku', lambda s, u, **k: seen.append(u) or next(answers))
    b.send_task('какая погода')
    assert b.w.said[-1][0] == 'В каком городе?' and b.dlg.state == CLARIFY
    b.send_task('в москве')
    assert 'Москве' in b.w.said[-1][0] and has_more(b.w.said[-1][0])
    assert 'какая погода' in seen[-1]  # same chat: history passed


# ---------------------------------------------------------------- session search
def write_session(root, cwd, sid, title, prompts, age_h=1, ep='claude-vscode'):
    d = root / headless.encode_cwd(cwd); d.mkdir(parents=True, exist_ok=True)
    f = d / f'{sid}.jsonl'
    lines = [{'type': 'ai-title', 'aiTitle': title, 'sessionId': sid}]
    for p in prompts:
        lines.append({'type': 'user', 'cwd': cwd, 'entrypoint': ep, 'sessionId': sid, 'message': {'role': 'user', 'content': p}})
        lines.append({'type': 'assistant', 'cwd': cwd, 'sessionId': sid, 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'ок'}]}})
    f.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in lines) + '\n', encoding='utf-8')
    t = time.time() - age_h * 3600; os.utime(f, (t, t))
    return f


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(SI, 'PROJECTS', tmp_path)
    write_session(tmp_path, r'C:\work\voxcrab', 'aaaa1111-0000-0000-0000-000000000001', 'Голосовой помощник',
                  ['сделай крабика в виджете', 'крабик должен моргать', 'поправь голосовой виджет'], age_h=2)
    write_session(tmp_path, r'C:\work\sadik', 'bbbb2222-0000-0000-0000-000000000002', 'Плагин погоды',
                  ['почини плагин', 'добавь команду'], age_h=1)
    write_session(tmp_path, r'C:\work\voxcrab', 'cccc3333-0000-0000-0000-000000000003', 'Старый крабик',
                  ['крабик'], age_h=24 * 20)
    write_session(tmp_path, r'C:\work\voxcrab', 'dddd4444-0000-0000-0000-000000000004', 'крабик агент',
                  ['крабик крабик', 'крабик'], ep='sdk-cli')  # agent's -p run: hidden
    return tmp_path


def test_discovery_scoring(transcripts):
    c = SI.find('найди сессию, где мы работаем над крабиком')
    assert c[0]['id'].startswith('aaaa1111') and all(not x['id'].startswith('dddd') for x in c)
    assert SI.pick(c)[0] == 'one'
    assert SI.find('найди сессию про плагин погоды')[0]['id'].startswith('bbbb2222')
    assert SI.find('найди сессию') == []  # no keywords
    live = SI.find('найди сессию где крабик', live={'cccc3333-0000-0000-0000-000000000003': {'socket': 'p', 'token': 't'}})
    assert live[0]['live'] is (live[0]['id'].startswith('cccc'))


def test_find_transcript_only_offers_resume(bridge, transcripts, monkeypatch):
    b = bridge; ran = []
    monkeypatch.setattr(headless, 'run', lambda task, cwd, **k: ran.append((task, cwd, k)) or
                        {'session_id': k.get('resume'), 'result': 'Крабик теперь моргает.', 'is_error': False, 'transcript': None})
    b.find_session('найди сессию, где мы работаем над крабиком')
    t = b.w.said[-1][0]
    assert 'до подключения' in t and 'фоне' in t and b.dlg.state == CONFIRM
    b.send_task('покажи найденную сессию')
    assert 'aaaa1111' in b.w.said[-1][0] and 'фоне' in b.w.said[-1][0]
    assert b.dlg.state == CONFIRM  # "show" does not consume the offer
    b.send_task('да, продолжай в фоне'); assert b.w.said[-1][0] == 'Что передать?'
    b.send_task('сделай чтобы крабик моргал')
    deadline = time.time() + 5
    while not ran and time.time() < deadline: time.sleep(0.05)
    while not has_more(b.w.said[-1][0]) and time.time() < deadline: time.sleep(0.05)
    assert ran[0][0] == 'сделай чтобы крабик моргал' and ran[0][2]['resume'].startswith('aaaa1111')
    assert ran[0][1] == r'C:\work\voxcrab' and has_more(b.w.said[-1][0])


def test_find_live_sends_directly(bridge, transcripts):
    b = bridge; sid = 'aaaa1111-0000-0000-0000-000000000001'
    b.sc.sessions[sid] = {'cwd': r'C:\work\voxcrab', 'socket': 'p', 'token': 't'}
    b.find_session('найди сессию, где мы работаем над крабиком')
    assert 'на связи' in b.w.said[-1][0] and b.dlg.state == CLARIFY
    b.send_task('добавь крабику шапку'); assert b.sent[-1] == (sid, 'добавь крабику шапку')


def test_find_none_is_failure(bridge, transcripts):
    b = bridge; b.find_session('найди сессию про квантовый холодильник')
    assert 'Не нашёл' in b.w.said[-1][0] and b.dlg.state == FAILED


# ---------------------------------------------------------------- new session (headless)
def test_new_session_mocked(bridge, monkeypatch, tmp_path):
    b = bridge; calls = []
    class R: returncode = 0; stderr = ''
    def runner(cmd, **kw):
        calls.append((cmd, kw)); r = R()
        sid = cmd[cmd.index('--session-id') + 1]
        r.stdout = json.dumps({'session_id': sid, 'result': 'Создал файл README.', 'is_error': False}); return r
    real_run = headless.run
    monkeypatch.setattr(headless, 'run', lambda *a, **k: real_run(*a, runner=runner, **k))
    opened = []; monkeypatch.setattr(headless, 'open_in_vscode', lambda cwd, sid: opened.append((cwd, sid)) or True)
    b.w.cfg['open_new_in_vscode'] = True
    b.new_session(str(tmp_path), 'создай README')
    deadline = time.time() + 8
    while not opened and time.time() < deadline: time.sleep(0.05)
    cmd, kw = calls[0]
    assert cmd[1:4] == ['-p', '--output-format', 'json'] and cmd[-1] == 'создай README' and '--session-id' in cmd
    assert kw['cwd'] == str(tmp_path) and kw['env']['CLAUDE_CODE_ENTRYPOINT'] == 'claude-vscode' and 'VOICE_BRIDGE_OFF' not in kw['env']
    sid = cmd[cmd.index('--session-id') + 1]
    assert opened == [(str(tmp_path), sid)] and b.last_sid == sid
    assert 'README' in b.w.said[-1][0] and has_more(b.w.said[-1][0])  # no Stop hook in the test: result spoken by fallback
    assert headless.vscode_uri(sid) == 'vscode://anthropic.claude-code/open?session=' + sid


def test_new_session_error_is_failure(bridge, monkeypatch, tmp_path):
    b = bridge
    monkeypatch.setattr(headless, 'run', lambda *a, **k: {'session_id': k.get('session_id'), 'result': 'Credit balance is too low', 'is_error': True, 'transcript': None})
    b.new_session(str(tmp_path), 'x')
    deadline = time.time() + 5
    while 'ошибкой' not in b.w.said[-1][0] if b.w.said else True:
        if time.time() > deadline: break
        time.sleep(0.05)
    assert 'ошибкой' in b.w.said[-1][0] and not has_more(b.w.said[-1][0]) and b.dlg.state == FAILED


@pytest.mark.skipif(not shutil.which('claude') or os.environ.get('SKIP_E2E'), reason='claude CLI not available')
def test_e2e_headless_session_listed_for_vscode():
    """Real run: session transcript lands in ~/.claude/projects/<encoded testproj>/ with a non-sdk entrypoint
    (the VS Code extension hides sdk-cli/sdk-ts/sdk-py), and --resume keeps the same session id."""
    cwd = str(Path(__file__).resolve().parents[1] / 'vscode_bridge' / 'testproj')
    real_home = Path(os.environ.get('VB_REAL_HOME') or Path(os.environ['SystemDrive'] + '\\Users') / os.environ.get('USERNAME', ''))
    env_home = {k: os.environ[k] for k in ('USERPROFILE', 'HOME')}
    os.environ['USERPROFILE'] = os.environ['HOME'] = str(real_home)  # the CLI must use the real ~/.claude
    try:
        projects = real_home / '.claude' / 'projects'
        import uuid; sid = str(uuid.uuid4())
        r = headless.run('Ответь одним словом: готово', cwd, model='haiku', session_id=sid, timeout=180)
        assert r['session_id'] == sid and not r['is_error']
        f = projects / headless.encode_cwd(cwd) / f'{sid}.jsonl'
        assert f.exists(), f
        eps = {json.loads(l).get('entrypoint') for l in f.read_text(encoding='utf-8').splitlines() if '"entrypoint"' in l}
        assert eps == {'claude-vscode'}
        r2 = headless.run('Какое слово ты сказал? Одним словом.', cwd, model='haiku', resume=sid, timeout=180)
        assert r2['session_id'] == sid and not r2['is_error']
        print('E2E session', sid, f)
    finally:
        os.environ.update(env_home)
