"""TTS text normalisation: Silero v5_ru is Russian-only, digits and Latin were skipped. Output must contain only Cyrillic, spaces and punctuation."""
import re

import pytest

import tts_normalize as T


@pytest.mark.parametrize('src,expected', [
    ('42', 'сорок два'), ('0', 'ноль'), ('101', 'сто один'), ('2021', 'две тысячи двадцать один'), ('1000000', 'один миллион'),
    ('21000', 'двадцать одна тысяча'), ('12000', 'двенадцать тысяч'), ('-5', 'минус пять'), ('осталось -5 градусов', 'осталось минус пять градусов'),
    ('3.14', 'три целых четырнадцать сотых'), ('2,5', 'две целых пять десятых'), ('1.1', 'одна целая одна десятая'), ('50%', 'пятьдесят процентов'),
    ('21%', 'двадцать один процента'.replace('процента', 'процент')), ('2,5%', 'две целых пять десятых процента'),
    ('12:30', 'двенадцать тридцать'), ('9:05', 'девять ноль пять'), ('версия 2.5', 'версия два точка пять'), ('v1.2.3', 'версия один точка два точка три'),
    ('3-й раз', 'третий раз'), ('21-го числа', 'двадцать первого числа'), ('5-я', 'пятая'), ('2-му', 'второму'),
])
def test_numbers(src, expected):
    assert T.normalize(src) == expected


@pytest.mark.parametrize('src,expected', [
    ('Claude', 'клод'), ('API', 'эй пи ай'), ('ok', 'окей'), ('GitHub', 'гитхаб'), ('XYZ', 'экс уай зед'), ('Claude, версия 2.5, API ок', 'клод, версия два точка пять, эй пи ай ок'),
])
def test_latin_known_terms_and_acronyms(src, expected):
    assert T.normalize(src) == expected


def test_unknown_latin_words_are_transliterated_and_camelcase_split():
    for w in ('banana', 'Sheriff', 'FooBar', 'netflix', 'mouse'): assert re.fullmatch(r'[а-яё ]+', T.normalize(w)), w
    assert T.normalize('FooBar') == 'фу бар'
    assert T.normalize('Shop') == 'шоп'


def test_everything_is_speakable():
    text = 'Claude 4.5 запустил 12 тестов (API v2.0): 99% ok, файл main.py, IP 192.168.0.1, 3-го июня в 14:05, цена -7,25 USD'
    out = T.normalize(text)
    assert not re.search(r'[A-Za-z0-9]', out), out
    assert re.fullmatch(r'[а-яё ,.:()-]+', out), out


def test_russian_text_and_empty_unchanged():
    assert T.normalize('Привет, как дела?') == 'Привет, как дела?' and T.normalize('') == '' and T.normalize('   ') == ''


def test_huge_number_is_read_digit_by_digit():
    assert T.cardinal(10 ** 15) == 'один ' + ' '.join(['ноль'] * 15)


def test_displayed_text_is_not_touched_by_widget_tts_wrapper():
    import inspect, widget
    src = inspect.getsource(widget.TTS.synth)
    assert 'tts_normalize.normalize(text)' in src and 'show_message' not in src
