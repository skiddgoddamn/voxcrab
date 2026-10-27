import queue, types
from dialog_state import BargeDetector


def run(det, frames, speaking=True, dt=0.032):
    """frames: (vad prob, rms). -> time of the first trigger or None."""
    t = 0.0
    for p, rms in frames:
        t += dt
        if det.feed(p, rms, speaking, t): return round(t, 2)
    return None


ECHO = [(0.9, 400)] * 60            # our own voice from the speakers: VAD is sure, but the level is constant
USER = [(0.95, 1400)] * 40


def test_echo_alone_is_not_an_answer():
    assert run(BargeDetector(), ECHO * 3) is None


def test_user_over_echo_triggers_after_min_duration():
    t = run(BargeDetector(), ECHO + USER)
    assert t is not None and t > 60 * 0.032 + 0.3           # not instantly: at least min_s of sustained speech


def test_short_blip_over_playback_is_ignored():
    assert run(BargeDetector(), ECHO + [(0.95, 1400)] * 4 + ECHO) is None


def test_quiet_vad_does_not_trigger_and_idle_resets():
    det = BargeDetector()
    assert run(det, ECHO + [(0.6, 1500)] * 40) is None       # below the raised threshold
    assert run(det, USER, speaking=False) is None and det.base is None


def test_no_playback_never_triggers_here():
    assert run(BargeDetector(), USER, speaking=False) is None


def _w(item):
    import widget
    return widget, types.SimpleNamespace(cur_item=item, redo=None, barge_hold=True, msgs=queue.Queue())


def test_unrecognised_answer_asks_the_same_question_again():
    q = ('Вопрос 2 из 3. Какой вариант?', True, {})
    widget, w = _w(q); widget.Widget.finish_barge(w, False)
    assert w.redo == q and w.barge_hold is False and w.cur_item is None


def test_recognised_answer_moves_on_and_a_plain_statement_is_not_repeated():
    q = ('Вопрос 2 из 3?', True, {})
    widget, w = _w(q); widget.Widget.finish_barge(w, True)
    assert w.redo is None and w.barge_hold is False
    widget, w = _w(('Готово.', False, {})); widget.Widget.finish_barge(w, False)
    assert w.redo is None
