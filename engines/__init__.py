"""Pluggable character engines.

  gif    built-in: GIF / PNG / sprite sheet / procedural pixel themes, rendered by tkinter (mascot.py). Always available.
  live2d Live2D Cubism 3/4/5 via pixi-live2d-display (MIT) in a WebView2 window. Needs the user's own live2dcubismcore.min.js.
  vrm    VRM 0.x / 1.0 via three.js + @pixiv/three-vrm (MIT) in a WebView2 window.

The web engines are optional adapters: when the webview package, the vendored JS or the Cubism Core is missing, the adapter reports
"unavailable" with a reason and the widget keeps using the standard (gif) character.
"""
import importlib.util, sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / 'web'
VENDOR = WEB_DIR / 'vendor'
ENGINES = ['gif', 'live2d', 'vrm']
ENGINE_RU = {'gif': 'Стандартный (GIF/PNG)', 'live2d': 'Live2D (Cubism 3-5)', 'vrm': 'VRM (3D)'}
VENDOR_FILES = {'live2d': ['pixi.min.js', 'cubism4.min.js'], 'vrm': ['vrm.bundle.js']}


def webview_ok():
    return sys.platform == 'win32' and importlib.util.find_spec('webview') is not None


def check_core(path):
    """-> (ok, reason). The Cubism Core is proprietary (Live2D Proprietary Software License): we never bundle or download it."""
    if not path: return False, 'Укажите путь к live2dcubismcore.min.js (скачивается с live2d.com в составе Cubism SDK for Web).'
    p = Path(path)
    if not p.is_file(): return False, 'Файл Cubism Core не найден: %s' % path
    if p.suffix.lower() != '.js': return False, 'Нужен файл live2dcubismcore.min.js.'
    try:
        if p.stat().st_size > 8 * 1024 * 1024: return False, 'Файл слишком большой для Cubism Core.'
        if b'Live2DCubismCore' not in p.read_bytes(): return False, 'Файл не похож на Cubism Core (нет Live2DCubismCore).'
    except OSError as e:
        return False, 'Файл не читается: %s' % e
    return True, ''


def availability(cfg):
    """{engine: (ok, reason)} for the settings tab and for the fallback logic in mascot.py."""
    out = {'gif': (True, '')}
    base = None
    if not webview_ok(): base = 'Нужны Windows и пакет pywebview (pip install pywebview) с Edge WebView2.'
    for eng in ('live2d', 'vrm'):
        missing = [f for f in VENDOR_FILES[eng] if not (VENDOR / f).is_file()]
        if base: out[eng] = (False, base)
        elif missing: out[eng] = (False, 'Не найдены файлы движка в engines/web/vendor: %s (npm run build).' % ', '.join(missing))
        elif eng == 'live2d':
            ok, why = check_core(cfg.get('live2d_core_path'))
            out[eng] = (ok, why)
        else: out[eng] = (True, '')
    return out
