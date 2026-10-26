import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_voice import interpret_project

NAMES = ['claude', 'sadik', 'blogFrontend', 'boxBackend', 'docsFrontend', 'mailBackend']

def test_any():
    for t in ['Да ни в каком, просто любой создай', 'в новой сессии', 'в новом', 'без разницы']:
        assert interpret_project(t, NAMES)[0] == 'default', t

def test_translit():
    assert interpret_project('в проекте клод', NAMES) == ('match', 'claude')
    assert interpret_project('садик', NAMES) == ('match', 'sadik')
    assert interpret_project('бокс бэкенд', NAMES) == ('match', 'boxBackend')

def test_ambiguous_keeps_task():
    k, v = interpret_project('фронтенд', NAMES)
    assert k == 'ambiguous' and len(v) == 3
    assert interpret_project('хмм', NAMES)[0] == 'ambiguous'

from bridge_voice import parse_route
C = [{'id': 'a'}, {'id': 'b'}]

def test_parse_route():
    assert parse_route('x {"target":null,"intent":"new","confidence":0.9,"complexity":"trivial","clear":true,"restatement":"погода"} y', C) == (None, True, 'Погода', 'new', 'trivial', '')
    r = parse_route('{"target":2,"intent":"session","confidence":0.9,"complexity":"trivial"}', C)
    assert r[0] == 'b' and r[4] == 'normal'
    assert parse_route('{"target":9,"confidence":0.3}', C)[:2] == (None, False)
    assert parse_route('{"target":null}', C)[3:5] == ('new', 'normal')

from bridge_voice import unfill
def test_unfill():
    assert unfill('Понял: открыл файл') == 'Открыл файл'
    assert unfill('Хорошо, Окей. Готово') == 'Готово'
    assert unfill('Понятно что делать') == 'Понятно что делать'

from bridge_voice import make_ack, is_question
def test_ack_and_question():
    assert parse_route('{"target":null,"complexity":"trivial","ack":"Понял, ищу погоду."}', C)[5] == 'Ищу погоду'
    assert make_ack('trivial', 'Ищу погоду') == 'Ищу погоду'
    assert 'проект' in make_ack('session', proj='проект')
    assert is_question('Назовите город?  ') and not is_question('Готово.')

from bridge_voice import NEG, Bridge
def test_more_and_neg():
    from dialog_state import NEG, Dialog
    assert NEG.match('спасибо большое') and NEG.match('нет') and not NEG.match('нету времени, открой файл')
    d = Dialog(); d.start('s')
    r = d.complete('Готово.'); assert r.startswith('Готово. ') and r.endswith('?')
    d.start('s'); assert d.complete('Готово.') == 'Готово.'  # at most once per 2 min
    assert Dialog().complete('Какой город?') == 'Какой город?'
    assert Dialog(ask_more=False).complete('Готово.') == 'Готово.'
