"""Characters and emotions: built-in procedural pixel themes (crab, cat, robot), user assets (GIF / PNG frames / sprite sheet),
import / replace / reset, fallback to the built-in default. No Tk here (PIL only), so it is testable headless.

Folders (see characters/README.md):
  %APPDATA%\\voice-widget\\characters\\<name>\\character.json      user characters (override with env VOICE_WIDGET_DATA)
  <repo>\\voice-widget\\characters\\<name>\\...                     bundled characters, read only
  <char>\\emotions\\<emotion>\\anim.gif | sheet.png | frames\\*.png  + emotion.json {fps, loop, duration, frame_w, frame_h}
"""
import json, os, re, shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageSequence

HERE = Path(__file__).resolve().parent
BUNDLED_ROOT = HERE / 'characters'
EMOTIONS = ['idle', 'listening', 'transcribing', 'thinking', 'working', 'success', 'error', 'question', 'confirm', 'sleep']
EXTRA_EMOTIONS = ['speaking']            # not in the user spec; the old crab had it, so it stays an (optional) emotion
ALL_EMOTIONS = EMOTIONS + EXTRA_EMOTIONS
EMOTION_RU = {'idle': 'Покой', 'listening': 'Слушает', 'transcribing': 'Распознает', 'thinking': 'Думает', 'working': 'Работает',
              'success': 'Успех', 'error': 'Ошибка', 'question': 'Вопрос', 'confirm': 'Подтверждение', 'sleep': 'Сон', 'speaking': 'Говорит'}
BUILTIN = ['crab', 'cat', 'robot', 'girl_sakura', 'girl_mio', 'girl_yuki', 'cat_black', 'cat_white', 'cat_gray']
BUILTIN_RU = {'crab': 'Краб', 'cat': 'Кот', 'robot': 'Робот', 'girl_sakura': 'Сакура', 'girl_mio': 'Мио', 'girl_yuki': 'Юки',
              'cat_black': 'Чёрный кот', 'cat_white': 'Белый кот', 'cat_gray': 'Серый кот'}
GW, GH = 18, 17                           # sprite grid (cells) of the built-in themes; the window box has the same ratio
DEFAULT_FPS = 9.0
DEFAULT_DURATION = {'success': 3.0, 'error': 4.0}   # seconds, then back to idle; 0 = stays until the next state
IMG_EXT = {'.png', '.webp', '.bmp', '.jpg', '.jpeg', '.gif'}

# assistant / dialog state -> emotion. The dialog agent emits working/awaiting_clarification/awaiting_confirmation/completed/failed/idle,
# widget.py legacy states (thinking = models loading, speaking...) are kept. Overridable via cfg['emotion_map'].
STATE_TO_EMOTION = {
    'idle': 'idle', 'listening': 'listening', 'transcribing': 'transcribing', 'thinking': 'thinking', 'speaking': 'speaking',
    'working': 'working', 'completed': 'success', 'success': 'success', 'failed': 'error', 'error': 'error',
    'awaiting_clarification': 'question', 'question': 'question', 'awaiting_confirmation': 'confirm', 'confirm': 'confirm', 'sleep': 'sleep',
}


def resolve_emotion(state, overrides=None):
    """State or emotion name -> emotion name (unknown -> idle)."""
    m = dict(STATE_TO_EMOTION); m.update({k: v for k, v in (overrides or {}).items() if v in ALL_EMOTIONS})
    return m.get(state, 'idle')


def user_root():
    base = os.environ.get('VOICE_WIDGET_DATA') or os.path.join(os.environ.get('APPDATA') or str(Path.home()), 'voice-widget')
    return Path(base) / 'characters'


class AssetError(Exception): pass


# ---------------------------------------------------------------- procedural pixel art
BODY, SHADE, DARK, WHITE = (217, 119, 87), (181, 88, 58), (26, 26, 26), (255, 255, 255)
GREEN, RED, YELLOW, BLUE = (74, 222, 128), (229, 72, 77), (250, 204, 21), (96, 165, 250)
GREY = (150, 150, 150)
CAT, CAT_D, CAT_L, PINK = (232, 163, 92), (190, 112, 52), (250, 228, 196), (240, 128, 150)
ROB, ROB_D, LED = (168, 184, 204), (104, 122, 146), (80, 220, 255)
GLYPH = {'?': ['XXX', '..X', '.XX', '...', '.X.'], 'z': ['XXX', '.X.', 'XXX']}


def pose(emotion, f):
    """Per-frame body pose shared by all built-in characters."""
    p = dict(dy=0, dx=0, arms_up=False, mouth=0, eyes='open', legs_alt=False)
    if emotion == 'idle': p.update(dy=(f // 6) % 2, eyes='blink' if f % 24 in (0, 1) else 'open')
    elif emotion == 'listening': p.update(arms_up=True, dy=(f // 3) % 2)
    elif emotion == 'transcribing': p.update(eyes='side' if (f // 4) % 2 else 'open', dy=(f // 4) % 2)
    elif emotion == 'thinking': p.update(eyes='blink' if f % 16 in (0, 1) else 'up')
    elif emotion == 'speaking': p.update(dy=1 if f % 4 in (0, 1) else 0, mouth=1 + f % 2, arms_up=(f // 4) % 2 == 0)
    elif emotion == 'working': p.update(legs_alt=f % 2 == 0, dy=(f // 2) % 2)
    elif emotion == 'success': p.update(arms_up=True, dy=(f // 2) % 2, eyes='happy')
    elif emotion == 'error': p.update(dx=1 if f % 2 else -1, eyes='x')
    elif emotion == 'question': p.update(dy=(f // 8) % 2, eyes='up' if (f // 12) % 2 else 'open')
    elif emotion == 'confirm': p.update(arms_up=(f // 6) % 2 == 0, dy=(f // 3) % 2)
    elif emotion == 'sleep': p.update(eyes='closed', dy=(f // 12) % 2)
    return p


class Shifted:
    """Pen proxy that moves everything by dx (error shake)."""
    def __init__(self, pen, dx): self.pen, self.dx = pen, dx
    def R(self, x0, y0, x1, y1, c): self.pen.R(x0 + self.dx, y0, x1 + self.dx, y1, c)
    def P(self, x, y, c): self.pen.P(x + self.dx, y, c)


class Pen:
    def __init__(self, im): self.d = ImageDraw.Draw(im)
    def R(self, x0, y0, x1, y1, c): self.d.rectangle((x0, y0, x1, y1), fill=c + (255,))
    def P(self, x, y, c): self.d.point((x, y), fill=c + (255,))
    def glyph(self, g, x, y, c):
        for j, row in enumerate(GLYPH[g]):
            for i, ch in enumerate(row):
                if ch == 'X': self.P(x + i, y + j, c)


def eyes(pen, kind, lx, rx, top, h, col):
    for x in (lx, rx):
        if kind == 'open': pen.R(x, top, x + 1, top + h - 1, col)
        elif kind == 'side': pen.R(x + 1, top, x + 2, top + h - 1, col)
        elif kind == 'up': pen.R(x, top - 1, x + 1, top + h - 2, col)
        elif kind in ('blink', 'closed'): pen.R(x, top + h - 1, x + 1, top + h - 1, col)
        elif kind == 'happy': pen.P(x, top + 1, col); pen.P(x + 1, top, col); pen.P(x + 2, top + 1, col)
        elif kind == 'x':
            y0 = top + h - 3
            for i in range(3): pen.P(x + i, y0 + i, col); pen.P(x + 2 - i, y0 + i, col)


def overlay(pen, emotion, f):
    if emotion == 'listening':
        n = (f // 2) % 3; pen.R(8 - n, 0, 9 + n, 1, GREEN)
    elif emotion == 'transcribing':
        for i, x in enumerate((6, 8, 10, 12)):
            h = 1 + (f // 2 + i * 2) % 4; pen.R(x, 3 - h + 1, x + 1, 3, BLUE)
    elif emotion == 'thinking':
        pen.R(5, 0, 12, 3, WHITE); pen.R(4, 1, 13, 2, WHITE); k = (f // 3) % 4
        for i in range(3):
            if k > i: pen.R(6 + i * 3, 1, 7 + i * 3, 2, DARK)
            else: pen.P(6 + i * 3, 2, GREY)
    elif emotion == 'working':
        pen.R(15, 0, 17, 2, WHITE); ring = [(15, 0), (16, 0), (17, 0), (17, 1), (17, 2), (16, 2), (15, 2), (15, 1)]
        pen.P(*ring[f % 8], DARK); pen.P(*ring[(f + 1) % 8], (120, 120, 120))
    elif emotion == 'success':
        for x, y in [(6, 2), (7, 3), (8, 2), (9, 1), (10, 0)]: pen.P(x, y, GREEN); pen.P(x + 1, y, GREEN)
    elif emotion == 'error': pen.R(16, 0, 17, 3, RED); pen.R(16, 5, 17, 5, RED)
    elif emotion == 'question': pen.glyph('?', 8, 0, YELLOW)
    elif emotion == 'confirm':
        for x, y in [(3, 2), (4, 3), (5, 2), (6, 1), (7, 0)]: pen.P(x, y, GREEN)
        for i in range(3): pen.P(11 + i, i, RED); pen.P(13 - i, i, RED)
    elif emotion == 'sleep':
        k = (f // 8) % 3
        for i in range(k + 1): pen.glyph('z', 11 + i * 2, 3 - i, (200, 210, 235))


def draw_crab(pen, p, emotion, f):
    oy = 2 + p['dy']; s = Shifted(pen, p['dx']); R, P = s.R, s.P
    R(3, oy + 4, 14, oy + 10, BODY); R(3, oy + 10, 14, oy + 10, SHADE)
    for x0 in (4, 7, 9, 12):
        up = p['legs_alt'] and x0 in (4, 9)
        R(x0, oy + 11, x0 + 1, oy + (11 if up else 12), SHADE if up else BODY)
    for x0 in (1, 15):
        if p['arms_up']: R(x0, oy + 1, x0 + 1, oy + 5, BODY); P(x0 if x0 == 1 else x0 + 1, oy, BODY)
        else: R(x0, oy + 6, x0 + 1, oy + 8, BODY); R(x0, oy + 8, x0 + 1, oy + 8, SHADE)
    eyes(s, p['eyes'], 5, 11, oy + 6, 3, DARK)
    if p['mouth'] == 1: R(7, oy + 9, 10, oy + 9, DARK)
    elif p['mouth'] == 2: R(8, oy + 8, 9, oy + 9, DARK)


def draw_cat(pen, p, emotion, f, pal=None):
    CAT, CAT_D, CAT_L, EYE, WHISK = pal or (globals()['CAT'], globals()['CAT_D'], globals()['CAT_L'], DARK, WHITE)
    oy = 2 + p['dy']; s = Shifted(pen, p['dx']); R, P = s.R, s.P
    R(5, oy + 11, 12, oy + 12, CAT); R(5, oy + 12, 12, oy + 12, CAT_D)               # body
    R(4, oy + 5, 13, oy + 10, CAT); R(4, oy + 10, 13, oy + 10, CAT_D)                 # head
    R(4, oy + 3, 4, oy + 4, CAT); R(5, oy + 4, 5, oy + 4, CAT); R(13, oy + 3, 13, oy + 4, CAT); R(12, oy + 4, 12, oy + 4, CAT)  # ears
    P(4, oy + 4, PINK); P(13, oy + 4, PINK)
    for x in (7, 10): P(x, oy + 5, CAT_D)                                              # forehead stripes
    P(8, oy + 5, CAT_D); P(9, oy + 5, CAT_D)
    R(7, oy + 9, 10, oy + 10, CAT_L)                                                   # muzzle
    R(8, oy + 8, 9, oy + 8, PINK)
    up = p['legs_alt']
    R(5, oy + 13, 6, oy + 13, CAT_D if up else CAT); R(11, oy + 13, 12, oy + 13, CAT)
    if p['arms_up']: R(2, oy + 6, 3, oy + 9, CAT); R(14, oy + 6, 15, oy + 9, CAT)     # paws up
    else:
        for yy in (8, 10): R(2, oy + yy, 3, oy + yy, WHISK); R(14, oy + yy, 15, oy + yy, WHISK)  # whiskers
    t = 1 if (f // 3) % 2 and emotion != 'sleep' else 0                                # tail
    R(13, oy + 12 - t, 15, oy + 12 - t, CAT); R(15, oy + 10 - t, 16, oy + 11 - t, CAT); P(16, oy + 10 - t, CAT_D)
    eyes(s, p['eyes'], 5, 11, oy + 6, 2, EYE)
    if p['mouth'] == 1: R(8, oy + 10, 9, oy + 10, DARK)
    elif p['mouth'] == 2: R(8, oy + 9, 9, oy + 10, DARK)
    else: P(7, oy + 10, DARK); P(10, oy + 10, DARK)


def draw_robot(pen, p, emotion, f):
    oy = 2 + p['dy']; s = Shifted(pen, p['dx']); R, P = s.R, s.P
    blink = (f // 4) % 2 == 0
    R(8, oy + 3, 9, oy + 3, ROB_D); R(8, oy + 2, 9, oy + 2, RED if blink or emotion == 'error' else YELLOW)   # antenna
    R(4, oy + 4, 13, oy + 9, ROB); R(4, oy + 9, 13, oy + 9, ROB_D); R(4, oy + 4, 13, oy + 4, (200, 212, 228))  # head
    R(3, oy + 6, 3, oy + 7, ROB_D); R(14, oy + 6, 14, oy + 7, ROB_D)                                         # bolts
    R(5, oy + 10, 12, oy + 12, ROB); R(5, oy + 12, 12, oy + 12, ROB_D)                                        # torso
    R(8, oy + 11, 9, oy + 11, LED if (f // 3) % 2 else ROB_D)                                                 # chest light
    if p['arms_up']: R(1, oy + 5, 2, oy + 10, ROB_D)
    else: R(2, oy + 10, 3, oy + 12, ROB_D); R(14, oy + 10, 15, oy + 12, ROB_D)
    if p['arms_up']: R(15, oy + 5, 16, oy + 10, ROB_D)
    R(5, oy + 13, 7, oy + 13, ROB_D if not p['legs_alt'] else ROB); R(10, oy + 13, 12, oy + 13, ROB_D)
    led = LED if p['eyes'] not in ('x',) else RED
    ek = p['eyes']
    if ek == 'x':
        eyes(s, 'x', 5, 11, oy + 6, 2, RED)
    else:
        eyes(s, ek, 5, 11, oy + 6, 2, led if ek != 'closed' else ROB_D)
    mouth_y = oy + 8
    if p['mouth']: R(6, mouth_y, 11, mouth_y, DARK); R(7 if p['mouth'] == 1 else 8, mouth_y - 1, 10 if p['mouth'] == 1 else 9, mouth_y - 1, DARK)
    else:
        for x in (6, 8, 10): P(x, mouth_y, DARK)


SKIN, SKIN_D, BLUSH = (255, 224, 200), (232, 190, 165), (255, 150, 160)


def draw_girl(pen, p, emotion, f, look):
    """Front-facing chibi girl on the same 18x17 grid. look: hair, hair_d, eye, dress, dress_d, style (long|twin|bob), acc (colour of the ribbon / clip)."""
    HAIR, HAIR_D, EYE, DRESS, DRESS_D, style, ACC = look
    oy = 1 + p['dy']; s = Shifted(pen, p['dx']); R, P = s.R, s.P
    R(3, oy + 2, 14, oy + 8, HAIR_D)                                                   # hair behind the head
    if style == 'twin':
        R(1, oy + 4, 2, oy + 11, HAIR); R(15, oy + 4, 16, oy + 11, HAIR); R(1, oy + 11, 2, oy + 11, HAIR_D); R(15, oy + 11, 16, oy + 11, HAIR_D)
    elif style == 'long': R(3, oy + 5, 4, oy + 11, HAIR); R(13, oy + 5, 14, oy + 11, HAIR)
    else: R(3, oy + 5, 4, oy + 8, HAIR); R(13, oy + 5, 14, oy + 8, HAIR)
    R(5, oy + 3, 12, oy + 9, SKIN); R(5, oy + 9, 12, oy + 9, SKIN_D)                   # face
    R(4, oy + 1, 13, oy + 3, HAIR); R(3, oy + 2, 3, oy + 4, HAIR); R(14, oy + 2, 14, oy + 4, HAIR)   # fringe
    P(5, oy + 4, HAIR); P(6, oy + 4, HAIR); P(8, oy + 4, HAIR); P(9, oy + 4, HAIR); P(11, oy + 4, HAIR); P(12, oy + 4, HAIR)
    R(11, oy + 1, 13, oy + 2, ACC); P(10, oy + 2, ACC); P(14, oy + 3, ACC)             # ribbon / clip on the right side
    ek = p['eyes']
    eyes(s, ek, 6, 10, oy + 5, 3, EYE)
    if ek in ('open', 'side', 'up'): P(6, oy + 5, (255, 255, 255)); P(10, oy + 5, (255, 255, 255))   # eye shine
    P(5, oy + 8, BLUSH); P(12, oy + 8, BLUSH)
    if p['mouth'] == 1: R(8, oy + 8, 9, oy + 8, (190, 70, 80))
    elif p['mouth'] == 2: R(8, oy + 8, 9, oy + 9, (190, 70, 80))
    elif ek in ('happy',): P(7, oy + 8, (190, 70, 80)); R(8, oy + 9, 9, oy + 9, (190, 70, 80)); P(10, oy + 8, (190, 70, 80))
    else: P(8, oy + 8, (190, 70, 80)); P(9, oy + 8, (190, 70, 80))
    R(6, oy + 10, 11, oy + 12, DRESS); R(6, oy + 12, 11, oy + 12, DRESS_D); P(8, oy + 10, (255, 255, 255)); P(9, oy + 10, (255, 255, 255))   # dress + collar
    if p['arms_up']:
        R(3, oy + 7, 4, oy + 11, DRESS); R(13, oy + 7, 14, oy + 11, DRESS); R(3, oy + 6, 4, oy + 6, SKIN); R(13, oy + 6, 14, oy + 6, SKIN)
    else:
        R(4, oy + 10, 5, oy + 12, DRESS); R(12, oy + 10, 13, oy + 12, DRESS); P(4, oy + 13, SKIN); P(13, oy + 13, SKIN)
    up = p['legs_alt']
    if not up: R(6, oy + 13, 7, oy + 13, SKIN); R(6, oy + 14, 7, oy + 14, DRESS_D)    # one leg lifts while 'working'
    R(10, oy + 13, 11, oy + 13, SKIN); R(10, oy + 14, 11, oy + 14, DRESS_D)            # shoes are the last row


GIRLS = {   # id: (title, look)
    'girl_sakura': ('Сакура', ((255, 150, 190), (214, 104, 150), (120, 70, 200), (255, 255, 255), (230, 190, 215), 'long', (230, 50, 70))),
    'girl_mio': ('Мио', ((70, 150, 240), (40, 100, 190), (30, 160, 140), (250, 250, 255), (160, 190, 235), 'twin', (255, 215, 70))),
    'girl_yuki': ('Юки', ((235, 235, 250), (170, 175, 205), (210, 60, 90), (90, 100, 170), (60, 70, 130), 'bob', (170, 120, 255))),
}
CATS = {    # id: (title, (body, shade, light, eyes, whiskers))
    'cat_black': ('Чёрный кот', ((62, 62, 76), (34, 34, 46), (118, 118, 136), (250, 214, 60), (170, 170, 190))),
    'cat_white': ('Белый кот', ((244, 244, 250), (196, 200, 220), (255, 255, 255), (60, 120, 200), (170, 175, 195))),
    'cat_gray': ('Серый кот', ((156, 168, 186), (108, 120, 142), (226, 232, 242), (40, 40, 52), (255, 255, 255))),
}
DRAWERS = {'crab': draw_crab, 'cat': draw_cat, 'robot': draw_robot}
for _id, (_t, _look) in GIRLS.items(): DRAWERS[_id] = (lambda look: lambda pen, p, e, f: draw_girl(pen, p, e, f, look))(_look)
for _id, (_t, _pal) in CATS.items(): DRAWERS[_id] = (lambda pal: lambda pen, p, e, f: draw_cat(pen, p, e, f, pal))(_pal)
PROC_FRAMES = 48
_proc_cache = {}


def procedural_frames(char, emotion):
    """48 RGBA frames (GW x GH) of a built-in character."""
    key = (char, emotion)
    if key not in _proc_cache:
        out = []
        for f in range(PROC_FRAMES):
            im = Image.new('RGBA', (GW, GH), (0, 0, 0, 0)); pen = Pen(im)
            DRAWERS[char](pen, pose(emotion, f), emotion, f); overlay(pen, emotion, f); out.append(im)
        _proc_cache[key] = out
    return _proc_cache[key]


# ---------------------------------------------------------------- animations
class Anim:
    def __init__(self, frames, fps=DEFAULT_FPS, loop=True, duration=0.0, source='builtin', grid=False):
        self.frames, self.fps, self.loop, self.duration, self.source, self.grid = frames, float(fps), bool(loop), float(duration), source, grid

    def index(self, t):
        """Frame index at t seconds from the start (clamped on the last frame when not looping)."""
        n = len(self.frames); i = int(t * max(self.fps, 0.1))
        return i % n if self.loop else min(i, n - 1)


def natural_key(p): return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(p))]


def load_gif(path):
    """-> (frames RGBA, fps from the frame delays, loop flag). Raises AssetError."""
    try:
        with Image.open(path) as im:
            frames, delays = [], []
            for fr in ImageSequence.Iterator(im):
                frames.append(fr.convert('RGBA')); delays.append(fr.info.get('duration', 100) or 100)
            loop = im.info.get('loop', 0) != 1
    except Exception as e: raise AssetError(f'GIF не читается: {e}')
    if not frames: raise AssetError('в GIF нет кадров')
    return frames, 1000.0 / max(20.0, sum(delays) / len(delays)), loop


def slice_sheet(im, fw, fh):
    """Row-major cells fw x fh; trailing fully transparent cells are dropped."""
    im = im.convert('RGBA')
    if fw < 1 or fh < 1 or fw > im.width or fh > im.height: raise AssetError(f'размер кадра {fw}x{fh} не подходит к листу {im.width}x{im.height}')
    out = [im.crop((x, y, x + fw, y + fh)) for y in range(0, im.height - fh + 1, fh) for x in range(0, im.width - fw + 1, fw)]
    while len(out) > 1 and out[-1].getchannel('A').getbbox() is None: out.pop()
    return out


def guess_cell(im):
    """Square cells by the sheet height (a horizontal strip), else by width (a vertical strip)."""
    w, h = im.size
    if w >= h and w % h == 0: return h, h
    if h > w and h % w == 0: return w, w
    return h, h


def load_frame_files(files):
    out = []
    for f in sorted(files, key=natural_key):
        try:
            with Image.open(f) as im: out.append(im.convert('RGBA'))
        except Exception as e: raise AssetError(f'{Path(f).name} не читается: {e}')
    if not out: raise AssetError('нет кадров')
    return out


def read_json(path, default):
    try: return json.loads(Path(path).read_text('utf8'))
    except Exception: return default


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + '.tmp'; Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf8'); os.replace(tmp, path)


# ---------------------------------------------------------------- character folders
def find_dir(char):
    """Folder of a user / bundled character or None (built-in procedural themes have no folder)."""
    if not char or char in BUILTIN and not (user_root() / char).exists(): return None
    for root in (user_root(), BUNDLED_ROOT):
        if (root / char / 'character.json').exists() or (root / char / 'emotions').exists(): return root / char
    return None


def char_meta(char):
    d = find_dir(char)
    return read_json(d / 'character.json', {}) if d else {}


def base_of(char):
    """Built-in theme that fills the gaps of a character (crab unless the character says otherwise)."""
    if char in BUILTIN: return char
    b = char_meta(char).get('base', 'crab')
    return b if b in BUILTIN else 'crab'


def list_characters():
    out = [{'id': c, 'name': BUILTIN_RU[c], 'builtin': True, 'engine': 'gif'} for c in BUILTIN]
    seen = set(BUILTIN)
    for root in (user_root(), BUNDLED_ROOT):
        if not root.exists(): continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in seen and ((d / 'character.json').exists() or (d / 'emotions').exists()):
                meta = read_json(d / 'character.json', {}); seen.add(d.name)
                out.append({'id': d.name, 'name': meta.get('name', d.name), 'builtin': False, 'engine': meta.get('engine') if meta.get('engine') in ('live2d', 'vrm') else 'gif'})
    return out


def safe_name(name):
    n = re.sub(r'[^\w\- ]', '', str(name), flags=re.U).strip().replace(' ', '-')
    if not n: raise AssetError('пустое имя персонажа')
    return n[:40]


def create_character(name, base='crab', fallback='builtin', title=None):
    """Create <user_root>/<name>/character.json (fallback: 'builtin' = missing emotions from `base`, 'idle' = from this character's idle)."""
    cid = safe_name(name); d = user_root() / cid
    if cid in BUILTIN: cid = cid + '-custom'; d = user_root() / cid
    d.mkdir(parents=True, exist_ok=True)
    if not (d / 'character.json').exists(): write_json(d / 'character.json', {'name': title or name, 'base': base, 'fallback': fallback})
    return cid


def editable_copy(char):
    """Built-in themes are read only: return a user character that can be edited (created on first use)."""
    if find_dir(char) and char not in BUILTIN and str(find_dir(char)).startswith(str(user_root())): return char
    return create_character(char + '-custom', base=base_of(char), title=BUILTIN_RU.get(char, char) + ' (свой)')


def emotion_dir(char, emotion):
    d = find_dir(char)
    return d / 'emotions' / emotion if d else None


def emotion_source(char, emotion):
    """'gif' | 'sheet' | 'frames' | 'builtin' (no user asset)."""
    d = emotion_dir(char, emotion)
    if d and d.exists():
        if (d / 'anim.gif').exists(): return 'gif'
        if (d / 'sheet.png').exists(): return 'sheet'
        if (d / 'frames').is_dir() and any(p.suffix.lower() in IMG_EXT for p in (d / 'frames').iterdir()): return 'frames'
    return 'builtin'


def emotion_meta(char, emotion):
    m = {'fps': DEFAULT_FPS, 'loop': True, 'duration': DEFAULT_DURATION.get(emotion, 0.0)}
    d = emotion_dir(char, emotion)
    if d: m.update({k: v for k, v in read_json(d / 'emotion.json', {}).items() if k in ('fps', 'loop', 'duration', 'frame_w', 'frame_h')})
    return m


def set_emotion_meta(char, emotion, **kw):
    d = emotion_dir(char, emotion)
    if d is None: raise AssetError('персонаж встроенный: сначала создайте копию')
    d.mkdir(parents=True, exist_ok=True)
    m = read_json(d / 'emotion.json', {}); m.update({k: v for k, v in kw.items() if v is not None}); write_json(d / 'emotion.json', m)


def load_user_anim(char, emotion):
    """Anim from the emotion folder. Raises AssetError when the files are corrupt."""
    d = emotion_dir(char, emotion); src = emotion_source(char, emotion); meta = emotion_meta(char, emotion)
    if src == 'gif':
        frames, fps, loop = load_gif(d / 'anim.gif')
        if not (d / 'emotion.json').exists() or 'fps' not in read_json(d / 'emotion.json', {}): meta['fps'] = fps
        if 'loop' not in read_json(d / 'emotion.json', {}): meta['loop'] = loop
    elif src == 'sheet':
        try:
            with Image.open(d / 'sheet.png') as im: sheet = im.convert('RGBA')
        except Exception as e: raise AssetError(f'sheet.png не читается: {e}')
        fw, fh = meta.get('frame_w'), meta.get('frame_h')
        if not fw or not fh: fw, fh = guess_cell(sheet)
        frames = slice_sheet(sheet, int(fw), int(fh))
    else:
        frames = load_frame_files([p for p in (d / 'frames').iterdir() if p.suffix.lower() in IMG_EXT])
    return Anim(frames, meta['fps'], meta['loop'], meta['duration'], src, grid=False)


def builtin_anim(char, emotion):
    meta = emotion_meta(char, emotion) if find_dir(char) else {'fps': DEFAULT_FPS, 'loop': True, 'duration': DEFAULT_DURATION.get(emotion, 0.0)}
    return Anim(procedural_frames(base_of(char), emotion), meta['fps'], meta['loop'], meta['duration'], 'builtin', grid=True)


def load_animation(char, emotion, log=None):
    """The animation for (character, emotion). Missing or corrupt user files fall back to the built-in default (warning in the log)."""
    log = log or (lambda *a: None)
    if emotion not in ALL_EMOTIONS: emotion = 'idle'
    if char not in BUILTIN and find_dir(char):
        src = emotion_source(char, emotion)
        if src != 'builtin':
            try: return load_user_anim(char, emotion)
            except AssetError as e: log(f'[character] {char}/{emotion}: {e}; fallback to built-in')
        elif emotion != 'idle' and char_meta(char).get('fallback') == 'idle' and emotion_source(char, 'idle') != 'builtin':
            try: return load_user_anim(char, 'idle')
            except AssetError as e: log(f'[character] {char}/idle: {e}; fallback to built-in')
    elif char not in BUILTIN and not find_dir(char): log(f'[character] {char!r} not found; using crab'); char = 'crab'
    return builtin_anim(char, emotion)


# ---------------------------------------------------------------- import / reset
def _clear_media(d):
    for n in ('anim.gif', 'sheet.png'):
        try: (d / n).unlink()
        except FileNotFoundError: pass
    shutil.rmtree(d / 'frames', ignore_errors=True)


def import_emotion(char, emotion, src, kind=None, frame_w=None, frame_h=None, fps=None, loop=None, duration=None):
    """Import a GIF / folder of frames (or list of files) / sprite sheet as the animation of `emotion`. The previous asset is replaced only after
    the new one parsed successfully (corrupt input raises AssetError and changes nothing). Returns the stored kind."""
    if emotion not in ALL_EMOTIONS: raise AssetError(f'неизвестная эмоция {emotion}')
    root = find_dir(char)
    if root is None or not str(root).startswith(str(user_root())): raise AssetError('персонаж встроенный: создайте копию')
    files = None
    if isinstance(src, (list, tuple)): files = [Path(p) for p in src]; srcp = files[0]
    else: srcp = Path(src)
    if kind is None:
        if files and len(files) > 1: kind = 'frames'
        elif srcp.is_dir(): kind = 'frames'
        elif srcp.suffix.lower() == '.gif': kind = 'gif'
        else: kind = 'sheet' if (frame_w and frame_h) else 'frames'
    d = root / 'emotions' / emotion; meta = {}
    if kind == 'gif':
        frames, gfps, gloop = load_gif(srcp)                       # validate before touching anything
        payload = ('gif', srcp)
    elif kind == 'sheet':
        try:
            with Image.open(srcp) as im: sheet = im.convert('RGBA')
        except Exception as e: raise AssetError(f'лист не читается: {e}')
        fw, fh = (int(frame_w), int(frame_h)) if frame_w and frame_h else guess_cell(sheet)
        frames = slice_sheet(sheet, fw, fh); meta.update(frame_w=fw, frame_h=fh); payload = ('sheet', sheet)
    elif kind == 'frames':
        lst = files or ([p for p in srcp.iterdir() if p.suffix.lower() in IMG_EXT] if srcp.is_dir() else [srcp])
        frames = load_frame_files(lst); payload = ('frames', sorted(lst, key=natural_key))
    else: raise AssetError(f'неизвестный тип {kind}')
    d.mkdir(parents=True, exist_ok=True); old = read_json(d / 'emotion.json', {})
    _clear_media(d)
    if payload[0] == 'gif': shutil.copyfile(payload[1], d / 'anim.gif')
    elif payload[0] == 'sheet': payload[1].save(d / 'sheet.png')
    else:
        (d / 'frames').mkdir()
        for i, p in enumerate(payload[1]):
            with Image.open(p) as im: im.convert('RGBA').save(d / 'frames' / f'{i:04d}.png')
    keep = {k: v for k, v in old.items() if k in ('fps', 'loop', 'duration')}
    meta.update(keep)
    for k, v in (('fps', fps), ('loop', loop), ('duration', duration)):
        if v is not None: meta[k] = v
    write_json(d / 'emotion.json', meta)
    return kind


def reset_emotion(char, emotion):
    """Back to the built-in animation (removes the user asset and its settings)."""
    d = emotion_dir(char, emotion)
    if d and d.exists(): shutil.rmtree(d, ignore_errors=True)


def import_character_image(path, name):
    """One image / GIF / sheet becomes a whole character: it is used for every emotion (fallback = idle)."""
    cid = create_character(name, fallback='idle')
    p = Path(path)
    import_emotion(cid, 'idle', p, kind='gif' if p.suffix.lower() == '.gif' else 'frames')
    return cid


def delete_character(char):
    d = find_dir(char)
    if d and str(d).startswith(str(user_root())): shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- rendering for Tk's transparentcolor
KEY_RGB = (255, 0, 255)


def rendered_top(frame, box_w, box_h, grid):
    """Y (in the box) of the highest opaque pixel of `frame` as render() draws it; 0 when the frame is empty. Anchors the speech bubble to the sprite."""
    w, h = frame.size
    sc = min(box_w / w, box_h / h)
    if grid: sc = max(1, int(sc))
    nh = max(1, int(h * sc)); bb = frame.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
    return 0 if bb is None else max(0, box_h - nh + int(bb[1] * sc))


def render(frame, box_w, box_h, grid, bg=KEY_RGB):
    """RGBA frame -> RGB image box_w x box_h on the key colour. Alpha is thresholded (no half-transparent halo), pixels that equal the key are nudged.
    Pixel-art stays crisp (NEAREST); big custom images are fitted with LANCZOS and aligned to the bottom centre."""
    w, h = frame.size
    sc = min(box_w / w, box_h / h)
    nw, nh = max(1, int(w * sc)), max(1, int(h * sc))
    resample = Image.NEAREST if (grid or max(w, h) <= 64) else Image.LANCZOS
    if grid: sc2 = max(1, int(sc)); nw, nh = w * sc2, h * sc2   # built-in grid sprites: integer scale; small custom pixel art: nearest-neighbour fitted to the box
    im = frame.resize((nw, nh), resample)
    a = im.getchannel('A').point(lambda v: 255 if v >= 128 else 0)
    out = Image.new('RGB', (box_w, box_h), bg)
    out.paste(im.convert('RGB'), ((box_w - nw) // 2, box_h - nh), a)
    arr = np.array(out)                                        # nudge opaque pixels that equal the key (they would turn transparent)
    m = np.zeros(arr.shape[:2], bool); m[box_h - nh:, (box_w - nw) // 2:(box_w - nw) // 2 + nw] = np.array(a) > 0
    hit = m & (arr == np.array(bg)).all(axis=2)
    if hit.any(): arr[hit, 2] = bg[2] - 1 if bg[2] else 1
    return Image.fromarray(arr)
