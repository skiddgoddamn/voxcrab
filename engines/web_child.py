"""Renderer process: loopback server + one frameless, topmost, per-pixel-transparent WebView2 window (pywebview).
Run by webhost.WebHost, never imported by the widget. Protocol: JSON lines. stdin = commands, stdout = events.
  in : {cmd: init|emotion|config|pause ...}  -> forwarded to the page;  {cmd: move|resize|topmost|show|hide|quit}  -> handled here
  out: {ev: ready|page|loaded|error|moved|menu}
pywebview transparent=True alone leaves the WinForms back colour visible on Windows, so after the window exists we make the form
black and extend the DWM frame into the whole client area: WebView2 transparent pixels then show the desktop (verified by screen grab).
"""
import argparse, json, os, sys, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines import web_server, hitmask, WEB_DIR, VENDOR  # noqa: E402

HWND_TOPMOST, SWP_FLAGS = -1, 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW


def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + '\n'); sys.stdout.flush()


class Api:
    def __init__(self): self.win = None; self.hwnd = 0; self._drag = False; self.mask = None; self.box = None; self.click_through = False; self.passthrough = False

    def emit(self, s):
        try: o = json.loads(s)
        except ValueError: return
        if o.get('ev') == 'drag': self.start_drag()
        elif o.get('ev') == 'mask':
            self.mask = hitmask.Mask(o.get('w'), o.get('h'), o.get('bits'))
            box = hitmask.clean_box(o.get('box'))
            if box and (self.box is None or max(abs(a - b) for a, b in zip(box, self.box)) > 0.015): self.box = box; out({'ev': 'bbox', 'box': box})   # visible model bounds -> bubble anchor
        else:
            if o.get('ev') == 'loaded' and self.win is not None:
                try: self.win.show()                      # the window stays hidden until the model is drawn (no black box while loading)
                except Exception as e: out({'ev': 'error', 'msg': 'show: %s' % e})
            out(o)

    def start_drag(self):
        """Native-pixel drag without relying on JS mouse capture: follow the cursor while the left button is held."""
        if self._drag: return
        self._drag = True
        threading.Thread(target=self._drag_loop, daemon=True).start()

    def _drag_loop(self):
        import ctypes, time
        from ctypes import wintypes
        u = ctypes.windll.user32
        try:
            pt, r = wintypes.POINT(), wintypes.RECT()
            u.GetCursorPos(ctypes.byref(pt)); u.GetWindowRect(self.hwnd, ctypes.byref(r))
            ox, oy, w, h, last = pt.x - r.left, pt.y - r.top, r.right - r.left, r.bottom - r.top, 0.0
            while u.GetAsyncKeyState(0x01) & 0x8000:                       # VK_LBUTTON
                u.GetCursorPos(ctypes.byref(pt))
                u.SetWindowPos(self.hwnd, 0, pt.x - ox, pt.y - oy, 0, 0, 0x0001 | 0x0004 | 0x0010)   # NOSIZE | NOZORDER | NOACTIVATE
                if time.time() - last > 0.1: last = time.time(); out({'ev': 'moved', 'x': pt.x - ox, 'y': pt.y - oy})
                time.sleep(0.008)
            u.GetWindowRect(self.hwnd, ctypes.byref(r)); out({'ev': 'moved', 'x': r.left, 'y': r.top})
        finally: self._drag = False


def setup_window(win, api):
    import ctypes
    from ctypes import wintypes
    from System import Action
    from System.Drawing import Color
    import time
    for _ in range(100):                                    # the WinForms form appears a moment after start
        if win.native is not None: break
        time.sleep(0.1)
    form = win.native
    api.hwnd = form.Handle.ToInt64()

    def paint(): form.BackColor = Color.Black
    form.Invoke(Action(paint))

    class M(ctypes.Structure): _fields_ = [('l', ctypes.c_int), ('r', ctypes.c_int), ('t', ctypes.c_int), ('b', ctypes.c_int)]
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(api.hwnd), ctypes.byref(M(-1, -1, -1, -1)))
    u = ctypes.windll.user32
    st = u.GetWindowLongW(wintypes.HWND(api.hwnd), -20)
    u.SetWindowLongW(wintypes.HWND(api.hwnd), -20, (st | 0x80 | 0x08000000) & ~0x40000)   # TOOLWINDOW + NOACTIVATE, no taskbar button


def hit_loop(api):
    """Click-through except on the model: the window ignores the mouse while the cursor is over a transparent pixel (WS_EX_LAYERED | WS_EX_TRANSPARENT).
    The pixel mask comes from the page (5 Hz); the cursor is polled here, so the switch has no round trip."""
    import ctypes, time
    from ctypes import wintypes
    u = ctypes.windll.user32
    pt, r = wintypes.POINT(), wintypes.RECT()
    while True:
        time.sleep(0.03)
        if not api.hwnd: continue
        want = False
        if api.click_through and api.mask is not None and not api._drag:
            u.GetCursorPos(ctypes.byref(pt)); u.GetWindowRect(api.hwnd, ctypes.byref(r))
            want = not api.mask.hit(pt.x - r.left, pt.y - r.top, r.right - r.left, r.bottom - r.top)
        elif api.passthrough: want = False
        if want != api.passthrough:
            ex = u.GetWindowLongW(wintypes.HWND(api.hwnd), -20)
            ex = (ex | 0x80000 | 0x20) if want else (ex & ~0x20)
            u.SetWindowLongW(wintypes.HWND(api.hwnd), -20, ex); api.passthrough = want


def place(api, x, y, w=None, h=None):
    import ctypes
    flags = 0x0004 | 0x0010 | (0x0001 if w is None else 0)             # NOZORDER | NOACTIVATE (| NOSIZE)
    ctypes.windll.user32.SetWindowPos(api.hwnd, 0, int(x), int(y), int(w or 0), int(h or 0), flags)


def command_loop(win, api, srv, index_url):
    for line in sys.stdin:
        try: m = json.loads(line)
        except ValueError: continue
        c = m.get('cmd')
        try:
            if c == 'quit': win.destroy(); return
            elif c == 'move': place(api, m['x'], m['y'])
            elif c == 'place': place(api, m['x'], m['y'], m['w'], m['h'])
            elif c == 'serve': srv.conf['model_dir'] = m.get('model_dir') or None; srv.conf['core_path'] = m.get('core') or None   # re-point a warm renderer
            elif c == 'unload': win.hide(); api.mask = None; api.box = None
            elif c == 'clickthrough': api.click_through = bool(m.get('value'))
            elif c == 'show': win.show()
            elif c == 'hide': win.hide()
            elif c == 'topmost':
                import ctypes
                ctypes.windll.user32.SetWindowPos(api.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
            else: win.evaluate_js('window.vw && window.vw.cmd(%s)' % json.dumps(m))
        except Exception as e:
            out({'ev': 'error', 'msg': 'child: %s' % e})
    win.destroy()   # parent closed our stdin: it is gone, exit


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor: coordinates are physical pixels, like the Tk parent (a 125% / 150% monitor stays crisp and exact)
    except Exception: pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--x', type=int, default=100); ap.add_argument('--y', type=int, default=100)
    ap.add_argument('--w', type=int, default=300); ap.add_argument('--h', type=int, default=400)
    ap.add_argument('--model-dir', default=''); ap.add_argument('--core', default=''); ap.add_argument('--data', default='')
    ap.add_argument('--no-topmost', action='store_true'); ap.add_argument('--click-through', action='store_true')
    a = ap.parse_args()
    import webview
    srv, port, tok = web_server.make_server(WEB_DIR, VENDOR, a.model_dir or None, a.core or None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = Api()
    index_url = 'http://127.0.0.1:%d/%s/index.html' % (port, tok)
    win = webview.create_window('voice-widget character', index_url, js_api=api, width=a.w, height=a.h, x=a.x, y=a.y,
                                frameless=True, transparent=True, on_top=not a.no_topmost, easy_drag=False, shadow=False, focus=False, resizable=False, hidden=True)
    api.win = win; api.click_through = a.click_through
    threading.Thread(target=hit_loop, args=(api,), daemon=True).start()

    def started():
        try: setup_window(win, api)
        except Exception as e: out({'ev': 'error', 'msg': 'window setup: %s' % e})
        try: place(api, a.x, a.y, a.w, a.h)                    # pywebview scales x/y/w/h itself; the parent speaks physical pixels like Tk does
        except Exception as e: out({'ev': 'error', 'msg': 'place: %s' % e})
        out({'ev': 'ready'})
        command_loop(win, api, srv, index_url)
    webview.start(started, private_mode=True)   # own temp profile per process: two renderers (or a stale one) never fight over one WebView2 user-data folder


if __name__ == '__main__':
    main()
