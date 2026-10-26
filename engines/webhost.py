"""Parent side of the renderer process (web_child.py): spawn, JSON-lines protocol, health, warm start. No Tk here, so it is testable headless
(tests start a fake child through `argv`).

Warm start: WebView2 needs several seconds to start its browser process. `warm()` starts the child in the background with a hidden window and no model;
`start()` then only re-points the loopback server, moves the window and reloads the page. `stop(keep_warm=True)` replaces the used process with a fresh warm one."""
import json, os, subprocess, sys, threading, time
from pathlib import Path

CHILD = Path(__file__).resolve().parent / 'web_child.py'
START_TIMEOUT = 30.0
PAGE_TIMEOUT = 8.0          # the page normally appears within ~2 s


def child_python():
    exe = sys.executable
    if exe.lower().endswith('pythonw.exe'):                      # a windowless parent still needs real stdio pipes in the child
        alt = Path(exe).with_name('python.exe')
        if alt.exists(): return str(alt)
    return exe


class WebHost:
    """status: idle | warming | warm | starting -> loaded | failed. `error` holds a user-readable reason when failed."""
    def __init__(self, on_event=None, log=None, argv=None):
        self.on_event = on_event or (lambda e: None); self.log = log or (lambda *a: None)
        self.argv = argv; self.proc = None; self.status = 'idle'; self.error = ''; self.info = {}
        self.init_msg = None; self.t_start = 0.0; self.lock = threading.Lock(); self.page_ready = False
        self.t_spawn = 0.0; self.spawn_args = None; self.retried = False

    # ---- process
    def alive(self): return self.proc is not None and self.proc.poll() is None

    def _spawn(self, x, y, w, h, model_dir, core_path, topmost, click_through):
        head = [sys.executable, '--web-child'] if getattr(sys, 'frozen', False) else [child_python(), str(CHILD)]
        argv = self.argv or head + ['--x', str(x), '--y', str(y), '--w', str(w), '--h', str(h), '--model-dir', model_dir or '']
        if not self.argv:
            if core_path: argv += ['--core', core_path]
            if not topmost: argv.append('--no-topmost')
            if click_through: argv.append('--click-through')
        flags = 0x08000000 if sys.platform == 'win32' else 0       # CREATE_NO_WINDOW
        self.page_ready = False; self.t_spawn = time.time(); self.spawn_args = (x, y, w, h, model_dir, core_path, topmost, click_through)
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags,
                                         text=True, encoding='utf8', bufsize=1, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        except OSError as e:
            self.proc = None; self._fail('Не удалось запустить процесс отрисовки: %s' % e); return False
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def warm(self, w=300, h=420):
        """Start the renderer in the background with no model (hidden). No-op when a process already exists."""
        if self.alive(): return
        self.init_msg = None; self.error = ''; self.status = 'warming'; self.t_start = time.time(); self.retried = False
        self._spawn(0, 0, w, h, None, None, True, False)

    def start(self, init_msg, x, y, w, h, model_dir, core_path=None, topmost=True, click_through=False):
        """init_msg is sent to the page as soon as the page says it is ready. Reuses a warm process when there is one."""
        warm = self.alive() and self.status in ('warming', 'warm')
        if not warm: self.stop()
        self.init_msg = init_msg; self.error = ''; self.info = {}; self.t_start = time.time(); self.status = 'starting'
        if not warm: self.retried = False
        if warm:
            self.spawn_args = (x, y, w, h, model_dir, core_path, topmost, click_through)
            self.send(cmd='serve', model_dir=model_dir or '', core=core_path or ''); self.send(cmd='place', x=x, y=y, w=w, h=h)
            self.send(cmd='clickthrough', value=click_through)
            if self.page_ready: self.send(**init_msg)          # the warm page is fresh and waiting: init right away
            return                                             # otherwise its 'page' event triggers init
        self._spawn(x, y, w, h, model_dir, core_path, topmost, click_through)

    def _read(self, proc):
        for line in proc.stdout:
            if proc is not self.proc: return                      # a replaced / stopped process must not touch the current state
            try: e = json.loads(line)
            except ValueError: continue
            ev = e.get('ev')
            if ev == 'page':
                self.page_ready = True
                if self.init_msg: self.send(**self.init_msg)
                elif self.status == 'warming': self.status = 'warm'
            elif ev == 'loaded': self.status = 'loaded'; self.info = e.get('info') or {}
            elif ev == 'error' and self.status != 'loaded': self._fail(e.get('msg') or 'ошибка отрисовки')
            elif ev == 'error': self.log('[web] runtime error:', e.get('msg'))
            try: self.on_event(e)
            except Exception as ex: self.log('[web] event handler failed', ex)

    def _fail(self, msg):
        self.status = 'failed'; self.error = msg; self.log('[web] failed:', msg)

    def send(self, **cmd):
        p = self.proc
        if p is None or p.poll() is not None or p.stdin is None: return False
        try:
            with self.lock: p.stdin.write(json.dumps(cmd, ensure_ascii=False) + '\n'); p.stdin.flush()
            return True
        except (OSError, ValueError): return False

    def poll(self):
        """Call periodically from the UI thread. -> None while fine, else the failure reason (status becomes failed)."""
        if self.status == 'failed': return self.error
        if self.status == 'idle': return None
        if self.proc is not None and self.proc.poll() is not None:
            self._fail('Процесс отрисовки завершился (код %s).' % self.proc.returncode); return self.error
        if self.status in ('starting', 'warming') and not self.page_ready and not self.retried and time.time() - self.t_spawn > PAGE_TIMEOUT and self.spawn_args:
            self.retried = True; a = self.spawn_args; self.log('[web] renderer did not come up, restarting once')   # WebView2 occasionally hangs right after a sibling process was killed
            old, self.proc = self.proc, None
            if old is not None and old.poll() is None: old.kill()
            self._spawn(*a); self.t_spawn = time.time(); return None
        if self.status == 'starting' and time.time() - self.t_start > START_TIMEOUT:
            self._fail('Модель не загрузилась за %d с.' % START_TIMEOUT); return self.error
        return None

    def stop(self, keep_warm=False):
        """keep_warm: replace the process with a fresh warm one for the next start() (only when it was healthy)."""
        if keep_warm and self.alive() and self.status in ('starting', 'loaded', 'warm', 'warming'):
            self.stop(); self.warm(); return                      # a used page cannot be reset reliably: replace it with a fresh warm process (~1.5 s, background)
        p, self.proc = self.proc, None
        self.status = 'idle'; self.init_msg = None
        if p is None: return
        try:
            if p.poll() is None:
                try: p.stdin.write('{"cmd":"quit"}\n'); p.stdin.flush(); p.stdin.close()
                except (OSError, ValueError): pass
                try: p.wait(3)
                except subprocess.TimeoutExpired: p.kill()
        except OSError: pass
