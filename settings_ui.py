"""Settings window (tkinter / ttk). Runs on the Tk main thread as a Toplevel of the mascot root.
`host` (the Widget, or a stub in tests) provides: cfg, apply_settings(changes) -> (msgs, errs), input_devices(), output_devices(), ui (Mascot or None),
test_speaker(device_name), voices (list), log(*a)."""
import os, queue, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

import numpy as np

import characters
import settings_store
import wake

EMO_RU = characters.EMOTION_RU
RU_EMO = {v: k for k, v in EMO_RU.items()}
STATE_RU = {'working': 'Работает (working)', 'awaiting_clarification': 'Ждет уточнения (awaiting_clarification)',
            'awaiting_confirmation': 'Ждет подтверждения (awaiting_confirmation)', 'completed': 'Готово (completed)', 'failed': 'Ошибка (failed)',
            'idle': 'Покой (idle)', 'listening': 'Слушаю (listening)', 'transcribing': 'Распознаю (transcribing)', 'thinking': 'Загрузка (thinking)',
            'speaking': 'Говорю (speaking)'}
BG = '#dfe3ea'
HOTKEY_ROWS = [('hotkey', 'Говорить (удерживать)'), ('toggle_hotkey', 'Показать / скрыть Клода'), ('settings_hotkey', 'Открыть настройки'), ('stop_hotkey', 'Замолчать сейчас'), ('mute_hotkey', 'Не слушать (вкл / выкл)'), ('meeting_hotkey', 'Запись встречи (вкл / выкл)')]
LOOK_KEYS = ('character', 'mascot_scale', 'mascot_opacity', 'mascot_anim_speed', 'model_scale', 'window_mode')
_KEYSYM = {'space': 'space', 'Return': 'enter', 'Tab': 'tab', 'BackSpace': 'backspace', 'Delete': 'delete', 'Insert': 'insert', 'Home': 'home', 'End': 'end',
           'Prior': 'page up', 'Next': 'page down', 'Left': 'left', 'Right': 'right', 'Up': 'up', 'Down': 'down'}


def combo_from_event(state, keysym, keycode=0):
    """Tk key event -> `keyboard` library hotkey string (ctrl+alt+space), or None while only modifiers are held / key unsupported.
    Letters and digits come from the Windows virtual-key code so a Russian layout works too."""
    if keysym in ('Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R', 'Win_L', 'Win_R', 'Caps_Lock', 'Num_Lock', 'Meta_L', 'Meta_R', 'Super_L', 'Super_R'): return None
    key = _KEYSYM.get(keysym)
    if key is None:
        if keysym.startswith('F') and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24: key = keysym.lower()
        elif 0x41 <= keycode <= 0x5A or 0x30 <= keycode <= 0x39: key = chr(keycode).lower()
        elif len(keysym) == 1 and keysym.isascii() and keysym.isalnum(): key = keysym.lower()
        else: return None
    mods = [m for bit, m in ((0x4, 'ctrl'), (0x20000, 'alt'), (0x1, 'shift')) if state & bit]
    return '+'.join(mods + [key])


class AnimPreview(tk.Label):
    """Animated preview of (character, emotion) on a flat background."""
    def __init__(self, master, w=120, h=108, **kw):
        super().__init__(master, bg=BG, bd=1, relief='sunken', **kw)
        self.w, self.h, self.anim, self.photo, self.t0, self.speed = w, h, None, None, time.time(), 1.0
        self.configure(width=w, height=h); self._job = None; self._last = None
        self.bind('<Destroy>', lambda e: self._job and self.after_cancel(self._job)); self._tick()

    def show(self, char, emotion, fps=None, loop=None, speed=1.0):
        a = characters.load_animation(char, emotion)
        if fps: a.fps = float(fps)
        if loop is not None: a.loop = bool(loop)
        self.anim, self.t0, self.speed, self._last = a, time.time(), speed, None

    def _tick(self):
        from PIL import ImageTk
        a = self.anim
        if a:
            i = a.index((time.time() - self.t0) * self.speed)
            if i != self._last:
                self._last = i
                img = characters.render(a.frames[i], self.w - 4, self.h - 4, a.grid, bg=(0xdf, 0xe3, 0xea))
                self.photo = ImageTk.PhotoImage(img); self.configure(image=self.photo)
        try: self._job = self.after(40, self._tick)
        except tk.TclError: pass


class Meter(tk.Canvas):
    def __init__(self, master, w=300, h=16):
        super().__init__(master, width=w, height=h, bg='#ffffff', highlightthickness=1, highlightbackground='#9aa3b2')
        self.w, self.h = w, h; self.set(0, 0)

    def set(self, rms, peak):
        self.delete('all')
        f = lambda v: min(1.0, (max(v, 1) / 8000.0) ** 0.5)
        col = '#4ade80' if rms < 5000 else '#e5484d'
        self.create_rectangle(0, 0, int(self.w * f(rms)), self.h, fill=col, outline='')
        x = int(self.w * f(peak)); self.create_line(x, 0, x, self.h, fill='#1a1a1a', width=2)


def record_mic(device, secs, on_level, sr=16000):
    """Blocking: record `secs` seconds (int16 mono 16 kHz) from a sounddevice input; on_level(rms, peak) per 100 ms block."""
    import sounddevice as sd
    chunks = []
    with sd.InputStream(samplerate=sr, channels=1, dtype='int16', blocksize=sr // 10, device=device) as st:
        t0 = time.time()
        while time.time() - t0 < secs:
            data, _ = st.read(sr // 10); a = data[:, 0].copy(); chunks.append(a)
            f = a.astype('float32'); on_level(float(np.sqrt(np.mean(f ** 2))), float(np.abs(f).max()))
    return np.concatenate(chunks)


class SettingsWindow:
    TABS = ['Слово активации', 'Персонаж и вид', 'Эмоции и анимации', 'Звук', 'Горячие клавиши', 'Поведение', 'Персонажи и движки']

    def _icon(self):
        try:
            import paths
            self.win.iconbitmap(str(paths.HERE / 'assets' / 'icons' / 'icon.ico'))
        except Exception: pass

    def __init__(self, root, host, tab=None):
        self.host, self.root = host, root; self.cfg = host.cfg
        self.win = tk.Toplevel(root); self.win.title('Клод: настройки'); self._icon(); self.win.geometry('800x800'); self.win.minsize(740, 700)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.v, self.q = {}, queue.Queue(); self.model_ready = False; self.testing = False
        self.pend_meta = {}; self.capture = None; self.snap_look = False
        self.status = tk.StringVar(value='')
        self.chars = []; self.devs_in = []; self.devs_out = []
        nb = self.nb = ttk.Notebook(self.win); nb.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        self.tab_frames = []
        for name, build in zip(self.TABS, (self.build_wake, self.build_look, self.build_emotions, self.build_sound, self.build_keys, self.build_behavior, self.build_engines)):
            f = ttk.Frame(nb, padding=12); nb.add(f, text=name); self.tab_frames.append(f); build(f)
        bar = ttk.Frame(self.win, padding=(8, 6)); bar.pack(fill='x')
        ttk.Label(bar, textvariable=self.status, foreground='#1f6b3b', wraplength=720).pack(fill='x', pady=(0, 4))
        for t, c in (('Предпросмотр', self.preview), ('Применить', self.apply), ('Сбросить', self.reset), ('Открыть папку ресурсов', self.open_folder)):
            ttk.Button(bar, text=t, command=c).pack(side='left', padx=(0, 6))
        ttk.Button(bar, text='Закрыть', command=self.close).pack(side='right')
        self.fill(self.cfg)
        threading.Thread(target=self._load_model, daemon=True).start()
        self.win.after(60, self._poll); self.win.bind('<KeyPress>', self._key, add='+')
        if tab is not None: self.focus(tab)
        self.win.lift(); self.win.focus_force()

    # ------------------------------------------------------------ lifecycle
    def alive(self):
        try: return bool(self.win.winfo_exists())
        except tk.TclError: return False

    def focus(self, tab=None):
        if isinstance(tab, int): self.nb.select(tab)
        elif tab in self.TABS: self.nb.select(self.TABS.index(tab))
        self.win.deiconify(); self.win.lift(); self.win.focus_force()

    def close(self):
        self.set_override(None)
        try: self.win.destroy()
        except tk.TclError: pass

    def log(self, *a):
        try: self.host.log(*a)
        except Exception: pass

    def _load_model(self):
        try: wake.get_model(); self.q.put(('model', True))
        except Exception as e: self.q.put(('model', str(e)))

    def _poll(self):
        if not self.alive(): return
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == 'model': self.model_ready = val is True; self.model_err = None if val is True else val; self.check_wake()
                elif kind == 'level': self.meter.set(*val); self.peak_var.set('RMS %d  пик %d (int16, максимум 32767)' % (val[0], val[1]))
                elif kind == 'wake_result': self.show_wake_result(val)
                elif kind == 'mic_result': self.show_mic_result(val)
                elif kind == 'msg': self.status.set(val)
        except queue.Empty: pass
        self.win.after(60, self._poll)

    # ------------------------------------------------------------ helpers
    def var(self, key, kind='str', default=None):
        v = {'str': tk.StringVar, 'bool': tk.BooleanVar, 'int': tk.IntVar, 'float': tk.DoubleVar}[kind](value=default)
        self.v[key] = v; return v

    def row(self, parent, r, label, widget=None, **kw):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=4, padx=(0, 10))
        if widget is not None: widget.grid(row=r, column=1, sticky=kw.get('sticky', 'w'), pady=4)
        return widget

    def scale(self, parent, key, lo, hi, fmt, res=None, command=None):
        fr = ttk.Frame(parent); var = self.var(key, 'float', lo); lab = ttk.Label(fr, width=8)
        def upd(_=None):
            x = var.get()
            if res: x = round(x / res) * res; var.set(x)
            lab.config(text=fmt % x)
            if command: command()
        s = ttk.Scale(fr, from_=lo, to=hi, variable=var, length=240, command=upd); s.pack(side='left'); lab.pack(side='left', padx=8)
        var.trace_add('write', lambda *a: lab.config(text=fmt % var.get())); return fr

    def spin(self, parent, key, lo, hi, step, kind='float', width=7):
        return ttk.Spinbox(parent, from_=lo, to=hi, increment=step, width=width, textvariable=self.var(key, kind, lo))

    def build_engines(self, f):
        import settings_engines
        self.eng = settings_engines.EnginesTab(self, f)

    # ------------------------------------------------------------ tab: wake word
    def build_wake(self, f):
        f.columnconfigure(1, weight=1)
        ttk.Checkbutton(f, text='Слово активации включено', variable=self.var('wake_word_enabled', 'bool', True)).grid(row=0, column=0, columnspan=2, sticky='w')
        ent = ttk.Entry(f, textvariable=self.var('wake_word', 'str', 'клод'), width=24, font=('Segoe UI', 12)); self.row(f, 1, 'Слово', ent)
        self.v['wake_word'].trace_add('write', lambda *a: self._debounce('wake', self.check_wake))
        fr = ttk.Frame(f); ttk.Entry(fr, textvariable=self.var('wake_extra_variants', 'str', ''), width=34).pack(side='left')
        ttk.Button(fr, text='Подобрать', command=self.suggest_variants).pack(side='left', padx=6); self.row(f, 2, 'Варианты написания', fr)
        self.wake_info = tk.Text(f, height=4, width=70, wrap='word', relief='flat', bg=f.winfo_toplevel().cget('bg'), font=('Segoe UI', 9), state='disabled')
        self.wake_info.tag_configure('error', foreground='#c42b31'); self.wake_info.tag_configure('warn', foreground='#9a6b00'); self.wake_info.tag_configure('ok', foreground='#1f6b3b')
        self.wake_info.grid(row=3, column=0, columnspan=2, sticky='we', pady=(2, 8))
        mf = ttk.Frame(f); mode = self.var('wake_mode', 'str', 'fast')
        for val, t in (('fast', 'Быстро (по частичному результату)'), ('strict', 'Строго (по финальному результату и уверенности)')):
            ttk.Radiobutton(mf, text=t, value=val, variable=mode).pack(anchor='w')
        self.row(f, 4, 'Режим срабатывания', mf)
        self.row(f, 5, 'Мин. уверенность (строго)', self.scale(f, 'wake_min_conf', 0.0, 1.0, '%.2f', 0.05))
        ttk.Checkbutton(f, text='Не слушать, когда микрофон занят звонком Discord', variable=self.var('mute_on_call', 'bool', False)).grid(row=20, column=0, columnspan=2, sticky='w', pady=2)
        ttk.Checkbutton(f, text='Слово прерывает речь ассистента (лучше в наушниках)', variable=self.var('wake_bargein', 'bool', True)).grid(row=6, column=0, columnspan=2, sticky='w', pady=4)
        ttk.Separator(f).grid(row=7, column=0, columnspan=2, sticky='we', pady=8)
        ttk.Label(f, text='Проверка: нажмите кнопку и через полсекунды скажите слово (запись 3 с).').grid(row=8, column=0, columnspan=2, sticky='w')
        tf = ttk.Frame(f); self.test_btn = ttk.Button(tf, text='Проверить', command=self.test_wake); self.test_btn.pack(side='left')
        self.meter = Meter(tf); self.meter.pack(side='left', padx=10); tf.grid(row=9, column=0, columnspan=2, sticky='w', pady=6)
        self.peak_var = tk.StringVar(value='Уровень микрофона появится во время проверки'); ttk.Label(f, textvariable=self.peak_var).grid(row=10, column=0, columnspan=2, sticky='w')
        self.wake_res = tk.Text(f, height=5, width=70, wrap='word', relief='flat', bg=f.winfo_toplevel().cget('bg'), font=('Segoe UI', 10), state='disabled')
        self.wake_res.tag_configure('ok', foreground='#1f6b3b'); self.wake_res.tag_configure('error', foreground='#c42b31'); self.wake_res.tag_configure('warn', foreground='#9a6b00')
        self.wake_res.grid(row=11, column=0, columnspan=2, sticky='we', pady=6)

    def _debounce(self, name, fn, ms=350):
        job = getattr(self, '_job_' + name, None)
        if job: self.win.after_cancel(job)
        setattr(self, '_job_' + name, self.win.after(ms, fn))

    def _text(self, w, lines):
        w.config(state='normal'); w.delete('1.0', 'end')
        for tag, t in lines: w.insert('end', t + '\n', tag)
        w.config(state='disabled')

    def find(self): return wake.model_find if self.model_ready else None

    def check_wake(self):
        word = self.v['wake_word'].get(); find = self.find(); cf = wake.conflicts(word, find)
        lines = [(l, {'error': 'Ошибка: ', 'warn': 'Внимание: '}.get(l, '') + t) for l, t in cf]
        if not wake.clean_word(word): pass
        elif not cf: lines.append(('ok', 'Слово подходит: есть в словаре Vosk, конфликтов нет.' if find else 'Конфликтов не найдено.'))
        if not find: lines.append(('warn', 'Словарь Vosk загружается: проверка наличия слова будет через пару секунд.' if not getattr(self, 'model_err', None) else 'Словарь Vosk недоступен: ' + str(self.model_err)))
        cfg = dict(self.cfg); cfg['wake_word'] = word; cfg['wake_extra_variants'] = self.v['wake_extra_variants'].get()
        cfg['wake_extra_variants'] = settings_store.validate_changes({'wake_extra_variants': cfg['wake_extra_variants']})[0].get('wake_extra_variants', [])
        lines.append(('', 'Распознается как: ' + ', '.join(wake.words(cfg))))
        self._text(self.wake_info, lines)

    def suggest_variants(self):
        find = self.find()
        vs = wake.phonetic_variants(self.v['wake_word'].get(), find)
        self.v['wake_extra_variants'].set(', '.join(vs)); self.check_wake()
        self.status.set('Подобрано %d вариантов%s' % (len(vs), '' if find else ' (словарь Vosk еще не загружен, не отфильтровано)'))

    def form_cfg(self):
        c = dict(self.cfg); ch = self.collect()
        c.update(settings_store.validate_changes(ch)[0]); return c

    def selected_mic(self):
        n = self.v['mic_device'].get(); n = None if n in ('', 'По умолчанию') else n
        for i, name in self.devs_in:
            if name == n: return i
        return None

    def test_wake(self):
        if self.testing: return
        cf = wake.conflicts(self.v['wake_word'].get(), self.find())
        if wake.has_errors(cf):
            self._text(self.wake_res, [('error', 'Сначала исправьте слово: ' + [t for l, t in cf if l == 'error'][0])]); return
        self.testing = True; self.test_btn.config(state='disabled'); self._text(self.wake_res, [('', 'Идет запись 3 секунды, говорите…')])
        cfg = self.form_cfg(); dev = self.selected_mic()
        def run():
            try:
                audio = record_mic(dev, 3.0, lambda r, p: self.q.put(('level', (r, p))))
                res = self.analyze(audio, cfg)
            except Exception as e: res = {'error': str(e)}
            self.q.put(('wake_result', res))
        threading.Thread(target=run, daemon=True).start()

    def analyze(self, audio, cfg):
        """Offline analysis of a recording (also used by tests / screenshots)."""
        f = audio.astype('float32'); rms, peak = float(np.sqrt(np.mean(f ** 2))) if len(f) else 0.0, float(np.abs(f).max()) if len(f) else 0.0
        res = {'rms': rms, 'peak': peak, 'seconds': len(audio) / 16000.0}
        det = wake.Detector(cfg, log=self.log); res.update(det.scan(audio)); res['mode'] = det.mode; res['min_conf'] = det.min_conf; res['wake'] = det.wake
        return res

    def show_wake_result(self, r):
        self.testing = False; self.test_btn.config(state='normal')
        if 'error' in r: self._text(self.wake_res, [('error', 'Ошибка записи: ' + r['error'])]); return
        self.meter.set(r['rms'], r['peak']); self.peak_var.set('RMS %d  пик %d (int16, максимум 32767)' % (r['rms'], r['peak']))
        lines = []
        if r['peak'] < 500: lines.append(('warn', 'Микрофон почти ничего не слышит (пик %d). Проверьте устройство на вкладке «Звук».' % r['peak']))
        elif r['peak'] > 30000: lines.append(('warn', 'Сигнал клипует (пик %d), уменьшите усиление микрофона.' % r['peak']))
        else: lines.append(('ok', 'Уровень нормальный: RMS %d, пик %d.' % (r['rms'], r['peak'])))
        lines.append(('', 'Услышал: «%s»' % (r['text'] or '…ничего')))
        if r['passes']: lines.append(('ok', 'Слово «%s» обнаружено (уверенность %.2f) при текущих настройках.' % (r['wake'][0], r['conf'])))
        elif r['hit']: lines.append(('warn', 'Слово услышано, но уверенность %.2f ниже порога %.2f (режим строго). Снизьте порог или выберите быстрый режим.' % (r['conf'], r['min_conf'])))
        else: lines.append(('error', 'Слово не обнаружено. Скажите его четче и ближе к микрофону, либо подберите другое слово.'))
        self._text(self.wake_res, lines)

    # ------------------------------------------------------------ tab: character and look
    def build_look(self, f):
        f.columnconfigure(1, weight=1)
        top = ttk.Frame(f); top.grid(row=0, column=0, columnspan=2, sticky='we')
        self.char_combo = ttk.Combobox(top, state='readonly', width=28, textvariable=self.var('character_name', 'str', '')); self.char_combo.pack(side='left')
        self.char_combo.bind('<<ComboboxSelected>>', lambda e: self.on_char())
        ttk.Button(top, text='Импорт картинки / GIF…', command=self.import_character).pack(side='left', padx=6)
        ttk.Button(top, text='Удалить', command=self.delete_char).pack(side='left')
        self.look_prev = AnimPreview(f, 180, 164); self.look_prev.grid(row=1, column=0, padx=(0, 16), pady=10, sticky='n')
        rt = ttk.Frame(f); rt.grid(row=1, column=1, sticky='nw', pady=10)
        self.char_hint = ttk.Label(rt, foreground='#555', wraplength=440, justify='left'); self.char_hint.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))
        for r, (lab, key, lo, hi, fmt, res) in enumerate((('Размер (масштаб)', 'mascot_scale', 1, 12, 'x%d', 1), ('Прозрачность', 'mascot_opacity', 0.3, 1.0, '%.2f', 0.05),
                                                           ('Скорость анимации', 'mascot_anim_speed', 0.25, 3.0, 'x%.2f', 0.25)), 1):
            self.row(rt, r, lab, self.scale(rt, key, lo, hi, fmt, res, self.refresh_look))
        pf = ttk.Frame(rt); self.spin(pf, 'mascot_x', -10000, 20000, 10, 'int', 7).pack(side='left'); ttk.Label(pf, text=' x ').pack(side='left')
        self.spin(pf, 'mascot_y', -10000, 20000, 10, 'int', 7).pack(side='left')
        ttk.Button(pf, text='Сбросить позицию', command=self.reset_pos).pack(side='left', padx=8); self.row(rt, 4, 'Позиция (x, y)', pf)
        ttk.Label(rt, text='Всегда поверх окон, перетаскивание мышью и значок в трее остаются. Правый клик по персонажу открывает меню.', foreground='#555', wraplength=440).grid(row=5, column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.refresh_chars()

    def refresh_chars(self, select=None):
        self.chars = characters.list_characters()
        self.char_combo['values'] = [c['name'] + ('' if c['builtin'] else '  [свой]') for c in self.chars]
        cur = select or self.v.get('character_id') or self.cfg.get('character', 'crab')
        ids = [c['id'] for c in self.chars]; cur = cur if cur in ids else 'crab'
        self.char_combo.current(ids.index(cur)); self.on_char()

    def char_id(self):
        i = self.char_combo.current(); return self.chars[i]['id'] if 0 <= i < len(self.chars) else 'crab'

    def on_char(self):
        cid = self.char_id(); c = [x for x in self.chars if x['id'] == cid][0]
        if c.get('engine', 'gif') != 'gif': self.char_hint.config(text='Модель %s: настраивается во вкладке «Персонажи и движки». Здесь показан стандартный запасной вариант.' % c['engine'].upper())
        else: self.char_hint.config(text=('Встроенный пиксельный персонаж. Чтобы заменить анимации эмоций, откройте вкладку «Эмоции»: копия создастся автоматически.' if c['builtin']
                                    else 'Свой персонаж: %s' % characters.find_dir(cid)))
        self.look_prev.show(cid, 'idle', speed=self.v['mascot_anim_speed'].get() or 1.0)
        if hasattr(self, 'emo_list'): self.on_emotion()

    def refresh_look(self): self.look_prev.speed = float(self.v['mascot_anim_speed'].get() or 1.0)

    def reset_pos(self):
        self.v['mascot_x'].set(0); self.v['mascot_y'].set(0)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight(); S = int(self.v['mascot_scale'].get())
        self.v['mascot_x'].set(sw - characters.GW * S - 40); self.v['mascot_y'].set(sh - characters.GH * S - 80)

    def import_character(self):
        p = filedialog.askopenfilename(parent=self.win, title='Картинка или GIF персонажа', filetypes=[('Картинки', '*.gif *.png *.webp *.jpg *.jpeg *.bmp'), ('Все', '*.*')])
        if not p: return
        name = simpledialog.askstring('Имя персонажа', 'Как назвать персонажа?', initialvalue=Path(p).stem, parent=self.win)
        if not name: return
        try: cid = characters.import_character_image(p, name)
        except characters.AssetError as e: messagebox.showerror('Импорт', str(e), parent=self.win); return
        self.refresh_chars(cid); self.status.set('Персонаж «%s» создан. Эту картинку он использует для всех эмоций, пока вы не загрузите свои.' % name)

    def delete_char(self):
        cid = self.char_id(); c = [x for x in self.chars if x['id'] == cid][0]
        if c['builtin']: self.status.set('Встроенных персонажей удалить нельзя.'); return
        if messagebox.askyesno('Удалить', 'Удалить персонажа «%s» вместе с файлами?' % c['name'], parent=self.win):
            characters.delete_character(cid); self.refresh_chars('crab')

    # ------------------------------------------------------------ tab: emotions
    def build_emotions(self, f):
        f.columnconfigure(1, weight=1)
        left = ttk.Frame(f); left.grid(row=0, column=0, sticky='ns', padx=(0, 12))
        self.emo_list = tk.Listbox(left, height=11, width=18, exportselection=False, activestyle='none')
        for e in characters.ALL_EMOTIONS: self.emo_list.insert('end', EMO_RU[e])
        self.emo_list.pack(); self.emo_list.selection_set(0); self.emo_list.bind('<<ListboxSelect>>', lambda e: self.on_emotion())
        right = ttk.Frame(f); right.grid(row=0, column=1, sticky='nwe')
        self.emo_prev = AnimPreview(right, 150, 136); self.emo_prev.grid(row=0, column=0, rowspan=4, padx=(0, 14), sticky='n')
        self.emo_src = ttk.Label(right, text='', wraplength=380, justify='left'); self.emo_src.grid(row=0, column=1, columnspan=3, sticky='w')
        ttk.Label(right, text='Кадров в секунду').grid(row=1, column=1, sticky='w'); sp = self.spin(right, 'emo_fps', 1, 60, 1, 'float', 6); sp.grid(row=1, column=2, sticky='w')
        ttk.Checkbutton(right, text='Зациклить', variable=self.var('emo_loop', 'bool', True)).grid(row=2, column=1, sticky='w')
        ttk.Label(right, text='Длительность, с (0 = до смены)').grid(row=3, column=1, sticky='w'); self.spin(right, 'emo_duration', 0, 60, 0.5, 'float', 6).grid(row=3, column=2, sticky='w')
        for k in ('emo_fps', 'emo_loop', 'emo_duration'): self.v[k].trace_add('write', lambda *a: self._debounce('emo', self.on_meta, 200))
        bf = ttk.Frame(f); bf.grid(row=1, column=0, columnspan=2, sticky='we', pady=8)
        for t, c in (('Загрузить GIF…', lambda: self.import_emo('gif')), ('Папка кадров PNG…', lambda: self.import_emo('frames')), ('Спрайт-лист…', lambda: self.import_emo('sheet')),
                     ('Сбросить на встроенную', self.reset_emo), ('Тест на персонаже', self.test_emo)):
            ttk.Button(bf, text=t, command=c).pack(side='left', padx=(0, 6))
        ttk.Separator(f).grid(row=2, column=0, columnspan=2, sticky='we', pady=6)
        ttk.Label(f, text='Какое состояние ассистента показывать какой эмоцией').grid(row=3, column=0, columnspan=2, sticky='w')
        mf = ttk.Frame(f); mf.grid(row=4, column=0, columnspan=2, sticky='we', pady=4)
        for i, s in enumerate(STATE_RU):
            r, c = i, 0
            ttk.Label(mf, text=STATE_RU[s], width=44).grid(row=r, column=c * 2, sticky='w', pady=1)
            cb = ttk.Combobox(mf, state='readonly', width=14, values=[EMO_RU[e] for e in characters.ALL_EMOTIONS], textvariable=self.var('map_' + s, 'str', ''))
            cb.grid(row=r, column=c * 2 + 1, padx=(0, 10), pady=1)
        ttk.Label(f, text='Во сне персонаж оказывается после N минут покоя (настраивается на вкладке «Поведение»).', foreground='#555').grid(row=5, column=0, columnspan=2, sticky='w')
        self._emo_loading = False

    def emo(self):
        sel = self.emo_list.curselection(); return characters.ALL_EMOTIONS[sel[0] if sel else 0]

    def on_emotion(self):
        cid = self.char_id(); e = self.emo(); self._emo_loading = True
        m = dict(characters.emotion_meta(cid, e)); m.update(self.pend_meta.get((cid, e), {}))
        a = characters.load_animation(cid, e)
        if (cid, e) not in self.pend_meta: m['fps'] = a.fps if a.source != 'builtin' else m['fps']
        self.v['emo_fps'].set(float(m['fps'])); self.v['emo_loop'].set(bool(m['loop'])); self.v['emo_duration'].set(float(m['duration']))
        self._emo_loading = False
        src = characters.emotion_source(cid, e)
        fallback = '' if src != 'builtin' or cid in characters.BUILTIN else ' (для своих персонажей без файла берется запасной вариант)'
        self.emo_src.config(text='Источник: ' + {'builtin': 'встроенная анимация' + fallback, 'gif': 'GIF', 'frames': 'кадры PNG', 'sheet': 'спрайт-лист'}[src] + '; кадров: %d' % len(a.frames))
        self.emo_prev.show(cid, e, m['fps'], m['loop'], self.v['mascot_anim_speed'].get() or 1.0)

    def on_meta(self):
        if self._emo_loading: return
        try: m = {'fps': float(self.v['emo_fps'].get()), 'loop': bool(self.v['emo_loop'].get()), 'duration': float(self.v['emo_duration'].get())}
        except (tk.TclError, ValueError): return
        cid = self.char_id(); self.pend_meta[(cid, self.emo())] = m
        a = self.emo_prev.anim
        if a: a.fps, a.loop = max(1.0, m['fps']), m['loop']; self.emo_prev.t0 = time.time()

    def ensure_editable(self):
        cid = self.char_id(); d = characters.find_dir(cid)
        if cid in characters.BUILTIN or d is None or not str(d).startswith(str(characters.user_root())):
            new = characters.editable_copy(cid); self.refresh_chars(new)
            self.status.set('Создана копия «%s» для редактирования (встроенных персонажей менять нельзя).' % new); return new
        return cid

    def import_emo(self, kind):
        e = self.emo()
        if kind == 'gif': p = filedialog.askopenfilename(parent=self.win, title='GIF для «%s»' % EMO_RU[e], filetypes=[('GIF', '*.gif')])
        elif kind == 'frames': p = filedialog.askdirectory(parent=self.win, title='Папка с кадрами PNG (по порядку имен)')
        else: p = filedialog.askopenfilename(parent=self.win, title='Спрайт-лист', filetypes=[('Картинки', '*.png *.webp *.gif *.bmp')])
        if not p: return
        fw = fh = None
        if kind == 'sheet':
            from PIL import Image
            try:
                with Image.open(p) as im: gw, gh = characters.guess_cell(im); size = im.size
            except Exception as ex: messagebox.showerror('Импорт', 'Файл не читается: %s' % ex, parent=self.win); return
            fw = simpledialog.askinteger('Спрайт-лист %dx%d' % size, 'Ширина кадра, px:', initialvalue=gw, minvalue=1, parent=self.win)
            fh = fw and simpledialog.askinteger('Спрайт-лист %dx%d' % size, 'Высота кадра, px:', initialvalue=gh, minvalue=1, parent=self.win)
            if not fw or not fh: return
        cid = self.ensure_editable()
        try: characters.import_emotion(cid, e, p, kind=kind, frame_w=fw, frame_h=fh); self.status.set('Анимация «%s» загружена.' % EMO_RU[e])
        except characters.AssetError as ex: messagebox.showerror('Импорт', str(ex), parent=self.win); return
        self.pend_meta.pop((cid, e), None); self.on_emotion()

    def reset_emo(self):
        cid = self.char_id(); e = self.emo()
        if characters.emotion_source(cid, e) == 'builtin' and cid in characters.BUILTIN: self.status.set('Уже встроенная анимация.'); return
        characters.reset_emotion(cid, e); self.pend_meta.pop((cid, e), None); self.status.set('«%s»: возвращена встроенная анимация.' % EMO_RU[e]); self.on_emotion()

    def test_emo(self):
        self.preview(); ui = getattr(self.host, 'ui', None)
        if ui: ui.preview(self.emo(), 5.0)

    # ------------------------------------------------------------ tab: sound
    def build_sound(self, f):
        f.columnconfigure(1, weight=1)
        try: self.devs_in = self.host.input_devices(); self.devs_out = self.host.output_devices()
        except Exception as ex: self.devs_in, self.devs_out = [], []; self.log('devices error', ex)
        self.row(f, 0, 'Микрофон', ttk.Combobox(f, state='readonly', width=52, values=['По умолчанию'] + [n for _, n in self.devs_in], textvariable=self.var('mic_device', 'str', 'По умолчанию')))
        self.row(f, 1, 'Динамик', ttk.Combobox(f, state='readonly', width=52, values=['По умолчанию'] + [n for _, n in self.devs_out], textvariable=self.var('speaker_device', 'str', 'По умолчанию')))
        bf = ttk.Frame(f); self.mic_btn = ttk.Button(bf, text='Тест микрофона (3 с)', command=self.test_mic); self.mic_btn.pack(side='left')
        ttk.Button(bf, text='Тест динамика', command=self.test_speaker).pack(side='left', padx=6); self.row(f, 2, '', bf)
        self.mic_res = ttk.Label(f, text='', wraplength=560, justify='left'); self.mic_res.grid(row=3, column=0, columnspan=2, sticky='w', pady=6)
        voices = list(getattr(self.host, 'voices', []) or characters_voices())
        self.row(f, 4, 'Голос', ttk.Combobox(f, state='readonly', width=30, values=voices, textvariable=self.var('tts_speaker', 'str', 'xenia')))
        self.row(f, 5, 'Скорость речи', self.scale(f, 'tts_rate', 0.5, 2.0, 'x%.2f', 0.05))
        self.mic_meter_note = ttk.Label(f, text='Уровень микрофона во время теста показывает шкала на вкладке «Слово активации».', foreground='#555'); self.mic_meter_note.grid(row=6, column=0, columnspan=2, sticky='w')

    def test_mic(self):
        if self.testing: return
        self.testing = True; self.mic_btn.config(state='disabled'); self.mic_res.config(text='Запись 3 секунды, говорите…'); dev = self.selected_mic()
        def run():
            try:
                a = record_mic(dev, 3.0, lambda r, p: self.q.put(('level', (r, p)))); f = a.astype('float32')
                res = {'rms': float(np.sqrt(np.mean(f ** 2))), 'peak': float(np.abs(f).max())}
            except Exception as e: res = {'error': str(e)}
            self.q.put(('mic_result', res))
        threading.Thread(target=run, daemon=True).start()

    def show_mic_result(self, r):
        self.testing = False; self.mic_btn.config(state='normal')
        if 'error' in r: self.mic_res.config(text='Ошибка: ' + r['error']); return
        self.meter.set(r['rms'], r['peak'])
        verdict = 'почти тишина, проверьте устройство' if r['peak'] < 500 else 'слишком громко (клиппинг)' if r['peak'] > 30000 else 'уровень нормальный'
        self.mic_res.config(text='RMS %d, пик %d: %s.' % (r['rms'], r['peak'], verdict))

    def test_speaker(self):
        n = self.v['speaker_device'].get(); n = None if n in ('', 'По умолчанию') else n
        try: self.host.test_speaker(n); self.status.set('Играет тестовая фраза.')
        except Exception as e: self.status.set('Динамик: ' + str(e))

    # ------------------------------------------------------------ tab: hotkeys
    def build_keys(self, f):
        f.columnconfigure(1, weight=1)
        self.key_btns = {}
        for r, (key, label) in enumerate(HOTKEY_ROWS):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', pady=8, padx=(0, 12))
            ttk.Entry(f, textvariable=self.var(key, 'str', ''), width=26, state='readonly', font=('Consolas', 11)).grid(row=r, column=1, sticky='w')
            b = ttk.Button(f, text='Записать', command=lambda k=key: self.start_capture(k)); b.grid(row=r, column=2, padx=8); self.key_btns[key] = b
            ttk.Button(f, text='Очистить', command=lambda k=key: self.v[k].set('')).grid(row=r, column=3)
        ttk.Label(f, text='Нажмите «Записать», затем комбинацию. Esc отменяет. Комбинации вида ctrl+alt+space; пустое значение отключает хоткей (кроме «Говорить»).',
                  foreground='#555', wraplength=600).grid(row=4, column=0, columnspan=4, sticky='w', pady=12)

    def start_capture(self, key):
        self.stop_capture(); self.capture = key; self.key_btns[key].config(text='Нажмите клавиши…'); self.win.focus_set(); self.status.set('Ждем комбинацию для «%s»…' % dict(HOTKEY_ROWS)[key])

    def stop_capture(self):
        if self.capture: self.key_btns[self.capture].config(text='Записать')
        self.capture = None

    def _key(self, e):
        if not self.capture: return
        if e.keysym == 'Escape': self.stop_capture(); self.status.set('Отменено.'); return 'break'
        combo = combo_from_event(e.state, e.keysym, e.keycode)
        if combo is None: return 'break'
        if not settings_store.valid_hotkey(combo): self.status.set('Нужна хотя бы одна клавиша кроме модификаторов.'); return 'break'
        self.v[self.capture].set(combo); self.stop_capture(); self.status.set('Записано: ' + combo); return 'break'

    # ------------------------------------------------------------ tab: behaviour
    def build_behavior(self, f):
        f.columnconfigure(1, weight=1)
        cb = lambda key, vals, default, w=18: ttk.Combobox(f, state='readonly', width=w, values=vals, textvariable=self.var(key, 'str', default))
        self.row(f, 0, 'Озвучивание (speak_mode)', cb('speak_mode', ['immediate', 'on_request'], 'immediate'))
        self.row(f, 1, 'Подтверждение задачи (confirm_mode)', cb('confirm_mode', ['unclear', 'always', 'never'], 'unclear'))
        ttk.Checkbutton(f, text='Спрашивать «что-то еще?» после выполнения (ask_more_after_done)', variable=self.var('ask_more_after_done', 'bool', True)).grid(row=2, column=0, columnspan=2, sticky='w', pady=3)
        ttk.Checkbutton(f, text='Простые вопросы отвечать сразу (trivial_direct)', variable=self.var('trivial_direct', 'bool', True)).grid(row=3, column=0, columnspan=2, sticky='w', pady=3)
        self.row(f, 4, 'Модель новой сессии', ttk.Combobox(f, width=18, values=['sonnet', 'opus', 'haiku'], textvariable=self.var('new_session_model', 'str', 'sonnet')))
        self.row(f, 5, 'Ждать ответа без слова, с (followup_timeout_sec)', self.spin(f, 'followup_timeout_sec', 1, 60, 1))
        self.row(f, 6, 'Пауза, завершающая фразу, с (silence_sec)', self.spin(f, 'silence_sec', 0.3, 5, 0.1))
        self.row(f, 7, 'Скрывать облачко через, с (bubble_hide_sec)', self.spin(f, 'bubble_hide_sec', 1, 60, 1))
        self.row(f, 8, 'Засыпать после покоя, мин (0 = никогда)', self.spin(f, 'sleep_after_min', 0, 600, 1))
        pf = ttk.Frame(f); ttk.Entry(pf, textvariable=self.var('default_project_dir', 'str', ''), width=42).pack(side='left')
        ttk.Button(pf, text='Обзор…', command=lambda: self.v['default_project_dir'].set(filedialog.askdirectory(parent=self.win) or self.v['default_project_dir'].get())).pack(side='left', padx=6)
        self.row(f, 9, 'Папка проектов по умолчанию', pf)

    # ------------------------------------------------------------ form <-> config
    def fill(self, cfg):
        g = lambda k, d=None: cfg.get(k, d)
        for k in ('wake_word_enabled', 'wake_bargein', 'ask_more_after_done', 'trivial_direct'): self.v[k].set(bool(g(k, True)))
        self.v['mute_on_call'].set(bool(g('mute_on_call', False)))
        self.v['wake_word'].set(g('wake_word', 'клод')); self.v['wake_extra_variants'].set(', '.join(g('wake_extra_variants', []) or []))
        self.v['wake_mode'].set(g('wake_mode', 'fast')); self.v['wake_min_conf'].set(float(g('wake_min_conf', 0.5)))
        for k, d in (('mascot_scale', 5), ('mascot_opacity', 1.0), ('mascot_anim_speed', 1.0), ('tts_rate', 1.0)): self.v[k].set(float(g(k, d)))
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.v['mascot_x'].set(int(g('mascot_x') if g('mascot_x') is not None else sw - characters.GW * 5 - 40))
        self.v['mascot_y'].set(int(g('mascot_y') if g('mascot_y') is not None else sh - characters.GH * 5 - 80))
        self.v['mic_device'].set(g('mic_device') or 'По умолчанию'); self.v['speaker_device'].set(g('speaker_device') or 'По умолчанию')
        self.v['tts_speaker'].set(g('tts_speaker', 'xenia'))
        for key, _ in HOTKEY_ROWS: self.v[key].set(g(key) or '')
        for k in ('speak_mode', 'confirm_mode', 'new_session_model', 'default_project_dir'): self.v[k].set(str(g(k, '')))
        for k in ('followup_timeout_sec', 'silence_sec', 'bubble_hide_sec', 'sleep_after_min'): self.v[k].set(float(g(k, 0)))
        emap = g('emotion_map', {}) or {}
        for s in STATE_RU: self.v['map_' + s].set(EMO_RU[characters.resolve_emotion(s, emap)])
        self.refresh_chars(g('character', 'crab')); self.check_wake(); self.eng.fill(cfg)

    def collect(self):
        v = lambda k: self.v[k].get()
        ch = {k: v(k) for k in ('wake_word_enabled', 'wake_bargein', 'mute_on_call', 'ask_more_after_done', 'trivial_direct', 'wake_word', 'wake_mode', 'speak_mode', 'confirm_mode',
                                'new_session_model', 'default_project_dir', 'tts_speaker')}
        ch['wake_extra_variants'] = v('wake_extra_variants'); ch['wake_min_conf'] = v('wake_min_conf')
        ch['character'] = self.char_id(); ch.update(self.eng.collect())
        for k in ('mascot_scale', 'mascot_opacity', 'mascot_anim_speed', 'tts_rate', 'mascot_x', 'mascot_y', 'followup_timeout_sec', 'silence_sec', 'bubble_hide_sec', 'sleep_after_min'):
            try: ch[k] = v(k)
            except tk.TclError: pass
        ch['mic_device'] = None if v('mic_device') in ('', 'По умолчанию') else v('mic_device')
        ch['speaker_device'] = None if v('speaker_device') in ('', 'По умолчанию') else v('speaker_device')
        for key, _ in HOTKEY_ROWS: ch[key] = v(key)
        ch['emotion_map'] = {s: RU_EMO.get(v('map_' + s)) for s in STATE_RU if RU_EMO.get(v('map_' + s)) and RU_EMO[v('map_' + s)] != characters.STATE_TO_EMOTION[s]}
        return ch

    def set_override(self, d):
        ui = getattr(self.host, 'ui', None)
        if ui is not None and hasattr(ui, 'set_look_override'): ui.set_look_override(d)

    # ------------------------------------------------------------ buttons
    def preview(self):
        ch = self.collect(); look = {k: ch[k] for k in LOOK_KEYS if k in ch}
        clean, _ = settings_store.validate_changes(look); self.set_override(clean)
        self._flush_meta_preview(); ui = getattr(self.host, 'ui', None)
        if ui is not None: ui.preview(self.emo(), 4.0)
        self.status.set('Предпросмотр на рабочем столе (не сохранено, нажмите «Применить»).')

    def _flush_meta_preview(self): pass

    def apply(self):
        ch = self.collect()
        if ch.get('wake_word') is not None and wake.has_errors(wake.conflicts(ch['wake_word'], self.find())) and self.model_ready:
            self.status.set('Слово активации не подходит, исправьте его (вкладка «Слово активации»).'); self.nb.select(0); return
        for (cid, e), m in list(self.pend_meta.items()):
            try:
                if characters.find_dir(cid) and str(characters.find_dir(cid)).startswith(str(characters.user_root())): characters.set_emotion_meta(cid, e, **m)
            except characters.AssetError as ex: self.log('meta', ex)
        self.pend_meta.clear(); self.set_override(None)
        msgs, errs = self.host.apply_settings(ch)
        if errs: self.status.set('Не применено: ' + '; '.join('%s: %s' % kv for kv in errs.items()) + ('' if not msgs else '. Применено: ' + '; '.join(msgs)))
        else: self.status.set('Применено и сохранено.' + (' ' + '; '.join(msgs) if msgs else ''))

    def reset(self):
        if not messagebox.askyesno('Сбросить', 'Вернуть все настройки формы к значениям по умолчанию? (нажмите «Применить», чтобы сохранить)', parent=self.win): return
        self.fill(self.host.defaults()); self.status.set('Значения по умолчанию подставлены, нажмите «Применить».')

    def open_folder(self):
        d = characters.user_root(); d.mkdir(parents=True, exist_ok=True)
        try: os.startfile(str(d))
        except Exception as e: self.status.set('Не удалось открыть папку: %s' % e)


def characters_voices(): return ['aidar', 'baya', 'kseniya', 'xenia', 'eugene', 'ru-RU-SvetlanaNeural', 'ru-RU-DmitryNeural']
