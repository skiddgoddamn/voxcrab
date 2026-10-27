import queue, threading, time, types
import pytest
import winmode


def W(**kw):
    import widget
    ns = types.SimpleNamespace(
        msgs=queue.Queue(), cur_item=None, redo=None, paused=False, followup=True, stop_speak=threading.Event(), recording=False, cancel_rec=False,
        muted=False, auto_muted=False, ptt=False, said=[], cfg={}, _mic_chk=0.0, ui=types.SimpleNamespace(release=lambda *a: None, set_muted=lambda on: None),
        set_state=lambda s: None, icon=None)
    ns.say_event = lambda t, ask=False, extra=None: ns.said.append(t)
    ns.__dict__.update(kw); return widget, ns


@pytest.mark.parametrize('t', ['стоп', 'хватит', 'замолчи', 'тихо', 'стоп пожалуйста', 'хватит уже', 'помолчи'])
def test_stop_words(t):
    import widget; assert widget.STOPCMD.match(t)


@pytest.mark.parametrize('t', ['стоп машина и дальше работаем', 'остановись и подумай', 'не хватит', 'расскажи тихо'])
def test_not_stop(t):
    import widget; assert not widget.STOPCMD.match(t)


@pytest.mark.parametrize('t', ['продолжай', 'дальше', 'давай дальше', 'читай дальше'])
def test_resume_words(t):
    import widget; assert widget.RESUMECMD.match(t)


def test_voice_stop_keeps_the_interrupted_message_and_the_queue():
    widget, w = W(cur_item=('Вопрос 1?', True, {}))
    w.msgs.put(('Вопрос 2?', True, {})); w.msgs.put(('Вопрос 3?', True, {}))
    widget.Widget.pause_speech(w)
    assert w.paused and w.stop_speak.is_set() and w.followup is False
    assert w.redo == ('Вопрос 1?', True, {}) and w.msgs.qsize() == 2        # nothing is dropped
    widget.Widget.resume_speech(w)
    assert not w.paused and w.redo is not None                                # the speaker re-reads question 1 first, then 2 and 3


def test_resume_with_nothing_pending_says_so():
    widget, w = W()
    widget.Widget.resume_speech(w); assert w.msgs.get_nowait()[0] == 'Больше ничего нет.'


def test_mute_closes_listening_and_speaks_confirmation():
    widget, w = W(recording=True)
    widget.Widget.set_muted(w, True)
    assert w.muted and w.followup is False and w.cancel_rec and w.said == ['Не слушаю.']
    widget.Widget.set_muted(w, False); assert not w.muted and w.said[-1] == 'Слушаю.'


def test_auto_mute_follows_discord_and_never_overrides_manual_mute(monkeypatch):
    widget, w = W(cfg={'mute_on_call': True})
    busy = {'v': True}; monkeypatch.setattr(winmode, 'mic_busy_by', lambda names=(): busy['v'])
    bound = lambda f: types.MethodType(f, w)
    w.set_muted = bound(widget.Widget.set_muted)
    widget.Widget.check_auto_mute(w); assert w.muted and w.auto_muted
    busy['v'] = False; w._mic_chk = 0; widget.Widget.check_auto_mute(w); assert not w.muted
    w.set_muted(True); busy['v'] = False; w._mic_chk = 0       # manual mute stays on after the call ends
    widget.Widget.check_auto_mute(w); assert w.muted and not w.auto_muted
    w.cfg['mute_on_call'] = False; w.set_muted(False); busy['v'] = True; w._mic_chk = 0
    widget.Widget.check_auto_mute(w); assert not w.muted        # default off


def test_mic_users_parses_the_consent_store():
    e = [('C:#Users#a#AppData#Local#Discord#app-1#Discord.exe', 133, 0), ('c:#x#chrome.exe', 133, 0), ('C:#Discord#Discord.exe', 133, 200), ('discord_old', 0, 0)]
    assert winmode.mic_users(e) == ['c:#users#a#appdata#local#discord#app-1#discord.exe']
