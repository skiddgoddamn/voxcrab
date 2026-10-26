"""Availability, Cubism Core check, loopback server, renderer-process protocol, window-mode helpers, settings validation."""
import sys, threading, time, urllib.error, urllib.request
from pathlib import Path

import pytest

import engines
import settings_store
import winmode
from engines import web_server
from engines.webhost import WebHost

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- availability / Cubism Core
def test_gif_always_available_even_without_web_stack(monkeypatch):
    monkeypatch.setattr(engines, 'webview_ok', lambda: False)
    av = engines.availability({})
    assert av['gif'] == (True, '') and av['live2d'][0] is False and av['vrm'][0] is False and 'pywebview' in av['vrm'][1]


def test_live2d_needs_core_but_vrm_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, 'webview_ok', lambda: True)
    av = engines.availability({})
    assert av['vrm'][0] is True and av['live2d'][0] is False and 'live2dcubismcore' in av['live2d'][1]
    core = tmp_path / 'live2dcubismcore.min.js'; core.write_text('var Live2DCubismCore = {};')
    assert engines.availability({'live2d_core_path': str(core)})['live2d'] == (True, '')


@pytest.mark.parametrize('content,ok', [('var Live2DCubismCore={}', True), ('alert(1)', False)])
def test_check_core(tmp_path, content, ok):
    p = tmp_path / 'c.js'; p.write_text(content)
    assert engines.check_core(str(p))[0] is ok


def test_check_core_rejects_missing_wrong_type_empty(tmp_path):
    assert not engines.check_core('')[0] and not engines.check_core(str(tmp_path / 'nope.js'))[0]
    t = tmp_path / 'c.txt'; t.write_text('Live2DCubismCore'); assert 'live2dcubismcore.min.js' in engines.check_core(str(t))[1]


def test_missing_vendor_files_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, 'webview_ok', lambda: True); monkeypatch.setattr(engines, 'VENDOR', tmp_path)
    assert 'vrm.bundle.js' in engines.availability({})['vrm'][1]


def test_cubism_core_is_not_bundled():
    assert not list(engines.VENDOR.glob('*cubismcore*')) and not list(engines.WEB_DIR.rglob('*cubismcore*'))


# ---------------------------------------------------------------- loopback server
def serve(tmp_path):
    (tmp_path / 'model').mkdir(); (tmp_path / 'model' / 'a.json').write_text('{"ok":1}'); (tmp_path / 'secret.txt').write_text('secret')
    core = tmp_path / 'core.js'; core.write_text('var Live2DCubismCore={}')
    srv, port, tok = web_server.make_server(engines.WEB_DIR, engines.VENDOR, tmp_path / 'model', core)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d/%s/' % (port, tok), port


def status(url):
    try: return urllib.request.urlopen(url, timeout=5).status
    except urllib.error.HTTPError as e: return e.code


def test_server_serves_only_whitelisted_files(tmp_path):
    srv, base, port = serve(tmp_path)
    try:
        assert srv.server_address[0] == '127.0.0.1'
        assert status(base + 'index.html') == 200 and status(base + 'host.js') == 200 and status(base + 'm/a.json') == 200 and status(base + 'core.js') == 200
        assert status(base + 'v/pixi.min.js') == 200
        assert status(base + 'm/../secret.txt') == 404 and status(base + 'm/%2e%2e/secret.txt') == 404 and status(base + 'v/../host.js') == 404
        assert status('http://127.0.0.1:%d/wrong-token/index.html' % port) == 404 and status(base + 'README.md') == 404
    finally: srv.shutdown()


# ---------------------------------------------------------------- renderer process protocol (fake child)
def host(mode, events=None):
    return WebHost(on_event=(events.append if events is not None else None), argv=[sys.executable, str(HERE / 'fake_child.py'), mode])


def wait(pred, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if pred(): return True
        time.sleep(0.05)
    return False


def test_host_loads_and_switches_emotions():
    evs = []; h = host('ok', evs)
    h.start({'cmd': 'init', 'engine': 'vrm'}, 0, 0, 10, 10, '')
    try:
        assert wait(lambda: h.status == 'loaded') and h.info == {'mouth': True, 'blink': True} and h.poll() is None
        for emo in ('success', 'error', 'sleep'): assert h.send(cmd='emotion', name=emo)
        assert wait(lambda: [e['emotion'] for e in evs if e.get('ev') == 'echo'] == ['success', 'error', 'sleep'])
    finally: h.stop()
    assert h.status == 'idle' and not h.send(cmd='emotion', name='idle')


def test_host_reports_page_error():
    h = host('error'); h.start({'cmd': 'init'}, 0, 0, 10, 10, '')
    try:
        assert wait(lambda: h.status == 'failed') and 'Cubism Core' in h.poll()
    finally: h.stop()


def test_host_detects_dead_child():
    h = host('die'); h.start({'cmd': 'init'}, 0, 0, 10, 10, '')
    try: assert wait(lambda: h.poll() is not None) and 'завершился' in h.error
    finally: h.stop()


def test_host_start_timeout(monkeypatch):
    from engines import webhost
    monkeypatch.setattr(webhost, 'START_TIMEOUT', 0.3)
    h = host('hang'); h.start({'cmd': 'init'}, 0, 0, 10, 10, '')
    try: assert wait(lambda: h.poll() is not None) and 'не загрузилась' in h.error
    finally: h.stop()


def test_host_spawn_failure_is_a_failure_not_a_crash():
    h = WebHost(argv=['definitely-not-an-executable-xyz']); h.start({}, 0, 0, 1, 1, '')
    assert h.status == 'failed' and 'Не удалось запустить' in h.poll()


# ---------------------------------------------------------------- window modes (pure parts)
def test_fullscreen_policy():
    assert winmode.should_hide('exclusive', 'exclusive') and winmode.should_hide('presentation', 'exclusive')
    assert not winmode.should_hide('busy', 'exclusive') and winmode.should_hide('busy', 'any')
    assert not winmode.should_hide('exclusive', 'never') and not winmode.should_hide(None, 'any') and not winmode.should_hide('ok', 'any')


def test_pet_position_and_patrol():
    assert winmode.pet_position((0, 0, 1920, 1040), 300, 420, 5000) == (1620, 620)
    assert winmode.pet_position((0, 0, 1920, 1040), 300, 420, -50) == (0, 620)
    assert winmode.patrol_step(0, 100, 1.0, 40) == 40 and winmode.patrol_step(90, 100, 1.0, 40) == 100 and winmode.patrol_step(100, 0, 0.5, 40) == 80


def test_notification_state_runs_on_this_machine():
    assert winmode.notification_state() in (None, 'ok', 'busy', 'exclusive', 'presentation', 'quiet', 'app', 'not_present')


# ---------------------------------------------------------------- settings validation of the new keys
def test_new_settings_validated():
    clean, errs = settings_store.validate_changes({'model_scale': 999, 'render_fps': 1, 'window_mode': 'pet', 'compat_mode': 'bogus', 'anim_enabled': False,
                                                   'fullscreen_policy': 'any', 'pet_patrol': 'yes', 'live2d_core_path': ''})
    assert clean['model_scale'] == 200 and clean['render_fps'] == 10 and clean['window_mode'] == 'pet' and clean['anim_enabled'] is False
    assert clean['live2d_core_path'] is None and 'compat_mode' in errs and 'pet_patrol' in errs and clean['fullscreen_policy'] == 'any'


def test_old_config_without_new_keys_gets_defaults(tmp_path):
    (tmp_path / 'config.json').write_text('{"character": "cat", "mascot_scale": 7}')
    cfg = settings_store.load_config(tmp_path / 'config.json', dict(settings_store.NEW_DEFAULTS))
    assert cfg['character'] == 'cat' and cfg['mascot_scale'] == 7 and cfg['window_mode'] == 'normal' and cfg['anim_enabled'] is True and cfg['render_fps'] == 30
