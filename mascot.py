"""Desktop mascot overlay: pixel-art crab + game-style speech bubble (tkinter, Windows).
All Tk calls happen on the main thread; other threads use Mascot.call(fn, *args)."""
import queue, random, sys, time
import tkinter as tk
from tkinter import font as tkfont

from PIL import ImageTk

import characters
import engines
import engines.webhost
from engines import hitmask
import winmode
from characters import GW, GH, STATE_TO_EMOTION  # noqa: F401  (STATE_TO_EMOTION: state -> emotion table, see characters.py)

KEY = '#FF00FF'  # transparent color


def dpi_aware():
    if sys.platform != 'win32': return
    import ctypes
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass


def tool_window(win):  # no taskbar / alt-tab entry
    if sys.platform != 'win32': return
    import ctypes
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        st = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, (st | 0x80) & ~0x40000)  # TOOLWINDOW, not APPWINDOW
    except Exception: pass


# ---------------------------------------------------------------- bubble
class Bubble:
    B, PAD, MAXW = 3, 12, 320

    def __init__(self, root):
        self.win = tk.Toplevel(root); self.win.overrideredirect(True); self.win.attributes('-topmost', True)
        self.win.attributes('-transparentcolor', KEY); self.win.configure(bg=KEY); self.win.withdraw()
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0); self.cv.pack()
        self.font = tkfont.Font(family='Consolas', size=11, weight='bold')
        self.small = tkfont.Font(family='Consolas', size=9, weight='bold')
        self.lh = self.font.metrics('linespace') + 3
        self.lines, self.items, self.total, self.shown = [], [], 0, 0.0
        self.visible = False; self.hide_at = None; self.last = 0.0; self.cps = 16.0; self.opts_pending = None
        self.on_pick = None

    def wrap(self, text, width, indent=0):
        out = []
        for para in text.split('\n'):
            cur = ''
            for w in para.split(' '):
                t = (cur + ' ' + w).strip()
                while self.font.measure(t) > width and not cur and len(w) > 1:  # very long word
                    k = len(t)
                    while k > 1 and self.font.measure(t[:k]) > width: k -= 1
                    out.append(t[:k]); t = t[k:]; w = t
                if self.font.measure(t) > width and cur: out.append(cur); cur = w
                else: cur = t
            out.append(cur)
        return out

    def show(self, text, kind, options, header, anchor, cps, hide):
        """anchor = (crab_cx, crab_top) in screen px."""
        B, P = self.B, self.PAD
        style = {'say': ('#FFFFFF', '#1a1a1a', self.font), 'user': ('#FFF1B8', '#6a5200', self.font),
                 'listen': ('#E6F7EC', '#1f6b3b', self.font)}[kind]
        bg, fg, fnt = style
        if kind == 'user': text = 'Вы: ' + text
        text = text[:300] + ('…' if len(text) > 300 else '')
        inner = self.MAXW - 2 * (P + B)
        body = self.wrap(text, inner)
        opt_lines = []  # (opt_index, text)
        for i, o in enumerate(options or []):
            for j, l in enumerate(self.wrap(f'{i + 1}. {o}', inner - 4)): opt_lines.append((i, l if j == 0 else '   ' + l))
        widest = max([self.font.measure(l) for l in body] + [self.font.measure(l) for _, l in opt_lines] + [self.small.measure(header or '')] + [60])
        w = min(self.MAXW, widest + 2 * (P + B)) if kind != 'say' or len(text) > 12 else widest + 2 * (P + B)
        w = max(w, 96)
        hh = (self.lh if header else 0)
        h = 2 * (P // 2 + B) + hh + len(body) * self.lh + (len(opt_lines) * self.lh + 6 if opt_lines else 0)
        tail = 4 * 4 - B
        cv = self.cv; cv.delete('all'); cv.config(width=w, height=h + tail)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = int(min(max(anchor[0] - w * 0.6, 4), sw - w - 4)); y = int(anchor[1] - (h + tail) + 6)
        if y < 4: y = 4
        tx = int(min(max(anchor[0] - x, 16), w - 16))
        def shape(inset, c):
            i = inset
            cv.create_rectangle(i + B, i, w - i - B - 1, h - i - 1, fill=c, outline=c)
            cv.create_rectangle(i, i + B, w - i - 1, h - i - B - 1, fill=c, outline=c)
        shape(0, '#1a1a1a'); shape(B, bg)
        for k in range(4):  # stepped tail
            hw = 10 - 3 * k; y0 = h - B + k * 4
            cv.create_rectangle(tx - hw, y0, tx + hw, y0 + 3, fill='#1a1a1a', outline='#1a1a1a')
            if hw - B > 0: cv.create_rectangle(tx - hw + B, y0, tx + hw - B, y0 + (3 if k else 2 + B), fill=bg, outline=bg)
        ty = P // 2 + B
        if header: cv.create_text(P + B, ty, anchor='nw', text=header, font=self.small, fill='#B5583A'); ty += self.lh
        self.items, self.lines = [], []
        for l in body:
            self.items.append(cv.create_text(P + B, ty, anchor='nw', text='', font=fnt, fill=fg)); self.lines.append(l); ty += self.lh
        self.total = sum(len(l) for l in self.lines); self.shown = 0.0
        self.opts_pending = (opt_lines, ty + 6, bg) if opt_lines else None
        self.cps = cps; self.hide_at = None if hide is None else time.time() + hide
        self.win.geometry(f'+{x}+{y}'); self.win.deiconify(); self.win.lift(); self.win.attributes('-topmost', True)
        self.visible = True; self.last = time.time()

    def draw_options(self):
        lines, y, bg = self.opts_pending; self.opts_pending = None
        cv = self.cv
        for i, l in lines:
            tag = f'opt{i}'
            it = cv.create_text(self.PAD + self.B + 2, y, anchor='nw', text=l, font=self.font, fill='#B5583A', tags=(tag,))
            cv.tag_bind(tag, '<Enter>', lambda e, t=tag: (cv.itemconfig(t, fill='#1a1a1a'), cv.config(cursor='hand2')))
            cv.tag_bind(tag, '<Leave>', lambda e, t=tag: (cv.itemconfig(t, fill='#B5583A'), cv.config(cursor='')))
            cv.tag_bind(tag, '<Button-1>', lambda e, i=i: self.on_pick and self.on_pick(i))
            y += self.lh

    def tick(self):
        if not self.visible: return
        now = time.time()
        if self.shown < self.total:
            self.shown = min(self.total, self.shown + (now - self.last) * self.cps); n = int(self.shown)
            for it, l in zip(self.items, self.lines):
                self.cv.itemconfig(it, text=l[:max(n, 0)]); n -= len(l)
        elif self.opts_pending: self.draw_options()
        self.last = now
        if self.hide_at and now >= self.hide_at and self.shown >= self.total: self.hide()

    def hide(self):
        self.visible = False; self.hide_at = None; self.win.withdraw()


# ---------------------------------------------------------------- mascot
class Mascot:
    """Desktop sprite. Character / emotions come from characters.py; states from the widget are mapped to emotions (STATE_TO_EMOTION)."""
    def __init__(self, cfg, on_moved=None, menu_builder=None, on_pick=None, log=None):
        dpi_aware()
        self.cfg, self.on_moved, self.menu_builder, self.on_pick = cfg, on_moved, menu_builder, on_pick
        self.log = log or (lambda *a: print(*a, flush=True)); self.settings_host = None; self.settings_win = None
        self.q = queue.Queue(); self.quit = False
        self.hidden = bool(cfg.get('mascot_hidden')); self.options = []
        self.emotion, self.t0, self.idle_since = 'idle', time.time(), time.time()
        self.transient_until = 0.0; self.override = None  # (emotion, until) while previewing
        self.anims, self.photos, self._last = {}, {}, None; self.look_override = {}
        self.muted = False; self.web = None; self.web_box = None; self.sprite_top = 0; self.host = None; self.web_key = None; self.web_emotion = None; self.engine_status = ('gif', '')  # (engine in use, why the model is not used)
        self.fs_hidden = False; self._fs_t = self._top_t = self._moved_t = self._place_t = 0.0; self._patrol = None; self._notified = None; self._dragging = False
        self.root = tk.Tk(); r = self.root
        r.overrideredirect(True); r.attributes('-topmost', True); r.attributes('-transparentcolor', KEY); r.configure(bg=KEY)
        self.S = int(cfg.get('mascot_scale', 5)); self.W, self.H = GW * self.S, GH * self.S
        self.label = tk.Label(r, bg=KEY, bd=0, highlightthickness=0); self.label.pack()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x = cfg.get('mascot_x'); y = cfg.get('mascot_y')
        if x is None or y is None or not (0 <= x <= sw - 20 and 0 <= y <= sh - 20): x, y = sw - self.W - 40, sh - self.H - 80
        self.x, self.y = int(x), int(y)
        r.geometry(f'{self.W}x{self.H}+{self.x}+{self.y}')
        self.bubble = Bubble(r); self.bubble.on_pick = self._pick
        tool_window(r); tool_window(self.bubble.win)
        self.label.bind('<ButtonPress-1>', self._press); self.label.bind('<B1-Motion>', self._drag); self.label.bind('<ButtonRelease-1>', self._release)
        self.label.bind('<Button-3>', self._menu)
        self._apply_look()
        if self.hidden: r.withdraw()
        self._render(); r.after(30, self._poll); r.after(40, self._animate); r.after(2500, self._prewarm)

    # ---- thread-safe entry
    def call(self, fn, *a): self.q.put((fn, a))

    def set_state(self, s): self.call(self._set_emotion, characters.resolve_emotion(s, self.cfg.get('emotion_map')))
    def set_emotion(self, name): self.call(self._set_emotion, characters.resolve_emotion(name, self.cfg.get('emotion_map')))
    def set_mouth(self, level): self.call(self._mouth, level)   # TTS amplitude 0..1 (None = no audio, the renderer falls back to its own motion)
    def preview(self, emotion, secs=4.0): self.call(self._preview, emotion, secs)
    def apply_settings(self): self.call(self._apply_look, True)
    def open_settings(self, tab=None): self.call(self._open_settings, tab)
    def say(self, text, kind='say', options=None, header=None, cps=16.0, hide=None, force=False):
        self.call(self._say, text, kind, options, header, cps, hide, force)
    def release(self, delay): self.call(self._release_bubble, delay)
    def hide_bubble(self): self.call(self.bubble.hide)
    def toggle(self): self.call(self._set_hidden, not self.hidden)
    def set_muted(self, on): self.call(self._set_muted, bool(on))
    def _set_muted(self, on): self.muted = on; self._last = None
    def set_hidden(self, v): self.call(self._set_hidden, v)
    def stop(self): self.quit = True

    # ---- main thread: emotions
    def _anim(self, emotion):
        a = self.anims.get(emotion)
        if a is None: a = self.anims[emotion] = characters.load_animation(self._lk('character', 'crab'), emotion, self.log)
        return a
    def _set_emotion(self, e):
        now = time.time()
        if e == self.emotion and self.transient_until == 0.0: return
        self.emotion = e; self.t0 = now; self.transient_until = 0.0
        if e == 'idle': self.idle_since = now
        else:
            d = self._anim(e).duration
            if d > 0: self.transient_until = now + d
    def _preview(self, e, secs):
        e = characters.resolve_emotion(e); self.override = (e, time.time() + secs); self.t0 = time.time()
    def _current(self):
        now = time.time()
        if getattr(self, 'muted', False): return 'sleep'            # "do not listen": the mascot sleeps
        if self.override:
            if now < self.override[1]: return self.override[0]
            self.override = None; self.t0 = now
        if self.transient_until and now > self.transient_until: self._set_emotion('idle')
        mins = float(self.cfg.get('sleep_after_min', 10) or 0)
        if self.emotion == 'idle' and mins > 0 and now - self.idle_since > mins * 60: return 'sleep'
        return self.emotion
    def _lk(self, key, default=None):  # look setting: settings-window preview override, else config
        return self.look_override.get(key, self.cfg.get(key, default))
    def set_look_override(self, d): self.call(self._set_override, d)
    def _set_override(self, d): self.look_override = dict(d or {}); self._apply_look(True)
    def _apply_look(self, rerender=False):
        try: self.root.attributes('-alpha', min(1.0, max(0.3, float(self._lk('mascot_opacity', 1.0)))))
        except Exception: pass
        S = min(12, max(1, int(self._lk('mascot_scale', 5))))
        if S != self.S:
            oldh = self.H; self.S = S; self.W, self.H = GW * S, GH * S; self.y += oldh - self.H
            self.root.geometry(f'{self.W}x{self.H}+{self.x}+{self.y}')
        self.anims.clear(); self.photos.clear(); self._last = None; self.t0 = time.time()
        self._sync_backend()
        if rerender and not self.web: self._render()
    def _open_settings(self, tab=None):
        if self.settings_host is None: return
        try:
            if self.settings_win is not None and self.settings_win.alive(): self.settings_win.focus(tab); return
            import settings_ui
            self.settings_win = settings_ui.SettingsWindow(self.root, self.settings_host, tab=tab)
        except Exception:
            import traceback; self.log('settings window failed', traceback.format_exc())

    # ---- main thread: bubble / window
    def _anchor(self):
        if not self.web: return (self.x + self.W // 2, self.y + max(2 * self.S, self.sprite_top - self.S))
        return hitmask.head_anchor(self.web_box, self.x, self.y, self.W, self.H)    # just above the visible model; window-relative fallback
    def _follow_bubble(self):
        if self.bubble.visible:
            b = self.bubble.win; ax, ay = self._anchor()
            b.geometry(f'+{(ax - 20) if self.web else self.x - 20}+{max(0, ay - b.winfo_height() + (2 if self.web else 6))}')
    def _say(self, text, kind, options, header, cps, hide, force):
        if self.hidden and not force and kind != 'say': return
        self.options = list(options or [])
        self.bubble.show(text, kind, self.options, header, self._anchor(), cps, hide)
    def _release_bubble(self, delay):
        if self.bubble.visible: self.bubble.hide_at = time.time() + delay
    def _pick(self, i):
        if i < len(self.options):
            lab = self.options[i]; self.bubble.hide()
            if self.on_pick: self.on_pick(lab)
    def _set_hidden(self, v):
        self.hidden = v; self.cfg['mascot_hidden'] = v
        self._apply_visibility()
        if self.on_moved: self.on_moved()
    def _poll(self):
        try:
            while True:
                fn, a = self.q.get_nowait(); fn(*a)
        except queue.Empty: pass
        except Exception as e: print('ui error', e, flush=True)
        self.bubble.tick()
        if self.quit: self._stop_web(final=True); self.root.destroy(); return
        self.root.after(30, self._poll)
    def _animate(self):
        try:
            if self.web: self._web_tick()
            elif not (self.hidden or self.fs_hidden): self._render()
            self._housekeeping()
        except Exception:
            import traceback; self.log('animate failed', traceback.format_exc())
        self.root.after(40 if self.cfg.get('anim_enabled', True) is not False else 250, self._animate)
    def _render(self):
        cur = self._current(); anim = self._anim(cur)
        i = anim.index((time.time() - self.t0) * float(self._lk('mascot_anim_speed', 1.0) or 1.0)) if self.cfg.get('anim_enabled', True) is not False else 0
        key = (cur, i)
        if key == self._last: return
        self._last = key; self.sprite_top = characters.rendered_top(anim.frames[i], self.W, self.H, anim.grid)
        ph = self.photos.get(key)
        if ph is None:
            if len(self.photos) > 400: self.photos.clear()
            ph = self.photos[key] = ImageTk.PhotoImage(characters.render(anim.frames[i], self.W, self.H, anim.grid))
        self.label.config(image=ph)

    def _press(self, e): self.drag = (e.x_root - self.x, e.y_root - self.y, False)
    def _drag(self, e):
        ox, oy, _ = self.drag; self.drag = (ox, oy, True)
        self.x, self.y = e.x_root - ox, e.y_root - oy
        self.root.geometry(f'+{self.x}+{self.y}')
        self._follow_bubble(); self._moved_t = time.time()
    def _release(self, e):
        if getattr(self, 'drag', (0, 0, False))[2]:
            self.cfg['mascot_x'], self.cfg['mascot_y'] = self.x, self.y
            if self.on_moved: self.on_moved()
    def move_to(self, x, y):
        self._place(x, y); self.cfg['mascot_x'], self.cfg['mascot_y'] = self.x, self.y
    def _menu(self, e): self._menu_at(e.x_root, e.y_root)
    def _menu_at(self, x, y):
        m = tk.Menu(self.root, tearoff=0)
        if self.menu_builder: self.menu_builder(m)
        try: m.tk_popup(x, y)
        finally: m.grab_release()

    # ---- engines: the standard tk sprite or a Live2D / VRM renderer process (engines/), with a fallback to the standard one
    def _place(self, x, y):
        self.x, self.y = int(x), int(y); self._place_t = time.time()
        if self.web: self.web.send(cmd='move', x=self.x, y=self.y)
        else: self.root.geometry(f'+{self.x}+{self.y}')

    def _set_box(self, w, h):  # change the window size keeping the bottom centre of the character where it was
        self.x += (self.W - w) // 2; self.y += self.H - h; self.W, self.H = w, h
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.x = min(max(self.x, 0), max(0, sw - w)); self.y = min(max(self.y, 0), max(0, sh - h))

    def _apply_visibility(self):
        vis = not (self.hidden or self.fs_hidden)
        if self.web:
            self.root.withdraw(); self.web.send(cmd='pause', value=not vis); self.web.send(cmd='show' if vis else 'hide')
        elif vis: self.root.deiconify(); self.root.attributes('-topmost', True)
        else: self.root.withdraw()
        if not vis: self.bubble.hide()

    def _web_cfg(self):
        return {'cmd': 'config', 'fps': int(min(60, max(10, float(self.cfg.get('render_fps', 30) or 30)))), 'anim': self.cfg.get('anim_enabled', True) is not False}

    def _sync_backend(self):
        """Pick the renderer for the current character. Any problem -> standard sprite + a note in engine_status (never a crash)."""
        char = self._lk('character', 'crab'); eng = 'gif'
        try:
            import engines.library as lib
            eng = lib.engine_of(char)
        except Exception as e:
            self.log('[engine] cannot read character', char, e)
        if eng == 'gif':
            self._stop_web(back=True); self.engine_status = ('gif', ''); return
        ok, why = engines.availability(self.cfg)[eng]
        if not ok: self._web_fallback(why, char); return
        sc = min(2.0, max(0.4, float(self._lk('model_scale', 100) or 100) / 100.0)); w, h = int(300 * sc), int(420 * sc)
        key = (char, w, h, self.cfg.get('live2d_core_path') if eng == 'live2d' else None)
        if self.web and self.web_key == key and self.web.status != 'failed':
            self.web.send(**self._web_cfg()); self.web.send(cmd='clickthrough', value=self.cfg.get('click_through', True) is not False); self._pet_snap(); return
        try: model = lib.load_model(char)
        except Exception as e: self._web_fallback(str(e), char); return
        self._stop_web()
        self._set_box(w, h); self._pet_snap(place=False)
        self.web = self._get_host()
        init = dict(self._web_cfg(), cmd='init', engine=eng, entry=model['entry'], profiles=model['profiles'], emotion=self._current())
        self.web.start(init, self.x, self.y, self.W, self.H, model['dir'], core_path=self.cfg.get('live2d_core_path') if eng == 'live2d' else None,
                       click_through=self.cfg.get('click_through', True) is not False)
        self.web_key, self.web_emotion = key, None; self.engine_status = (eng, ''); self.root.withdraw()
        self.log('[engine] started', eng, char, 'box', w, h)

    def _get_host(self):
        if self.host is None: self.host = engines.webhost.WebHost(on_event=lambda e: self.call(self._web_event, e), log=self.log)
        return self.host

    def _prewarm(self):
        """Start the renderer process in the background (hidden, no model) so the first Live2D / VRM character appears in a second, not in ten."""
        if self.quit or self.web is not None or self.cfg.get('prewarm_engine', True) is False: return
        try:
            import engines.library as lib
            av = engines.availability(self.cfg)
            if any(av[m['engine']][0] for m in lib.list_models()): self._get_host().warm(); self.log('[engine] prewarm started')
        except Exception as e: self.log('[engine] prewarm skipped', e)

    def _stop_web(self, back=False, final=False):
        had = self.web is not None
        if had:
            keep = not final and self.cfg.get('prewarm_engine', True) is not False and self.web.status in ('starting', 'loaded')
            self.web_box = None
            try: self.web.stop(keep_warm=keep)
            except Exception as e: self.log('[engine] stop failed', e)
        if final and self.host is not None:
            try: self.host.stop()
            except Exception as e: self.log('[engine] stop failed', e)
        self.web = None; self.web_key = None
        if back and (had or self.W != GW * self.S):
            self._set_box(GW * self.S, GH * self.S); self.root.geometry(f'{self.W}x{self.H}+{self.x}+{self.y}'); self._last = None
        if back: self._apply_visibility()

    def _web_fallback(self, why, char=None):
        self.log('[engine] fallback to the standard character:', why)
        self.engine_status = ('gif', why)
        self._stop_web(back=True)
        if (char, why) != self._notified:
            self._notified = (char, why)
            self._say('Модель не запущена, показываю стандартного персонажа: ' + why, 'say', None, None, 40.0, 8.0, True)

    def _web_event(self, e):
        ev = e.get('ev')
        if ev == 'bbox': self.web_box = e.get('box'); self._follow_bubble()
        elif ev == 'loaded': self.web_box = None; self.web_emotion = None; self._notified = None; self.log('[engine] loaded', e.get('info'))
        elif ev == 'moved' and self.web and time.time() - self._place_t > 0.4:
            self.x, self.y = int(e['x']), int(e['y']); self.cfg['mascot_x'], self.cfg['mascot_y'] = self.x, self.y
            self._moved_t = time.time(); self._follow_bubble()
            if self.on_moved: self.on_moved()
        elif ev == 'menu' and self.web:
            import ctypes
            from ctypes import wintypes
            p = wintypes.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(p)); self._menu_at(p.x, p.y)

    def _mouth(self, level):
        if self.web and self.web.status == 'loaded': self.web.send(cmd='mouth', level=level)

    def _web_tick(self):
        why = self.web.poll()
        if why: self._web_fallback(why, self._lk('character')); return
        cur = self._current()
        if self.web.status == 'loaded' and cur != self.web_emotion and self.web.send(cmd='emotion', name=cur): self.web_emotion = cur

    def _pet_snap(self, place=True):
        if self._lk('window_mode', 'normal') != 'pet': return
        area = winmode.work_area()
        if area:
            x, y = winmode.pet_position(area, self.W, self.H, self.x)
            if place: self._place(x, y)
            else: self.x, self.y = x, y

    def _housekeeping(self):
        now = time.time()
        if now - self._fs_t > 1.0:
            self._fs_t = now
            hide = winmode.should_hide(winmode.notification_state(), self.cfg.get('fullscreen_policy', 'exclusive'))
            if hide != self.fs_hidden:
                self.fs_hidden = hide; self._apply_visibility(); self.log('[window] fullscreen app', 'hides' if hide else 'released', 'the character')
        if self.cfg.get('compat_mode') == 'borderless_game' and now - self._top_t > 2.0 and not (self.hidden or self.fs_hidden):
            self._top_t = now
            if self.web: self.web.send(cmd='topmost')
            else: winmode.keep_on_top(self._hwnd())
        if self._lk('window_mode', 'normal') == 'pet': self._pet_tick(now)

    def _hwnd(self):
        import ctypes
        return ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

    def _pet_tick(self, now):
        area = winmode.work_area()
        if not area: return
        if self._moved_t:
            if now - self._moved_t < 0.6: return
            self._moved_t = 0.0; x, y = winmode.pet_position(area, self.W, self.H, self.x)   # drag finished: sit back on the taskbar edge
            if (x, y) != (self.x, self.y): self._place(x, y); self.cfg['mascot_x'], self.cfg['mascot_y'] = x, y
            if self.on_moved: self.on_moved()
            return
        if not self.cfg.get('pet_patrol') or self.emotion != 'idle' or self.bubble.visible or self.override: self._patrol = None; return
        last = getattr(self, '_patrol_t', now); self._patrol_t = now
        if self._patrol is None or abs(self._patrol - self.x) < 1:
            if getattr(self, '_patrol_wait', 0) > now: return
            if self._patrol is not None: self._patrol = None; self._patrol_wait = now + random.uniform(4, 12); return
            self._patrol = random.randint(area[0], max(area[0], area[2] - self.W))
        self._place(winmode.patrol_step(self.x, self._patrol, min(0.2, now - last)), area[3] - self.H)

    def run(self): self.root.mainloop()
