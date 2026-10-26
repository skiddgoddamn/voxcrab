"""Wake word: grammar build (word + phonetic variants + decoys), vocabulary / conflict checks, detector.
Pure functions take `find` (word -> vocab id, -1 when the model does not know it) so tests need no Vosk model."""
import json, re
from pathlib import Path

import paths
HERE = paths.HERE
VOSK_DIR = paths.DATA / 'models' / 'vosk' / 'vosk-model-small-ru-0.22'
SR = 16000

# decoy words: with a grammar of only wake words + [unk] the small Vosk model maps almost any sound to the wake word
DECOYS = ['алло', 'алё', 'привет', 'да', 'нет', 'ну', 'так', 'что', 'это', 'и', 'в', 'на', 'я', 'ты', 'он', 'не', 'как', 'ладно', 'давай', 'хорошо', 'окей',
          'слушай', 'смотри', 'сейчас', 'тест', 'раз', 'два', 'три', 'проверка', 'сколько', 'где', 'когда', 'почему', 'кто', 'вот', 'там', 'тут', 'здесь', 'один', 'ещё',
          'код', 'клад', 'клуб', 'плод', 'вход', 'ход', 'холод', 'голод', 'коль', 'клен', 'глод', 'клавиатура', 'кот', 'клип', 'класс', 'слово', 'можно', 'нужно']
# frequent speech words: a wake word that is one of them fires all the time
COMMON = {'да', 'нет', 'ну', 'так', 'что', 'это', 'как', 'вот', 'там', 'тут', 'где', 'кто', 'все', 'всё', 'еще', 'ещё', 'уже', 'или', 'если', 'тоже', 'очень',
          'можно', 'нужно', 'надо', 'давай', 'ладно', 'хорошо', 'привет', 'алло', 'слушай', 'смотри', 'сейчас', 'сегодня', 'потом', 'сначала', 'просто', 'думаю',
          'файл', 'код', 'тест', 'один', 'два', 'три', 'пока', 'ок', 'окей'}
SUGGEST = ['клод', 'джарвис', 'компьютер', 'ассистент', 'помощник', 'алиса', 'бродяга', 'краб', 'пятница', 'квазар']
KNOWN_VARIANTS = {'клод': ['клауд', 'клот', 'клоуд'], 'claude': ['клод', 'клауд']}
_LAT = [('sch', 'ск'), ('ch', 'ч'), ('sh', 'ш'), ('th', 'т'), ('ph', 'ф'), ('ck', 'к'), ('au', 'о'), ('ou', 'у'), ('ee', 'и'), ('oo', 'у'),
        ('a', 'а'), ('b', 'б'), ('c', 'к'), ('d', 'д'), ('e', 'е'), ('f', 'ф'), ('g', 'г'), ('h', 'х'), ('i', 'и'), ('j', 'дж'), ('k', 'к'), ('l', 'л'), ('m', 'м'),
        ('n', 'н'), ('o', 'о'), ('p', 'п'), ('q', 'к'), ('r', 'р'), ('s', 'с'), ('t', 'т'), ('u', 'у'), ('v', 'в'), ('w', 'в'), ('x', 'кс'), ('y', 'и'), ('z', 'з')]
# single-letter confusions Vosk often makes (voiced/unvoiced, vowel reduction)
_SWAP = [('д', 'т'), ('т', 'д'), ('г', 'к'), ('к', 'г'), ('б', 'п'), ('п', 'б'), ('з', 'с'), ('с', 'з'), ('в', 'ф'), ('ф', 'в'), ('о', 'а'), ('а', 'о'),
         ('ы', 'и'), ('и', 'ы'), ('о', 'оу'), ('о', 'ау'), ('у', 'оу'), ('е', 'э'), ('э', 'е')]


def normalize(word):
    return ' '.join(re.sub(r'[^\w\s-]', ' ', str(word or '').lower()).split())


def translit(word):
    """Latin spelling -> Russian letters (claude -> клод via the known table, otherwise letter by letter)."""
    w = normalize(word)
    if not re.search('[a-z]', w): return w
    if w in KNOWN_VARIANTS: return KNOWN_VARIANTS[w][0]
    out, i = '', 0
    while i < len(w):
        for lat, ru in _LAT:
            if w.startswith(lat, i): out += ru; i += len(lat); break
        else: out += w[i]; i += 1
    return out


def clean_word(word):
    """What goes into the grammar: one lower-case Russian word (the widget matches single tokens)."""
    w = translit(word)
    w = w.split(' ')[0] if w else ''
    return re.sub(r'[^а-яё-]', '', w)


def phonetic_variants(word, find=None, limit=8):
    """Spellings Vosk may produce for `word`. With `find` only in-vocabulary ones are kept."""
    base = clean_word(word); out = []
    for v in KNOWN_VARIANTS.get(base, []): out.append(v)
    for a, b in _SWAP:
        for m in re.finditer(a, base):
            v = base[:m.start()] + b + base[m.end():]
            if v != base and len(v) >= 3 and v not in out: out.append(v)
    if find: out = [v for v in out if find(v) >= 0]
    return out[:limit]


def words(cfg):
    """All tokens that count as the wake word, in match order: main word first."""
    main = clean_word(cfg.get('wake_word') or 'клод') or 'клод'
    out = [main]
    for v in list(cfg.get('wake_extra_variants') or []) + KNOWN_VARIANTS.get(main, []):
        v = clean_word(v)
        if v and v not in out: out.append(v)
    return out


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1): cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def conflicts(word, find=None, decoys=DECOYS):
    """[(level, text)]: level 'error' = unusable, 'warn' = will work badly."""
    out, w = [], clean_word(word)
    if not w: return [('error', 'Введите слово русскими буквами.')]
    if len(w) < 4: out.append(('warn', f'Слово «{w}» короткое: короткие слова срабатывают случайно, лучше 5+ букв.'))
    if w in COMMON: out.append(('error', f'«{w}» обычное слово из речи: сработает на любой фразе с ним.'))
    for d in decoys:
        if d == w:
            if w not in COMMON: out.append(('error', f'«{w}» совпадает со словом-ловушкой, его специально отфильтровывают.'))
            break
    else:
        near = [d for d in decoys if len(d) >= 3 and _lev(w, d) <= 1]
        if near: out.append(('info', 'Похожие слова (' + ', '.join(near) + ') добавлены в грамматику как ловушки, чтобы не срабатывать на них.'))
    if find is not None and find(w) < 0:
        alts = [a for a in SUGGEST if a != w and find(a) >= 0][:4] + phonetic_variants(w, find)[:3]
        out.append(('error', f'«{w}» нет в словаре модели Vosk (small-ru), оно не распознается. '
                             + ('Попробуйте: ' + ', '.join(dict.fromkeys(alts)) + '.' if alts else '')))
    return out


def has_errors(items): return any(l == 'error' for l, _ in items)


def build_grammar(cfg, find=None):
    """-> (grammar list, wake tokens that made it, missing tokens). Decoys equal to a wake token are dropped."""
    wake = words(cfg); missing = []
    if find is not None:
        keep = [w for w in wake if find(w) >= 0]; missing = [w for w in wake if w not in keep]; wake = keep
    decoys = [d for d in DECOYS if d not in wake and (find is None or find(d) >= 0)]
    return wake + decoys + ['[unk]'], wake, missing


def matches(text, wake):
    toks = set(normalize(text).split())
    return any(w in toks for w in wake)


_model = None


def get_model():
    global _model
    if _model is None:
        import vosk
        vosk.SetLogLevel(-1); _model = vosk.Model(str(VOSK_DIR))
    return _model


def model_find(word): return get_model().vosk_model_find_word(word)


class Detector:
    """KaldiRecognizer with the wake grammar. feed(int16 bytes) -> (text, hit, is_final). fast: a partial result is enough; strict: final + confidence."""
    def __init__(self, cfg, model=None, log=None):
        import vosk
        self.model = model or get_model(); self.mode = cfg.get('wake_mode', 'fast'); self.min_conf = float(cfg.get('wake_min_conf', 0.5))
        grammar, self.wake, self.missing = build_grammar(cfg, self.model.vosk_model_find_word)
        if log:
            if self.missing: log('[wake] not in Vosk vocabulary, ignored:', self.missing)
            log('[wake] grammar: wake=%s decoys=%d mode=%s min_conf=%.2f' % (self.wake, len(grammar) - len(self.wake) - 1, self.mode, self.min_conf))
        self.rec = vosk.KaldiRecognizer(self.model, SR, json.dumps(grammar, ensure_ascii=False)); self.rec.SetWords(True)
        self.last_part = ''; self.last_conf = 0.0

    def reset(self): self.rec.Reset(); self.last_part = ''

    def feed(self, data):
        if self.rec.AcceptWaveform(data):
            r = json.loads(self.rec.Result()); text = r.get('text', ''); self.last_part = ''
            confs = [x.get('conf', 1.0) for x in r.get('result', []) if x.get('word') in self.wake]
            self.last_conf = max(confs) if confs else 0.0
            return text, matches(text, self.wake) and (self.mode == 'fast' or self.last_conf >= self.min_conf), True
        text = json.loads(self.rec.PartialResult()).get('partial', '')
        self.last_part = text
        return text, (self.mode == 'fast' and matches(text, self.wake)), False

    def scan(self, audio):
        """Offline check of recorded int16 audio: -> dict(text, hit, conf, passes)."""
        self.reset(); best, texts = 0.0, []
        raw = audio.tobytes()
        def take(r):
            nonlocal best
            texts.append(r.get('text', ''))
            for x in r.get('result', []):
                if x.get('word') in self.wake: best = max(best, x.get('conf', 0.0))
        for i in range(0, len(raw), 3200):
            if self.rec.AcceptWaveform(raw[i:i + 3200]): take(json.loads(self.rec.Result()))
        take(json.loads(self.rec.FinalResult()))
        text = ' '.join(t for t in texts if t); hit = matches(text, self.wake)
        return {'text': text, 'hit': hit, 'conf': best, 'passes': hit and (self.mode == 'fast' or best >= self.min_conf)}
