"""config.json helpers: defaults for the GUI-managed keys, tolerant load, atomic save."""
import json, os, tempfile
from pathlib import Path

# keys owned by the settings window (widget.DEFAULTS is extended with these)
NEW_DEFAULTS = {
    'wake_word': 'клод',               # the activation word (Russian spelling; Latin is transliterated)
    'wake_extra_variants': [],         # user-added spellings Vosk may produce for the word
    'wake_mode': 'fast',               # fast = trigger on the partial result, strict = only on a final result with confidence
    'wake_min_conf': 0.5,              # strict mode: minimal word confidence (0..1)
    'character': 'crab',               # crab | cat | robot | <folder under characters/>
    'mascot_opacity': 1.0,             # 0.3 .. 1.0
    'mascot_anim_speed': 1.0,          # multiplier of the animation fps
    'sleep_after_min': 10,             # idle for N minutes -> sleep emotion (0 = never)
    'emotion_map': {},                 # overrides of mascot.STATE_TO_EMOTION
    'settings_hotkey': 'ctrl+alt+s',
    'stop_hotkey': 'ctrl+alt+x',        # hush now: stop speech, drop the pending question
    'mute_hotkey': 'ctrl+alt+m',        # toggle "do not listen": the microphone stream is closed
    'mute_on_call': False,             # auto "do not listen" while Discord uses the microphone (Windows privacy consent store, read only)
    # characters and engines (Live2D / VRM are optional; the standard GIF/PNG character always works)
    'model_scale': 100,                # % of the 300x420 window used by Live2D / VRM models
    'window_mode': 'normal',           # normal | pet (desktop pet: sits on the taskbar edge, optional patrol)
    'pet_patrol': False,               # pet mode: wander along the taskbar while idle
    'compat_mode': 'normal',           # normal | borderless_game (re-assert topmost every 2 s; works over borderless-windowed games)
    'fullscreen_policy': 'exclusive',  # hide the character when: exclusive (D3D exclusive fullscreen / presentation) | any (any fullscreen app) | never
    'anim_enabled': True,              # False: static pose, minimal CPU/GPU
    'render_fps': 30,                  # 10..60, Live2D / VRM render rate
    'prewarm_engine': True,            # keep the renderer process warm (hidden) once a Live2D / VRM model is installed: fast switching
    'click_through': True,             # Live2D / VRM window ignores the mouse except on the model pixels
    'live2d_core_path': '',            # the user's own live2dcubismcore.min.js (proprietary, never bundled)
}

# every GUI-managed key -> allowed type(s); used to reject garbage from the window / a hand-edited file
TYPES = {
    'wake_word': str, 'wake_extra_variants': list, 'wake_mode': str, 'wake_min_conf': (int, float),
    'character': str, 'mascot_opacity': (int, float), 'mascot_anim_speed': (int, float),
    'sleep_after_min': (int, float), 'emotion_map': dict, 'settings_hotkey': str, 'stop_hotkey': str, 'mute_hotkey': str, 'mute_on_call': bool,
    'model_scale': (int, float), 'window_mode': str, 'pet_patrol': bool, 'compat_mode': str, 'fullscreen_policy': str, 'anim_enabled': bool,
    'render_fps': (int, float), 'live2d_core_path': str, 'click_through': bool, 'prewarm_engine': bool,
}


def load_config(path, defaults):
    """defaults + file. A broken file is kept as <name>.bad and defaults are used (never crash at boot)."""
    cfg = dict(defaults)
    path = Path(path)
    try:
        data = json.loads(path.read_text('utf8'))
        if not isinstance(data, dict): raise ValueError('config root is not an object')
        cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        try: os.replace(path, path.with_suffix('.bad'))
        except OSError: pass
    for k, t in TYPES.items():  # drop values of the wrong type
        if k in cfg and not isinstance(cfg[k], t): cfg[k] = defaults.get(k, NEW_DEFAULTS[k])
    return cfg


def save_config(path, cfg):
    """Atomic: write a temp file in the same directory, fsync, os.replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


# ---------------------------------------------------------------- validation of what the settings window sends
MODS = {'ctrl', 'alt', 'shift', 'windows', 'win'}
SPEC = {  # key -> (kind, arg...)
    'wake_word_enabled': ('bool',), 'wake_bargein': ('bool',), 'barge_in': ('bool',), 'wake_word': ('str',), 'wake_extra_variants': ('strlist',),
    'wake_mode': ('enum', ('fast', 'strict')), 'wake_min_conf': ('num', 0.0, 1.0),
    'character': ('str',), 'mascot_scale': ('int', 1, 12), 'mascot_opacity': ('num', 0.3, 1.0), 'mascot_anim_speed': ('num', 0.25, 3.0),
    'mascot_x': ('int?', -10000, 20000), 'mascot_y': ('int?', -10000, 20000), 'sleep_after_min': ('num', 0, 600), 'emotion_map': ('map',),
    'mic_device': ('str?',), 'speaker_device': ('str?',), 'tts_speaker': ('str',), 'tts_rate': ('num', 0.5, 2.0),
    'hotkey': ('hotkey',), 'toggle_hotkey': ('hotkey?',), 'settings_hotkey': ('hotkey?',), 'stop_hotkey': ('hotkey?',), 'mute_hotkey': ('hotkey?',), 'mute_on_call': ('bool',),
    'speak_mode': ('enum', ('immediate', 'on_request')), 'confirm_mode': ('enum', ('unclear', 'always', 'never')),
    'ask_more_after_done': ('bool',), 'trivial_direct': ('bool',), 'new_session_model': ('str',),
    'model_scale': ('int', 40, 200), 'window_mode': ('enum', ('normal', 'pet')), 'pet_patrol': ('bool',),
    'compat_mode': ('enum', ('normal', 'borderless_game')), 'fullscreen_policy': ('enum', ('exclusive', 'any', 'never')),
    'anim_enabled': ('bool',), 'click_through': ('bool',), 'prewarm_engine': ('bool',), 'render_fps': ('int', 10, 60), 'live2d_core_path': ('str?',),
    'followup_timeout_sec': ('num', 1, 60), 'silence_sec': ('num', 0.3, 5), 'bubble_hide_sec': ('num', 1, 60), 'default_project_dir': ('str',),
}


def valid_hotkey(s):
    parts = [p for p in str(s).lower().replace(' ', '').split('+') if p]
    return bool(parts) and parts[-1] not in MODS and all(p in MODS for p in parts[:-1]) and len(set(parts)) == len(parts)


def validate_changes(changes, emotions=(), states=()):
    """-> (clean dict, {key: error text}). Unknown keys are dropped, numbers clamped to their range, wrong types rejected."""
    clean, errs = {}, {}
    for k, v in changes.items():
        spec = SPEC.get(k)
        if spec is None: continue
        kind = spec[0]; opt = kind.endswith('?'); kind = kind.rstrip('?')
        try:
            if v in (None, '') and opt: clean[k] = None; continue
            if kind == 'bool':
                if not isinstance(v, bool): raise ValueError('ожидается да/нет')
                clean[k] = v
            elif kind == 'str':
                if not isinstance(v, str) or not v.strip(): raise ValueError('пустое значение')
                clean[k] = v.strip()
            elif kind == 'strlist':
                if isinstance(v, str): v = [x for x in v.replace(';', ',').split(',')]
                clean[k] = [str(x).strip() for x in v if str(x).strip()]
            elif kind == 'enum':
                if v not in spec[1]: raise ValueError('допустимо: ' + ', '.join(spec[1]))
                clean[k] = v
            elif kind in ('num', 'int'):
                if isinstance(v, bool): raise ValueError('ожидается число')
                x = float(v); x = min(spec[2], max(spec[1], x)); clean[k] = int(round(x)) if kind == 'int' else x
            elif kind == 'hotkey':
                if not valid_hotkey(v): raise ValueError('нужна комбинация вида ctrl+alt+space')
                clean[k] = '+'.join(p for p in str(v).lower().replace(' ', '').split('+') if p)
            elif kind == 'map':
                if not isinstance(v, dict): raise ValueError('ожидается таблица')
                clean[k] = {s: e for s, e in v.items() if (not states or s in states) and (not emotions or e in emotions)}
        except (ValueError, TypeError) as e:
            errs[k] = str(e)
    return clean, errs
