import pytest
from dialog_state import CLARIFY, Dialog, is_repeat


@pytest.mark.parametrize('t', ['повтори', 'Повтори ещё раз', 'повтори еще раз', 'ещё раз', 'Ещё раз.', 'что?', 'Что', 'не расслышал', 'не расслышала', 'скажи ещё раз',
                               'повтори пожалуйста', 'что ты сказал', 'эй повтори'])
def test_repeat_phrases(t): assert is_repeat(t)


@pytest.mark.parametrize('t', ['что там', 'что нового', 'что делает этот код', 'открой проект', 'повтори тест три раза в файле', 'как дела', ''])
def test_not_repeat(t): assert not is_repeat(t)


def test_reask_keeps_state_of_expired_question():
    d = Dialog(clarify_timeout=1)
    d.ask('project', 'В какой проект?')
    assert d.tick(now=d.pending.t + 5) and d.pending is None
    p = d.reask()
    assert p and p.text == 'В какой проект?' and d.pending is p and d.state == CLARIFY


def _widget_repeat(last_ask, last_said, dlg):
    import queue, types
    import widget
    w = types.SimpleNamespace(msgs=queue.Queue(), last_ask=last_ask, last_said=last_said, bridge=types.SimpleNamespace(dlg=dlg))
    widget.Widget.repeat(w)
    return w.msgs.get_nowait()


def test_repeat_pending_question_keeps_state():
    d = Dialog(); d.ask('project', 'В какой проект?')
    q = ('В какой проект?', True, {}); said = ('Готово.', False, {})
    assert _widget_repeat(q, said, d) == q
    assert d.pending is not None and d.state == CLARIFY


def test_repeat_plain_statement_and_nothing():
    d = Dialog(); said = ('Готово, всё сделал.', False, {})
    assert _widget_repeat(('старый вопрос?', True, {}), said, d) == said     # answered question is not replayed
    assert _widget_repeat(None, None, d)[0].startswith('Мне пока нечего')


def test_repeat_reopens_expired_question():
    d = Dialog(clarify_timeout=1); d.ask('project', 'Какой проект?'); d.tick(now=d.pending.t + 5)
    q = ('Какой проект?', True, {})
    assert _widget_repeat(q, q, d) == q and d.pending is not None
