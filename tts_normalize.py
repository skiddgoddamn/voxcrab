"""Text -> text that a Russian-only TTS can read. Silero v5_ru has no Latin letters and no digits in its alphabet, so they were silently skipped.
normalize() is applied right before synthesis only; the bubble keeps the original text.

  numbers   42 -> сорок два, -5 -> минус пять, 2.5 / 2,5 -> две целых пять десятых, 50% -> пятьдесят процентов, 3-й -> третий, 12:30
  versions  "версия 2.5", v1.2.3 -> два точка пять (digits read separately around dots)
  Latin     known terms from TERMS (Claude -> клод), short ALLCAPS acronyms letter by letter (API -> эй пи ай), other words by phonetic transliteration
"""
import re

ONES_M = ['ноль', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять', 'десять', 'одиннадцать', 'двенадцать', 'тринадцать',
          'четырнадцать', 'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
TENS = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']
HUNDREDS = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот']
SCALES = [('тысяча', 'тысячи', 'тысяч', 'f'), ('миллион', 'миллиона', 'миллионов', 'm'), ('миллиард', 'миллиарда', 'миллиардов', 'm'),
          ('триллион', 'триллиона', 'триллионов', 'm')]


def _triple(n, gender):
    out = [HUNDREDS[n // 100]] if n >= 100 else []
    n %= 100
    if n >= 20: out.append(TENS[n // 10]); n %= 10
    if n:
        w = ONES_M[n]
        if n == 1 and gender == 'f': w = 'одна'
        elif n == 1 and gender == 'n': w = 'одно'
        elif n == 2 and gender == 'f': w = 'две'
        out.append(w)
    return [w for w in out if w]


def _plural(n, forms):
    n %= 100
    if 11 <= n <= 19: return forms[2]
    return forms[{1: 0, 2: 1, 3: 1, 4: 1}.get(n % 10, 2)]


def cardinal(n, gender='m'):
    if n == 0: return 'ноль'
    if n >= 10 ** 15: return ' '.join(ONES_M[int(d)] for d in str(n))
    parts, groups = [], []
    while n: groups.append(n % 1000); n //= 1000
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g: continue
        if i == 0: parts += _triple(g, gender)
        else:
            one, two, many, sg = SCALES[i - 1]
            parts += _triple(g, sg) + [_plural(g, (one, two, many))]
    return ' '.join(parts)


# ---- ordinals (simple: 1..99 and round hundreds), inflected by the written ending
ORD_ONES = ['нулевой', 'первый', 'второй', 'третий', 'четвёртый', 'пятый', 'шестой', 'седьмой', 'восьмой', 'девятый', 'десятый', 'одиннадцатый', 'двенадцатый',
            'тринадцатый', 'четырнадцатый', 'пятнадцатый', 'шестнадцатый', 'семнадцатый', 'восемнадцатый', 'девятнадцатый']
ORD_TENS = ['', '', 'двадцатый', 'тридцатый', 'сороковой', 'пятидесятый', 'шестидесятый', 'семидесятый', 'восьмидесятый', 'девяностый']
ENDINGS = {'й': '', 'я': 'ая', 'е': 'ое', 'го': 'ого', 'му': 'ому', 'м': 'ом', 'х': 'ых', 'ю': 'ую', 'ой': 'ой', 'ая': 'ая', 'ый': '', 'ое': 'ое', 'ую': 'ую', 'ого': 'ого'}


def _inflect(word, ending):
    e = ENDINGS.get(ending, '')
    if not e: return word                                  # nominative masculine
    if word == 'третий':
        return {'ая': 'третья', 'ое': 'третье', 'ого': 'третьего', 'ому': 'третьему', 'ом': 'третьем', 'ых': 'третьих', 'ую': 'третью', 'ой': 'третьей'}.get(e, word)
    return word[:-2] + e


def ordinal(n, ending='й'):
    if n < 20: w = ORD_ONES[n]; return _inflect(w, ending)
    if n < 100:
        t, o = divmod(n, 10)
        if o == 0: return _inflect(ORD_TENS[t], ending)
        return TENS[t] + ' ' + _inflect(ORD_ONES[o], ending)
    if n == 100: return _inflect('сотый', ending)
    return cardinal(n)                                      # big ordinals: read as a cardinal, still audible


def _decimal(int_part, frac, signed=False):
    n = int(int_part)
    ip = cardinal(n, 'f')
    whole = 'целая' if n % 10 == 1 and n % 100 != 11 else 'целых'
    k = len(frac)
    names = {1: ('десятая', 'десятых'), 2: ('сотая', 'сотых'), 3: ('тысячная', 'тысячных')}
    f = int(frac)
    if k in names:
        fw = names[k][0] if f % 10 == 1 and f % 100 != 11 else names[k][1]
        return '%s %s %s %s' % (ip, whole, cardinal(f, 'f'), fw)
    return '%s точка %s' % (cardinal(n), ' '.join(ONES_M[int(d)] for d in frac))


def _num(m):
    return cardinal(int(m.group(0)))


def normalize_numbers(t):
    t = re.sub(r'(?i)\b(версия|версии|версию|version|ver|v)\.?\s*(\d+(?:[.,]\d+)+)',
               lambda m: ('версия ' if m.group(1).lower() in ('v', 'ver', 'version') else m.group(1) + ' ') + ' точка '.join(cardinal(int(x)) for x in re.split(r'[.,]', m.group(2))), t)
    t = re.sub(r'(?<![\w.,])(\d+(?:\.\d+){2,})(?![\w])', lambda m: ' точка '.join(cardinal(int(x)) for x in m.group(1).split('.')), t)   # 1.2.3
    t = re.sub(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)', lambda m: cardinal(int(m.group(1))) + ' ' + (('ноль ' if m.group(2)[0] == '0' else '') + cardinal(int(m.group(2))) if m.group(2) != '00' else 'ровно'), t)
    t = re.sub(r'(?<![\w.,])-(?=\d)', 'минус ', t)
    t = re.sub(r'(\d+)[.,](\d{1,6})\s*%', lambda m: _decimal(m.group(1), m.group(2)) + ' процента', t)
    t = re.sub(r'(\d+)\s*%',lambda m: cardinal(int(m.group(1))) + ' ' + _plural(int(m.group(1)), ('процент', 'процента', 'процентов')), t)
    t = re.sub(r'(?<![\w.,])(\d+)[.,](\d{1,6})(?![\d])', lambda m: _decimal(m.group(1), m.group(2)), t)
    t = re.sub(r'(\d+)-?(й|я|е|го|му|м|х|ю|ая|ой|ый|ое|ую|ого)\b', lambda m: ordinal(int(m.group(1)), m.group(2)) if int(m.group(1)) < 100000 else m.group(0), t)
    return re.sub(r'\d+', _num, t)


# ---------------------------------------------------------------- Latin
TERMS = {
    'claude': 'клод', 'anthropic': 'антропик', 'api': 'эй пи ай', 'ok': 'окей', 'okay': 'окей', 'github': 'гитхаб', 'git': 'гит', 'python': 'пайтон',
    'javascript': 'джаваскрипт', 'typescript': 'тайпскрипт', 'java': 'джава', 'json': 'джейсон', 'html': 'эйч ти эм эль', 'css': 'си эс эс', 'sql': 'эс кью эль',
    'http': 'эйч ти ти пи', 'https': 'эйч ти ти пи эс', 'url': 'ю ар эль', 'ui': 'ю ай', 'ai': 'эй ай', 'gpt': 'джи пи ти', 'cpu': 'си пи ю', 'gpu': 'джи пи ю',
    'vs': 'ви эс', 'code': 'код', 'vscode': 'ви эс код', 'windows': 'виндовс', 'linux': 'линукс', 'docker': 'докер', 'npm': 'эн пи эм', 'node': 'нод', 'nodejs': 'нод джей эс',
    'agent': 'эйджент', 'widget': 'виджет', 'sonnet': 'сонет', 'haiku': 'хайку', 'opus': 'опус', 'tts': 'ти ти эс', 'stt': 'эс ти ти', 'wifi': 'вай фай', 'usb': 'ю эс би',
    'pdf': 'пи ди эф', 'id': 'ай ди', 'ip': 'ай пи', 'ssh': 'эс эс эйч', 'pr': 'пи ар', 'ci': 'си ай', 'cd': 'си ди', 'ux': 'ю экс', 'app': 'апп', 'bug': 'баг', 'fix': 'фикс',
    'test': 'тест', 'tests': 'тесты', 'build': 'билд', 'commit': 'коммит', 'push': 'пуш', 'pull': 'пулл', 'merge': 'мёрдж', 'branch': 'бранч', 'main': 'мейн', 'error': 'эррор',
}
LETTERS = {'a': 'эй', 'b': 'би', 'c': 'си', 'd': 'ди', 'e': 'и', 'f': 'эф', 'g': 'джи', 'h': 'эйч', 'i': 'ай', 'j': 'джей', 'k': 'кей', 'l': 'эль', 'm': 'эм', 'n': 'эн',
           'o': 'оу', 'p': 'пи', 'q': 'кью', 'r': 'ар', 's': 'эс', 't': 'ти', 'u': 'ю', 'v': 'ви', 'w': 'дабл ю', 'x': 'экс', 'y': 'уай', 'z': 'зед'}
DIGRAPHS = [('tch', 'ч'), ('sch', 'ск'), ('igh', 'ай'), ('tion', 'шн'), ('sion', 'жн'), ('ough', 'ау'), ('eau', 'о'), ('sh', 'ш'), ('ch', 'ч'), ('th', 'з'), ('ph', 'ф'), ('ck', 'к'),
            ('qu', 'кв'), ('oo', 'у'), ('ee', 'и'), ('ea', 'и'), ('ou', 'ау'), ('ow', 'ау'), ('ai', 'эй'), ('ay', 'эй'), ('oa', 'оу'), ('oi', 'ой'), ('oy', 'ой'), ('au', 'о'),
            ('aw', 'о'), ('ew', 'ью'), ('ie', 'и'), ('ei', 'эй'), ('wh', 'в'), ('kn', 'н'), ('wr', 'р'), ('gh', 'г'), ('ng', 'нг'), ('ts', 'ц')]
SINGLE = {'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г', 'h': 'х', 'i': 'и', 'j': 'дж', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п',
          'q': 'к', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс', 'y': 'и', 'z': 'з'}


def translit(word):
    w = word.lower(); out = []; i = 0
    if len(w) > 3 and w.endswith('e') and w[-2] not in 'aeiouy': w = w[:-1]       # silent final e (name, code)
    while i < len(w):
        for dg, ru in DIGRAPHS:
            if w.startswith(dg, i): out.append(ru); i += len(dg); break
        else:
            c = w[i]
            if c == 'c' and i + 1 < len(w) and w[i + 1] in 'eiy': out.append('с')
            elif c == 'y' and i == 0: out.append('й')
            else: out.append(SINGLE.get(c, ''))
            i += 1
    return ''.join(out)


def _latin_word(w):
    lw = w.lower()
    if lw in TERMS: return TERMS[lw]
    if w.isupper() and 2 <= len(w) <= 5: return ' '.join(LETTERS[c.lower()] for c in w)           # short acronym: letter by letter
    parts = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+', w)                                        # CamelCase -> words
    if len(parts) > 1: return ' '.join(_latin_word(p) for p in parts)
    if len(w) == 1: return LETTERS[lw]
    return translit(w)


def normalize_latin(t):
    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", lambda m: _latin_word(m.group(0).replace("'", '')), t)


def normalize(text):
    t = normalize_numbers(str(text))
    t = normalize_latin(t)
    return re.sub(r'[ \t]+', ' ', t).strip()
