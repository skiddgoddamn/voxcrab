"""Click-through hit mask, runtime re-pointing of the loopback server, warm renderer reuse."""
import sys, threading, time, urllib.request
from pathlib import Path

import engines
from engines import hitmask, web_server
from engines.webhost import WebHost

HERE = Path(__file__).resolve().parent


def wait(pred, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if pred(): return True
        time.sleep(0.05)
    return False


def test_mask_hit_and_neighbourhood():
    bits = '0' * 30 + '0001100000' + '0' * 60            # 10x10, opaque cells (3,3) and (4,3)
    m = hitmask.Mask(10, 10, bits)
    assert m.hit(35, 35, 100, 100) and m.hit(45, 35, 100, 100)
    assert m.hit(20, 35, 100, 100)                       # 1-cell margin
    assert not m.hit(5, 5, 100, 100) and not m.hit(90, 90, 100, 100)
    assert not m.hit(-1, 5, 100, 100) and not m.hit(100, 5, 100, 100) and not m.hit(5, 5, 0, 0)


def test_mask_scales_with_window_size():
    m = hitmask.Mask(4, 4, '0000' '0110' '0110' '0000')
    assert m.hit(150, 150, 300, 300) and not m.hit(10, 10, 300, 300, grow=0) and m.hit(300, 300, 600, 600)


def test_bad_mask_is_ignored():
    for args in ((0, 0, ''), (4, 4, '01'), (4, 4, None), ('a', 4, '0' * 16), (9999, 1, '0' * 9999)):
        assert not hitmask.Mask(*args).hit(1, 1, 10, 10)


def test_server_can_be_repointed_at_runtime(tmp_path):
    a = tmp_path / 'a'; b = tmp_path / 'b'; a.mkdir(); b.mkdir(); (a / 'x.json').write_text('A'); (b / 'x.json').write_text('B')
    srv, port, tok = web_server.make_server(engines.WEB_DIR, engines.VENDOR, a)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/%s/m/x.json' % (port, tok)
    try:
        assert urllib.request.urlopen(url).read() == b'A'
        srv.conf['model_dir'] = b
        assert urllib.request.urlopen(url).read() == b'B'
    finally: srv.shutdown()


def fake(events=None):
    return WebHost(on_event=(events.append if events is not None else None), argv=[sys.executable, str(HERE / 'fake_child.py'), 'ok'])


def test_warm_process_is_reused_and_kept_between_models():
    evs = []; h = fake(evs)
    h.warm()
    try:
        assert wait(lambda: h.status == 'warm') and h.alive()
        pid = h.proc.pid
        h.start({'cmd': 'init', 'engine': 'vrm'}, 10, 20, 300, 420, 'D:/models/a', core_path='C:/core.js', click_through=True)
        assert wait(lambda: h.status == 'loaded') and h.proc.pid == pid
        echoes = [e for e in evs if e.get('ev') == 'echo']
        assert any(e['cmd'] == 'serve' and e['args']['model_dir'] == 'D:/models/a' and e['args']['core'] == 'C:/core.js' for e in echoes)
        assert any(e['cmd'] == 'place' and (e['args']['x'], e['args']['w']) == (10, 300) for e in echoes)
        h.stop(keep_warm=True)
        assert h.alive() and h.proc.pid != pid
        assert wait(lambda: h.status == 'warm' and h.page_ready)
        pid = h.proc.pid
        h.start({'cmd': 'init', 'engine': 'vrm'}, 0, 0, 300, 420, 'D:/models/b')
        assert wait(lambda: h.status == 'loaded') and h.proc.pid == pid
    finally: h.stop()
    assert h.status == 'idle' and not h.alive()


def test_start_while_still_warming_waits_for_the_page():
    h = fake(); h.warm()
    h.start({'cmd': 'init', 'engine': 'vrm'}, 0, 0, 10, 10, '')
    try: assert wait(lambda: h.status == 'loaded')
    finally: h.stop()


def test_failed_or_dead_warm_process_is_replaced_by_a_cold_start():
    h = fake(); h.warm()
    try:
        assert wait(lambda: h.status == 'warm')
        h.proc.kill(); h.proc.wait()
        assert wait(lambda: h.poll() is not None)
        h.start({'cmd': 'init', 'engine': 'vrm'}, 0, 0, 10, 10, '')
        assert wait(lambda: h.status == 'loaded')
    finally: h.stop()


def test_stop_without_keep_warm_kills_the_process():
    h = fake(); h.warm()
    assert wait(lambda: h.status == 'warm'); p = h.proc
    h.stop()
    assert p.poll() is not None and h.status == 'idle'


def test_clean_box_rejects_garbage():
    from engines.hitmask import clean_box
    assert clean_box([0.2, 0.3, 0.8, 0.95]) == (0.2, 0.3, 0.8, 0.95)
    for bad in (None, [], [0, 0, 1], [0.5, 0.5, 0.5, 0.5], [-0.1, 0, 1, 1], [0, 0, 2, 1], ['a', 0, 1, 1], [0.1, 0.1, 0.11, 0.9]): assert clean_box(bad) is None


def test_head_anchor_follows_the_visible_model_not_the_window():
    from engines.hitmask import head_anchor
    # small chibi at the bottom of a 300x420 window at (100, 200): head top at 55% of the height, body centred slightly right
    assert head_anchor([0.3, 0.55, 0.7, 0.98], 100, 200, 300, 420) == (100 + 150, 200 + int(0.55 * 420))
    # model filling the window -> near the top, like before
    assert head_anchor([0.05, 0.04, 0.95, 0.98], 0, 0, 300, 420)[1] == int(0.04 * 420)
    # no box / broken box -> the old window-relative anchor
    assert head_anchor(None, 100, 200, 300, 420) == (250, 200 + int(420 * 0.06)) == head_anchor([0, 0, 0, 0], 100, 200, 300, 420)
