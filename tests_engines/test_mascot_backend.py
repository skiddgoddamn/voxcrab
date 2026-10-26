"""Mascot backend switching with a real Tk root (skipped without a display): standard character without engines, fallback on every
failure, emotion forwarding to the renderer process, return to the standard character, animations off."""
import sys
from pathlib import Path

import pytest

import characters
import engines
import engines.webhost as webhost
import make_models as M
from engines import library

tk = pytest.importorskip('tkinter')
HERE = Path(__file__).resolve().parent


@pytest.fixture
def make(monkeypatch, tmp_path):
    import mascot
    created = []

    def factory(**cfg):
        base = {'character': 'crab', 'mascot_scale': 5, 'mascot_x': 100, 'mascot_y': 100, 'sleep_after_min': 0}; base.update(cfg)
        logs = []
        try: m = mascot.Mascot(base, log=lambda *a: logs.append(' '.join(map(str, a))))
        except tk.TclError: pytest.skip('no display')
        m.logs = logs; created.append(m); return m
    yield factory
    import gc
    for m in created:
        try: m._stop_web(final=True); m.root.destroy()
        except Exception: pass
        m.photos.clear(); m.anims.clear()
    created.clear(); gc.collect()      # free Tk objects on this (the Tk) thread, not in whichever thread the GC happens to run


def drain(m):
    while not m.q.empty():
        fn, a = m.q.get_nowait(); fn(*a)


def vrm_char(tmp_path, name='avatar'):
    return library.import_model('vrm', M.write_vrm(tmp_path / (name + '.vrm')), name)[0]


def use_fake_child(monkeypatch, mode, sent):
    class Fake(webhost.WebHost):
        def __init__(self, **kw): super().__init__(argv=[sys.executable, str(HERE / 'fake_child.py'), mode], **kw)
        def send(self, **cmd): sent.append(cmd); return super().send(**cmd)
    monkeypatch.setattr(webhost, 'WebHost', Fake)
    monkeypatch.setattr(engines, 'webview_ok', lambda: True)


def wait_for(m, pred, t=8.0):
    import time
    end = time.time() + t
    while time.time() < end:
        drain(m)
        if m.web: m._web_tick()   # what the animation timer does in the real widget
        if pred(): return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize('ch', ['crab', 'cat', 'robot'])
def test_standard_character_needs_no_engine(make, monkeypatch, ch):
    monkeypatch.setattr(engines, 'webview_ok', lambda: False)
    if True:
        m = make(character=ch)
        assert m.web is None and m.engine_status == ('gif', '') and (m.W, m.H) == (characters.GW * 5, characters.GH * 5)
        m._set_emotion('success'); m._render(); m._set_emotion('error'); m._render()
        assert m.emotion == 'error' and m._last is not None


def test_model_without_engine_falls_back_to_standard(make, monkeypatch, tmp_path):
    monkeypatch.setattr(engines, 'webview_ok', lambda: False)
    cid = vrm_char(tmp_path)
    m = make(character=cid)
    assert m.web is None and m.engine_status[0] == 'gif' and 'pywebview' in m.engine_status[1]
    assert m.bubble.visible and 'стандартного персонажа' in ''.join(m.bubble.lines)
    m._set_emotion('thinking'); m._render()           # still animates (the crab)
    assert m._last is not None


def test_live2d_without_core_falls_back(make, monkeypatch, tmp_path):
    monkeypatch.setattr(engines, 'webview_ok', lambda: True)
    cid = library.import_model('live2d', M.write_live2d(tmp_path / 'hero'), 'hero')[0]
    m = make(character=cid)
    assert m.web is None and 'live2dcubismcore' in m.engine_status[1]


def test_broken_model_files_fall_back(make, monkeypatch, tmp_path):
    use_fake_child(monkeypatch, 'ok', [])
    cid = vrm_char(tmp_path)
    (characters.find_dir(cid) / 'model' / 'avatar.vrm').write_bytes(b'broken')
    m = make(character=cid)
    assert m.web is None and m.engine_status[0] == 'gif' and m.engine_status[1]


def test_model_runs_in_renderer_and_gets_emotions(make, monkeypatch, tmp_path):
    sent = []; use_fake_child(monkeypatch, 'ok', sent)
    cid = vrm_char(tmp_path)
    m = make(character=cid, model_scale=100, render_fps=24, anim_enabled=True)
    assert m.web is not None and m.engine_status == ('vrm', '') and (m.W, m.H) == (300, 420)
    assert wait_for(m, lambda: any(c.get('cmd') == 'init' for c in sent))
    init = next(c for c in sent if c.get('cmd') == 'init')
    assert init['engine'] == 'vrm' and init['fps'] == 24 and set(init['profiles']) == set(characters.ALL_EMOTIONS) and init['profiles']['success']['expression'] == 'happy'
    assert wait_for(m, lambda: m.web.status == 'loaded')
    m.set_state('completed'); drain(m)                 # dialog state -> emotion -> renderer
    assert wait_for(m, lambda: {'cmd': 'emotion', 'name': 'success'} in sent)
    m.set_state('awaiting_clarification'); drain(m); m._web_tick()
    assert {'cmd': 'emotion', 'name': 'question'} in sent


def test_renderer_failure_falls_back_with_message(make, monkeypatch, tmp_path):
    use_fake_child(monkeypatch, 'error', [])
    m = make(character=vrm_char(tmp_path))
    assert wait_for(m, lambda: m.web is None, 10)
    assert m.engine_status[0] == 'gif' and 'Cubism Core' in m.engine_status[1] and (m.W, m.H) == (characters.GW * 5, characters.GH * 5)
    m._set_emotion('success'); m._render()


def test_return_to_standard_character(make, monkeypatch, tmp_path):
    use_fake_child(monkeypatch, 'ok', [])
    m = make(character=vrm_char(tmp_path))
    assert m.web is not None
    m.cfg['character'] = 'crab'; m._apply_look(True)
    assert m.web is None and m.engine_status == ('gif', '') and (m.W, m.H) == (characters.GW * 5, characters.GH * 5)


def test_animations_off_gives_static_frame(make):
    m = make(character='crab', anim_enabled=False)
    m._render(); first = m._last
    import time; m.t0 -= 5; m._last = None; m._render()
    assert first == m._last and first[1] == 0


def test_pet_mode_snaps_to_taskbar_edge(make, monkeypatch, tmp_path):
    import winmode
    monkeypatch.setattr(winmode, 'work_area', lambda: (0, 0, 1920, 1000))
    m = make(character='crab', window_mode='pet')
    m._pet_snap()
    assert m.y == 1000 - m.H


def test_tts_amplitude_is_forwarded_to_the_renderer(make, monkeypatch, tmp_path):
    sent = []; use_fake_child(monkeypatch, 'ok', sent)
    m = make(character=vrm_char(tmp_path))
    assert wait_for(m, lambda: m.web.status == 'loaded')
    m.set_mouth(0.6); m.set_mouth(None); drain(m)
    assert {'cmd': 'mouth', 'level': 0.6} in sent and {'cmd': 'mouth', 'level': None} in sent


def test_click_through_setting_reaches_the_renderer(make, monkeypatch, tmp_path):
    sent = []; use_fake_child(monkeypatch, 'ok', sent)
    m = make(character=vrm_char(tmp_path), click_through=False)
    assert wait_for(m, lambda: m.web.status == 'loaded')
    m.cfg['click_through'] = True; m._apply_look(True)
    assert {'cmd': 'clickthrough', 'value': True} in sent


def test_prewarmed_renderer_is_reused_for_the_first_model(make, monkeypatch, tmp_path):
    sent = []; use_fake_child(monkeypatch, 'ok', sent)
    cid = vrm_char(tmp_path)
    m = make(character='crab')
    assert m.web is None
    m._prewarm()
    assert m.host is not None and wait_for(m, lambda: m.host.status == 'warm')
    pid = m.host.proc.pid
    m.cfg['character'] = cid; m._apply_look(True)
    assert m.web is m.host and wait_for(m, lambda: m.web.status == 'loaded') and m.host.proc.pid == pid
    assert any(c.get('cmd') == 'serve' for c in sent) and any(c.get('cmd') == 'place' for c in sent)
    m.cfg['character'] = 'crab'; m._apply_look(True)           # back to the standard character: the process stays warm
    assert m.web is None and wait_for(m, lambda: m.host.status == 'warm') and m.host.alive() and m.host.proc.pid != pid


def test_prewarm_off_or_without_models_starts_nothing(make, monkeypatch, tmp_path):
    use_fake_child(monkeypatch, 'ok', [])
    m = make(character='crab'); m._prewarm(); assert m.host is None            # no models installed
    vrm_char(tmp_path); m.cfg['prewarm_engine'] = False; m._prewarm(); assert m.host is None
