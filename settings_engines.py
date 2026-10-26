"""Settings tab "Персонажи и движки": engine choice, model import with validation, list, license / capability info,
emotion preview on the real mascot, performance and window-mode options, Cubism Core path, return to the standard character.
Built on top of SettingsWindow (sw): shares its variables (sw.v), status line and the character combobox (the single source of truth for `character`)."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import characters
import engines
from engines import library, model_check, profiles
from engines.model_check import ModelError

ENGINE_VIEWS = [('gif', 'Стандартный (GIF/PNG)'), ('live2d', 'Live2D (Cubism 3-5)'), ('vrm', 'VRM (3D)')]
WINDOW_MODES = {'normal': 'Обычное плавающее окно', 'pet': 'Desktop pet (на кромке панели задач)'}
COMPAT = {'normal': 'Обычные окна', 'borderless_game': 'Безрамочные игры (поверх каждые 2 с)'}
FULLSCREEN = {'exclusive': 'Скрывать в эксклюзивном полноэкранном режиме', 'any': 'Скрывать в любом полноэкранном приложении', 'never': 'Не скрывать'}
STATE_BUTTONS = [('idle', 'Покой'), ('listening', 'Слушает'), ('recognizing', 'Распознает'), ('thinking', 'Думает'), ('working', 'Работает'),
                 ('speaking', 'Говорит'), ('success', 'Успех'), ('error', 'Ошибка'), ('clarification', 'Уточнение'), ('sleeping', 'Сон')]
NOTE = ('Работает поверх обычных окон и безрамочных игр. Эксклюзивный полноэкранный режим недоступен для любых обычных окон: персонаж там скрывается. '
        'Без инъекций в процессы и обхода античита.')
CORE_NOTE = ('Cubism Core принадлежит Live2D Inc. (проприетарная лицензия): приложение его не содержит и не скачивает. Скачайте Cubism SDK for Web на live2d.com, '
             'примите лицензию и укажите файл.')


def invert(d): return {v: k for k, v in d.items()}


class EnginesTab:
    def __init__(self, sw, f):
        self.sw, self.f, self.items = sw, f, []
        f.columnconfigure(0, weight=1)
        self.view = tk.StringVar(value='gif')
        top = ttk.Frame(f); top.grid(row=0, column=0, sticky='we')
        ttk.Label(top, text='Движок:').pack(side='left')
        for key, label in ENGINE_VIEWS:
            ttk.Radiobutton(top, text=label, value=key, variable=self.view, command=self.refresh).pack(side='left', padx=8)
        self.avail = ttk.Label(f, wraplength=730, justify='left'); self.avail.grid(row=1, column=0, sticky='we', pady=(2, 4))

        mid = ttk.Frame(f); mid.grid(row=2, column=0, sticky='nsew'); mid.columnconfigure(1, weight=1)
        self.lb = tk.Listbox(mid, height=6, width=28, exportselection=False); self.lb.grid(row=0, column=0, sticky='ns')
        self.lb.bind('<<ListboxSelect>>', lambda e: self.show_info())
        self.info = tk.Text(mid, height=8, width=70, wrap='word', state='disabled', bg='#f4f5f8', relief='sunken', bd=1)
        self.info.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
        sb = ttk.Scrollbar(mid, command=self.info.yview); sb.grid(row=0, column=2, sticky='ns'); self.info.config(yscrollcommand=sb.set)

        bt = ttk.Frame(f); bt.grid(row=3, column=0, sticky='we', pady=6)
        for text, cmd in (('Импорт модели…', self.import_file), ('Live2D из папки…', self.import_folder), ('Удалить', self.remove),
                          ('Показать на столе', self.use), ('К стандартному персонажу', self.reset_standard)):
            ttk.Button(bt, text=text, command=cmd).pack(side='left', padx=(0, 6))

        core = ttk.LabelFrame(f, text='Live2D Cubism Core (путь к вашему файлу)', padding=6); core.grid(row=4, column=0, sticky='we')
        core.columnconfigure(0, weight=1)
        ttk.Entry(core, textvariable=sw.var('live2d_core_path', 'str', '')).grid(row=0, column=0, sticky='we')
        ttk.Button(core, text='Обзор…', command=self.pick_core).grid(row=0, column=1, padx=4)
        ttk.Button(core, text='Проверить', command=self.check_core).grid(row=0, column=2)
        self.core_state = ttk.Label(core, wraplength=730, justify='left'); self.core_state.grid(row=1, column=0, columnspan=3, sticky='w')
        ttk.Label(core, text=CORE_NOTE, foreground='#555', wraplength=730, justify='left').grid(row=2, column=0, columnspan=3, sticky='w')

        emo = ttk.LabelFrame(f, text='Предпросмотр эмоций (на персонаже рабочего стола)', padding=6); emo.grid(row=5, column=0, sticky='we', pady=6)
        for i, (state, label) in enumerate(STATE_BUTTONS):
            ttk.Button(emo, text=label, width=12, command=lambda s=state: self.preview_state(s)).grid(row=i // 5, column=i % 5, padx=2, pady=2)

        perf = ttk.LabelFrame(f, text='Производительность и окно', padding=6); perf.grid(row=6, column=0, sticky='we')
        ttk.Checkbutton(perf, text='Анимации включены (выкл. = статичная поза, минимум нагрузки)', variable=sw.var('anim_enabled', 'bool', True)).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(perf, text='Частота кадров модели').grid(row=1, column=0, sticky='w'); ttk.Spinbox(perf, from_=10, to=60, increment=5, width=5, textvariable=sw.var('render_fps', 'int', 30)).grid(row=1, column=1, sticky='w')
        ttk.Label(perf, text='Размер модели, %').grid(row=1, column=2, sticky='e', padx=(20, 4)); ttk.Spinbox(perf, from_=40, to=200, increment=10, width=5, textvariable=sw.var('model_scale', 'int', 100)).grid(row=1, column=3, sticky='w')
        self.combos = {}
        for r, (key, table, label) in enumerate((('window_mode', WINDOW_MODES, 'Режим окна'), ('compat_mode', COMPAT, 'Совместимость'), ('fullscreen_policy', FULLSCREEN, 'Полный экран')), 2):
            ttk.Label(perf, text=label).grid(row=r, column=0, sticky='w')
            c = ttk.Combobox(perf, state='readonly', width=48, values=list(table.values()), textvariable=sw.var(key + '_ru', 'str', '')); c.grid(row=r, column=1, columnspan=3, sticky='w', pady=2)
            self.combos[key] = table
        ttk.Checkbutton(perf, text='Desktop pet: гулять по панели задач, пока покой', variable=sw.var('pet_patrol', 'bool', False)).grid(row=5, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(perf, text='Клики сквозь прозрачные места', variable=sw.var('click_through', 'bool', True)).grid(row=5, column=2, columnspan=2, sticky='w')
        ttk.Checkbutton(perf, text='Держать движок прогретым (быстрый старт, около 100 МБ памяти)', variable=sw.var('prewarm_engine', 'bool', True)).grid(row=7, column=0, columnspan=4, sticky='w')
        ttk.Label(perf, text=NOTE, foreground='#555', wraplength=730, justify='left').grid(row=6, column=0, columnspan=4, sticky='w', pady=(4, 0))
        self.refresh()

    # ---------------------------------------------------------------- form <-> config
    def fill(self, cfg):
        sw = self.sw
        sw.v['anim_enabled'].set(cfg.get('anim_enabled', True) is not False)
        sw.v['render_fps'].set(int(cfg.get('render_fps', 30) or 30)); sw.v['model_scale'].set(int(cfg.get('model_scale', 100) or 100))
        sw.v['pet_patrol'].set(bool(cfg.get('pet_patrol'))); sw.v['click_through'].set(cfg.get('click_through', True) is not False); sw.v['prewarm_engine'].set(cfg.get('prewarm_engine', True) is not False); sw.v['live2d_core_path'].set(cfg.get('live2d_core_path') or '')
        for key, table in self.combos.items(): sw.v[key + '_ru'].set(table.get(cfg.get(key), list(table.values())[0]))
        self.check_core(quiet=True); self.refresh()

    def collect(self):
        sw = self.sw; ch = {}
        for key, table in self.combos.items(): ch[key] = invert(table).get(sw.v[key + '_ru'].get(), list(table)[0])
        for k in ('anim_enabled', 'render_fps', 'model_scale', 'pet_patrol', 'live2d_core_path', 'click_through', 'prewarm_engine'):
            try: ch[k] = sw.v[k].get()
            except tk.TclError: pass
        return ch

    # ---------------------------------------------------------------- list / info
    def current_cfg(self):
        cfg = dict(self.sw.cfg); cfg['live2d_core_path'] = self.sw.v['live2d_core_path'].get() or None; return cfg

    def refresh(self, select=None):
        eng = self.view.get(); ok, why = engines.availability(self.current_cfg())[eng]
        self.avail.config(text=('Движок доступен.' if ok else 'Движок недоступен: ' + why), foreground='#1f6b3b' if ok else '#a33')
        self.items = [c for c in characters.list_characters() if c.get('engine', 'gif') == eng]
        self.lb.delete(0, 'end')
        for c in self.items: self.lb.insert('end', c['name'] + ('' if c['builtin'] else '  [свой]'))
        cur = select or self.sw.cfg.get('character', 'crab')
        ids = [c['id'] for c in self.items]
        if cur in ids: self.lb.selection_set(ids.index(cur))
        elif self.items: self.lb.selection_set(0)
        self.show_info()

    def selected(self):
        s = self.lb.curselection(); return self.items[s[0]] if s and s[0] < len(self.items) else None

    def set_info(self, lines):
        self.info.config(state='normal'); self.info.delete('1.0', 'end'); self.info.insert('end', '\n'.join(lines)); self.info.config(state='disabled')

    def show_info(self):
        c = self.selected()
        if c is None:
            self.set_info(['Моделей этого движка пока нет. Нажмите «Импорт модели…».' if self.view.get() != 'gif' else 'Нет персонажей.']); return
        if c.get('engine', 'gif') == 'gif':
            self.set_info([c['name'], 'Стандартный персонаж: GIF / PNG / спрайт-лист или рисованная тема. Работает без дополнительных движков.',
                           'Эмоции настраиваются во вкладке «Эмоции и анимации».']); return
        meta = characters.char_meta(c['id']); inf = meta.get('info') or {}
        lines = [c['name'], '%s, %s' % (c['engine'].upper(), inf.get('version', '')), '']
        lines += profiles.describe({'has_blink': inf.get('has_blink'), 'has_mouth': inf.get('has_mouth'), 'expressions': inf.get('expressions'), 'motions': inf.get('motions')})
        lines += ['', 'ЛИЦЕНЗИЯ / УСЛОВИЯ МОДЕЛИ (из метаданных):'] + ['  ' + x for x in inf.get('license_lines', [])]
        if inf.get('warnings'): lines += ['', 'Замечания:'] + ['  ' + x for x in inf['warnings']]
        self.set_info(lines)

    # ---------------------------------------------------------------- actions
    def import_file(self):
        eng = self.view.get()
        types = {'live2d': [('Live2D model3', '*.model3.json')], 'vrm': [('VRM', '*.vrm *.glb')], 'gif': [('Модель', '*.vrm *.glb *.model3.json')]}[eng]
        p = filedialog.askopenfilename(parent=self.sw.win, title='Файл модели', filetypes=types + [('Все', '*.*')])
        if p: self.do_import(p)

    def import_folder(self):
        p = filedialog.askdirectory(parent=self.sw.win, title='Папка модели Live2D')
        if p: self.do_import(p)

    def do_import(self, path):
        sw = self.sw
        eng = model_check.guess_engine(path)
        if eng is None:
            messagebox.showerror('Импорт', 'Не удалось определить формат. Поддерживаются: Live2D (*.model3.json или папка модели) и VRM (*.vrm).', parent=sw.win); return
        try: info = model_check.inspect(eng, path)
        except ModelError as e:
            sw.status.set('Импорт не выполнен: %s' % e); messagebox.showerror('Импорт модели', str(e), parent=sw.win); return
        name = simpledialog.askstring('Имя персонажа', 'Как назвать персонажа?', initialvalue=info['title'], parent=sw.win)
        if not name: return
        sw.status.set('Копирую модель…'); sw.win.update_idletasks()
        try: cid, info = library.import_model(eng, path, name)
        except ModelError as e:
            sw.status.set('Импорт не выполнен: %s' % e); messagebox.showerror('Импорт модели', str(e), parent=sw.win); return
        self.view.set(eng); sw.refresh_chars(cid); self.refresh(select=cid)
        msg = 'Модель «%s» импортирована (%s).' % (name, info['version'])
        if eng == 'live2d' and not engines.check_core(self.sw.v['live2d_core_path'].get())[0]: msg += ' Укажите путь к Cubism Core, иначе модель не запустится.'
        sw.status.set(msg + ' Лицензия показана справа: проверьте условия автора.')

    def remove(self):
        c = self.selected(); sw = self.sw
        if c is None or c['builtin']: sw.status.set('Встроенных персонажей удалить нельзя.'); return
        if messagebox.askyesno('Удалить', 'Удалить «%s» вместе с файлами?' % c['name'], parent=sw.win):
            characters.delete_character(c['id']); sw.refresh_chars('crab' if sw.char_id() == c['id'] else sw.char_id()); self.refresh()

    def use(self):
        c = self.selected()
        if c is None: return False
        self.sw.refresh_chars(c['id']); self.sw.preview()
        ok = engines.availability(self.current_cfg())[c.get('engine', 'gif')]
        self.sw.status.set('Предпросмотр на рабочем столе (не сохранено, нажмите «Применить»).' + ('' if ok[0] else ' Движок недоступен, показан запасной стандартный персонаж: ' + ok[1]))
        return True

    def preview_state(self, state):
        if not self.use(): return
        ui = getattr(self.sw.host, 'ui', None)
        if ui is not None: ui.preview(profiles.STATE_ALIASES.get(state, state), 6.0)

    def reset_standard(self):
        self.view.set('gif'); self.sw.refresh_chars('crab'); self.refresh(select='crab'); self.sw.apply()
        self.sw.status.set('Возврат к стандартному персонажу выполнен и сохранён.')

    def pick_core(self):
        p = filedialog.askopenfilename(parent=self.sw.win, title='live2dcubismcore.min.js', filetypes=[('JavaScript', '*.js'), ('Все', '*.*')])
        if p: self.sw.v['live2d_core_path'].set(p); self.check_core()

    def check_core(self, quiet=False):
        ok, why = engines.check_core(self.sw.v['live2d_core_path'].get())
        self.core_state.config(text='Cubism Core найден.' if ok else why, foreground='#1f6b3b' if ok else '#a33')
        if not quiet: self.refresh()
