"""Голосовой ассистент для сессий Claude (Agent UI). Трей + маленькое окно статуса.
Слушаю: хоткей (push-to-talk) или слово «Клод» (Vosk) -> запись до паузы (Silero VAD) -> faster-whisper -> POST /api/voice.
Говорит: события из /api/voice/events озвучиваются Silero TTS (fallback edge-tts)."""
import collections, json, os, queue, re, sys, threading, time, zipfile
import urllib.request
from pathlib import Path

import numpy as np

from bridge_voice import Bridge
from dialog_state import BargeDetector, is_repeat
from mascot import Mascot
import characters, settings_apply, settings_store, wake, winmode
from engines import lipsync
import tts_normalize, meeting

import paths
HERE, DATA = paths.HERE, paths.DATA
LOG_PATH = DATA / 'widget.log'
_log_lock = threading.Lock()


def _open_log():
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000: os.replace(LOG_PATH, DATA / 'widget.log.1')
    except Exception: pass
    return open(LOG_PATH, 'a', encoding='utf-8', buffering=1)


_logf = _open_log()


def log(*a, **kw):
    msg = ' '.join(str(x) for x in a)
    with _log_lock:
        for line in (msg.splitlines() or ['']):
            _logf.write(time.strftime('%Y-%m-%d %H:%M:%S ') + line + '\n')


class _Tee:  # stdout/stderr (None under pythonw) -> widget.log with timestamps
    def __init__(self): self.buf = ''
    def write(self, t):
        self.buf += t
        while '\n' in self.buf:
            line, self.buf = self.buf.split('\n', 1)
            if line.strip(): log(line)
        return len(t)
    def flush(self): pass
    def isatty(self): return False


def install_logging():
    sys.stdout = sys.stderr = _Tee()
    def hook(t, v, tb):
        import traceback; log('UNCAUGHT', ''.join(traceback.format_exception(t, v, tb)))
    sys.excepthook = hook
    def thook(args):
        import traceback
        log('THREAD EXCEPTION in', getattr(args.thread, 'name', '?'), ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = thook
    import atexit; atexit.register(lambda: log('process exit (atexit)'))
    try:
        import faulthandler; faulthandler.enable(_logf)
    except Exception: pass


MODELS = DATA / 'models'
CONFIG = DATA / 'config.json'
SR = 16000
DEFAULTS = {
    'agent_ui_url': 'http://127.0.0.1:3737', 'hotkey': 'ctrl+alt+space',
    'wake_word_enabled': True, 'wake_bargein': True, 'barge_in': True,
    'speak_mode': 'immediate',            # immediate | on_request
    'whisper_model': 'auto',              # auto = medium on GPU, small on CPU
    'tts_speaker': 'xenia',               # silero: aidar baya kseniya xenia eugene; edge-tts: ru-RU-SvetlanaNeural ru-RU-DmitryNeural
    'tts_rate': 1.0,                      # speech speed
    'mic_device': None,                   # index or name part, null = default
    'followup_timeout_sec': 8,            # how long the mic stays open for an answer without the wake word
    'silence_sec': 1.2,                   # pause that ends the phrase
    'max_record_sec': 30,
    'toggle_hotkey': 'ctrl+alt+h',        # show/hide the mascot
    'stop_hotkey': 'ctrl+alt+x',          # hush + stop listening + drop pending question
    'show_when_hidden_on_question': True, # bubble alone when a question arrives while hidden
    'bubble_hide_sec': 4,                 # bubble auto-hide delay after speech
    'confirm_mode': 'unclear',            # unclear | always | never: restate the task and ask before sending
    'confirm_logprob': -0.9,              # Whisper avg_logprob below this counts as unclear speech
    'bridge_enabled': True,               # vscode_bridge sidecar: hooks from VS Code/CLI sessions
    'default_project_dir': '', 'new_session_mode': 'headless', 'trivial_direct': True, 'ask_more_after_done': True, 'speaker_device': None, 'new_session_model': 'sonnet',  # new voice session: headless (claude -p, listed+opened in VS Code) | terminal (console)
    'bridge_hold': True,                  # hold permission/AskUserQuestion prompts for a voice answer
    'bridge_hold_sec': 45,                # then fall through to the normal prompt (max 50)
    'mascot_scale': 5, 'mascot_x': None, 'mascot_y': None, 'mascot_hidden': False,
}
DEFAULTS.update(settings_store.NEW_DEFAULTS)
WAKE_WORDS = wake.words(DEFAULTS)  # live list (settings window updates it in place)
# decoy words: with a grammar of only wake words + [unk] the small Vosk model maps almost any sound to «клод»
DECOYS = ['алло', 'алё', 'привет', 'да', 'нет', 'ну', 'так', 'что', 'это', 'и', 'в', 'на', 'я', 'ты', 'он', 'не', 'как', 'ладно', 'давай', 'хорошо', 'окей',
          'слушай', 'смотри', 'сейчас', 'тест', 'раз', 'два', 'три', 'проверка', 'сколько', 'где', 'когда', 'почему', 'кто', 'вот', 'там', 'тут', 'здесь', 'один', 'ещё',
          'код', 'клад', 'клуб', 'плод', 'вход', 'ход', 'холод', 'голод', 'коль', 'клен', 'глод', 'клавиатура', 'кот', 'клип', 'класс', 'слово', 'можно', 'нужно']
SILERO_VOICES = ['aidar', 'baya', 'kseniya', 'xenia', 'eugene']
EDGE_VOICES = ['ru-RU-SvetlanaNeural', 'ru-RU-DmitryNeural']
STOPCMD = re.compile(r'^(стоп|стой|хватит|замолчи|замолчите|тихо|помолчи|достаточно|заткнись)( пожалуйста| уже| сейчас| болтать)*$')
RESUMECMD = re.compile(r'^(продолжай|продолжи|продолжайте|дальше|давай дальше|читай дальше|говори дальше)( пожалуйста)*$')
FLUSH = re.compile(r'^(что там|что нового|что новенького|есть новости|рассказывай)')


STARTUP_LNK = Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs/Startup/ClaudeVoiceWidget.lnk'


def autostart_enabled(): return STARTUP_LNK.exists()


def set_autostart(on):
    import subprocess
    if not on:
        try: STARTUP_LNK.unlink()
        except FileNotFoundError: pass
        log('[autostart] off'); return
    py = Path(sys.executable); pyw = py.with_name('pythonw.exe')
    args, target = ('', py) if paths.FROZEN else ('"%s"' % (HERE / 'widget.py'), pyw if pyw.exists() else py)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.Arguments='%s';"
          "$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Save()") % (STARTUP_LNK, target, args.replace("'", "''"), DATA if paths.FROZEN else HERE)
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], creationflags=0x08000000, check=False)
    log('[autostart] on', STARTUP_LNK.exists())


def load_config(): return settings_store.load_config(CONFIG, DEFAULTS)


def add_cuda_dlls():  # cublas/cudnn from pip (nvidia-*-cu12) for ctranslate2
    if sys.platform != 'win32': return
    import site
    for base in site.getsitepackages():
        for sub in (Path(base) / 'nvidia').glob('*/bin'):
            os.add_dll_directory(str(sub)); os.environ['PATH'] = str(sub) + os.pathsep + os.environ['PATH']


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists(): return dest
    log('download', url, flush=True)
    urllib.request.urlretrieve(url, str(dest) + '.part'); os.replace(str(dest) + '.part', dest)
    return dest


# ---------------------------------------------------------------- engines
class STT:
    def __init__(self, cfg):
        add_cuda_dlls()
        import ctranslate2
        from faster_whisper import WhisperModel
        gpu = ctranslate2.get_cuda_device_count() > 0
        name = cfg['whisper_model']
        if name == 'auto': name = 'medium' if gpu else 'small'
        root = str(MODELS / 'whisper')
        try:
            if not gpu: raise RuntimeError('no gpu')
            self.model = WhisperModel(name, device='cuda', compute_type='float16', download_root=root); self.device = 'cuda'
        except Exception:
            if name in ('medium', 'large-v3', 'large-v2', 'large-v3-turbo') and not gpu: name = 'small'
            self.model = WhisperModel(name, device='cpu', compute_type='int8', download_root=root); self.device = 'cpu'
        self.name = name; self.lock = threading.Lock()      # the live commands and the long meeting transcript share one model

    def segments(self, audio):
        # float32 mono 16k -> [(start, end, text)] with a VAD filter (meeting transcript chunks)
        with self.lock:
            segs, _ = self.model.transcribe(audio, language='ru', beam_size=1, vad_filter=True, condition_on_previous_text=False)
            return [(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]

    def __call__(self, audio):  # float32 mono 16k
        with self.lock: return self._call(audio)

    def _call(self, audio):
        segs, _ = self.model.transcribe(audio, language='ru', beam_size=1, vad_filter=False, condition_on_previous_text=False, initial_prompt='Клод, скажи.')
        segs = list(segs); self.last_logprob = sum(s.avg_logprob for s in segs) / len(segs) if segs else 0.0
        return ' '.join(s.text.strip() for s in segs).strip()


class TTS:
    URL = 'https://models.silero.ai/models/tts/ru/v5_ru.pt'

    def __init__(self, cfg):
        self.speaker, self.model, self.rate, self.rate_mul = cfg['tts_speaker'], None, 48000, cfg['tts_rate']
        try:
            import torch
            torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
            path = download(self.URL, MODELS / 'silero' / 'v5_ru.pt')
            self.model = torch.package.PackageImporter(str(path)).load_pickle('tts_models', 'model')
            self.model.to('cpu')
            self.torch = torch
        except Exception as e:
            log('silero unavailable, edge-tts fallback:', e, flush=True)

    def synth(self, text):  # -> (float32 array, sample rate)
        text = tts_normalize.normalize(text)   # silero v5_ru is Russian-only: digits and Latin letters were skipped, so spell them out first (display text is untouched)
        if not text: return np.zeros(2400, dtype='float32'), self.rate
        if self.model is not None and self.speaker in SILERO_VOICES:
            with self.torch.no_grad():
                a = self.model.apply_tts(text=text, speaker=self.speaker, sample_rate=self.rate, put_accent=True, put_yo=True)
            return a.numpy().astype('float32'), self.rate
        import asyncio, edge_tts, soundfile as sf, tempfile
        tmp = Path(tempfile.gettempdir()) / 'voice-edge.mp3'
        asyncio.run(edge_tts.Communicate(text, self.speaker if self.speaker in EDGE_VOICES else EDGE_VOICES[0], rate='%+d%%' % round((self.rate_mul - 1) * 100)).save(str(tmp)))
        data, sr = sf.read(str(tmp), dtype='float32')
        return (data if data.ndim == 1 else data.mean(1)), sr


def make_vad():
    from silero_vad import load_silero_vad
    import torch
    m = load_silero_vad()
    def prob(frame):  # int16 x 512
        return float(m(torch.from_numpy(frame.astype('float32') / 32768), SR))
    return prob, m.reset_states


def make_wake(cfg):
    d = MODELS / 'vosk' / 'vosk-model-small-ru-0.22'
    if not d.exists():
        z = download('https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip', MODELS / 'vosk' / 'small-ru.zip')
        zipfile.ZipFile(z).extractall(MODELS / 'vosk')
    return wake.Detector(cfg, log=log)


# ---------------------------------------------------------------- app
class Widget:
    def __init__(self):
        self.cfg = load_config()
        self.state, self.held, self.last_ask = 'загрузка моделей', [], None
        self.last_said = None
        self.msgs = queue.Queue(); self.audio = queue.Queue()
        self.speaking = threading.Event(); self.stop_speak = threading.Event(); self.recording = False
        self.cancel_rec = False
        self.barge_det = BargeDetector(); self.barge_hold = False; self.cur_item = None; self.redo = None
        self.meeting = False; self.meeting_busy = False; self.meeting_rec = None; self.last_transcript = None
        self.muted = False; self.auto_muted = False; self.paused = False; self._mic_chk = 0.0
        self.ptt = False; self.followup = False; self.last_voice = 0.0; self.quit = False
        self.ring = collections.deque(maxlen=48)  # ~1.5 s pre-roll
        self.working = False; self.work_until = 0.0; self.ui = None; self.followup_rec = False
        self.ready = False; self.mic_changed = False; self.current_mic = None; self.mic_status = ''; self.bridge = None; self.stt_unclear = False; self._ui_ok = (0.0, False)

    def save(self):
        settings_store.save_config(CONFIG, self.cfg)  # atomic

    def set_state(self, s):
        self.state = s; log('[state]', s, flush=True)
        if not self.ui: return
        if self.working and time.time() > self.work_until: self.working = False
        base = 'working' if self.working else 'idle'
        if s.startswith('слушаю…'): m = 'listening'
        elif s.startswith('распознаю'): m = 'transcribing'
        elif s.startswith('загрузка'): m = 'thinking'
        elif s == 'говорит': m = 'speaking'; self.working = False
        elif s.startswith(('ошибка', 'нет связи')): m = 'error'; self.working = False
        elif s.startswith('отправил') or s == 'ответ принят': self.working = True; self.work_until = time.time() + 90; m = 'working'
        else: m = base
        self.ui.set_state(m)

    def parse_ask(self, text):
        text = re.sub(r'^«[^»]*» спрашивает\.\s*', '', text)
        part = None
        m = re.search(r'Вопрос (\d+) из (\d+)\.\s*', text)
        if m: part = f'{m.group(1)}/{m.group(2)}'; text = text.replace(m.group(0), '', 1)
        opts = []
        m = re.search(r'\s*Варианты: (.*?)\.?$', text, re.S)
        if m: opts = [o.strip() for o in m.group(1).split(', ') if o.strip()]; text = text[:m.start()]
        return text.strip(), part, opts

    def pick_option(self, label):  # click in the bubble == voice answer
        self.followup = False
        self.ui.say(label, 'user', hide=3); self.stt_unclear = False
        threading.Thread(target=self.answer_or_send, args=(label,), daemon=True).start()

    def answer_or_send(self, text):
        if self.meeting: log('[meeting] input ignored while recording'); return
        if self.bridge and self.bridge.try_answer(text): return
        self.send(text)

    def agent_ui_alive(self):
        t, ok = self._ui_ok
        if time.time() - t > 10:
            try: urllib.request.urlopen(self.cfg['agent_ui_url'] + '/', timeout=0.7).close(); ok = True
            except Exception as e: ok = hasattr(e, 'code')  # an HTTP error still means it is up
            self._ui_ok = (time.time(), ok)
        return ok

    def show_message(self, text, ask, extra=None):
        cps = 15.0 * self.cfg['tts_rate']; extra = extra or {}
        if 'opts' in extra:  # bridge prompt: options come structured, header = project
            self.ui.say(extra.get('shown', text), 'say', extra['opts'] or None, extra.get('header'), cps, None, force=self.cfg['show_when_hidden_on_question']); return
        if ask:
            body, part, opts = self.parse_ask(text)
            self.ui.say(body, 'say', opts, part, cps, None, force=self.cfg['show_when_hidden_on_question'])
        else: self.ui.say(text, 'say', None, extra.get('header'), cps, None)

    # ---- speaking: never while the user talks; barge-in stops it
    def say_event(self, text, ask=False, extra=None):
        ask = ask or str(text).rstrip().endswith('?')  # a spoken question always opens follow-up listening
        item = (text, ask, extra or {})
        if self.meeting and not (extra or {}).get('meeting'): self.held.append(item); return      # nothing is spoken over a meeting; it waits in the queue
        if ask: self.last_ask = item
        self.last_said = item
        self.working = False
        if self.cfg['speak_mode'] == 'on_request': self.held.append(item); self.set_state(f'ждёт: {len(self.held)}'); return
        self.msgs.put(item)

    def repeat(self):
        """Re-speak the last assistant text (the pending question first); the dialog state is left as it is."""
        reopened = self.bridge.dlg.reask() if self.bridge else None   # an expired clarification comes back once
        pending = self.bridge.dlg.pending if self.bridge else None
        item = self.last_ask if (pending or reopened) and self.last_ask else self.last_said
        if reopened and not item: item = (reopened.text, True, {})
        if item: self.msgs.put(item); return
        self.msgs.put(('Мне пока нечего повторить.', False, {}))

    def flush(self):
        if self.bridge: self.bridge.dlg.reask()
        if self.last_ask and not self.held: self.msgs.put(self.last_ask); return
        items, self.held = self.held, []
        for it in items: self.msgs.put(it)
        if not items: self.msgs.put(('Новостей нет.', False, {}))

    def speaker_loop(self):
        import sounddevice as sd
        while not self.quit:
            while (self.barge_hold or self.paused) and not self.quit: time.sleep(0.05)      # the user is answering: the next question waits until the answer is handled
            if self.redo:                    # recognition failed: ask the same question again, before the rest of the queue
                item, self.redo = self.redo, None
                with self.msgs.mutex: self.msgs.queue.appendleft(item); self.msgs.not_empty.notify()
            try: text, ask, extra = self.msgs.get(timeout=0.3)
            except queue.Empty: continue
            self.cur_item = (text, ask, extra)
            self.stop_speak.clear(); self.show_message(text, ask, extra)
            for part in re.split(r'(?<=[.!?])\s+', text):
                part = part.strip()
                if not part or self.stop_speak.is_set(): continue
                while self.recording or time.time() - self.last_voice < 0.8: time.sleep(0.1)  # do not interrupt the user
                try: wav, sr = self.tts.synth(part[:700])
                except Exception as e: log('tts error', e, flush=True); continue
                while self.recording or time.time() - self.last_voice < 0.8: time.sleep(0.1)
                self.set_state('говорит'); self.speaking.set()
                try:
                    if self.tts.model is not None and self.tts.speaker in SILERO_VOICES: sr = int(sr * self.cfg['tts_rate'])  # silero: speed via playback rate
                    with sd.OutputStream(samplerate=sr, channels=1, dtype='float32', device=self.resolve_speaker()) as out:
                        for i in range(0, len(wav), sr // 25):
                            if self.stop_speak.is_set() or self.ptt: break
                            ch = wav[i:i + sr // 25]; self.ui.set_mouth(lipsync.rms_level(ch))   # mouth follows the real audio
                            out.write(ch.reshape(-1, 1))
                except Exception as e: log('play error', e, flush=True)
                self.ui.set_mouth(None); self.speaking.clear()
            if not self.barge_hold: self.cur_item = None
            self.set_state('слушаю')
            self.ui.release(60 if ask else self.cfg['bubble_hide_sec'])
            if ask and not self.stop_speak.is_set() and self.msgs.empty() and not self.paused and not self.muted:
                time.sleep(0.3); self.followup = True  # open the mic for the answer, no wake word needed

    # ---- listening
    def frame(self, timeout=0.3):
        try: return self.audio.get(timeout=timeout)
        except queue.Empty: return None

    def record(self, preroll=None, initial_timeout=6.0, ptt=False):
        self.recording = True; self.set_state('слушаю…')
        if self.followup_rec: self.ui.say('Слушаю…', 'listen', hide=None); self.vad_reset()
        frames = list(preroll or []); started = bool(preroll); last_speech = time.time(); t0 = time.time(); speech_frames = len(frames) // 4
        while time.time() - t0 < self.cfg['max_record_sec'] and not self.quit:
            f = self.frame()
            if f is None: continue
            frames.append(f)
            if self.vad(f) > 0.5:
                started = True; last_speech = self.last_voice = time.time(); speech_frames += 1
            if ptt:
                if not self.ptt: break
            elif started and time.time() - last_speech >= self.cfg['silence_sec']: break
            elif not started and time.time() - t0 > initial_timeout: break
            if self.cancel_rec: self.cancel_rec = False; frames = []; break  # stop hotkey
        self.recording = False; self.last_voice = time.time()
        return np.concatenate(frames) if frames and speech_frames * 512 / SR > 0.25 else None

    def handle(self, audio, via_wake):
        if self.meeting: return False
        self.set_state('распознаю')
        log('[stt] audio %.1fs' % (len(audio) / SR)); text = self.stt(audio.astype('float32') / 32768)
        if text.strip(): self.ui.say(text.strip(), 'user', hide=3)
        low = re.sub(r'[^\w\s]', ' ', text.lower()).strip()
        log('[heard] STT text=%r logprob=%.2f' % (text, self.stt.last_logprob))
        letters = sum(c.isalpha() for c in text); words = len(text.split())
        self.stt_unclear = self.stt.last_logprob < self.cfg['confirm_logprob'] or words < 2 or letters < 0.6 * max(1, len(text.replace(' ', '')))
        low = re.sub(r'^(эй\s+)?(' + '|'.join(WAKE_WORDS) + r')\s*', '', low).strip()
        if not low:  # only the wake word: wait for the command without it
            self.followup = True; return bool(text.strip())
        if STOPCMD.match(low): self.pause_speech(); return True
        if RESUMECMD.match(low): self.resume_speech(); return True
        if FLUSH.match(low): self.flush(); return True
        if is_repeat(low): self.repeat(); return True
        self.answer_or_send(text); return True

    def send(self, text):
        import requests
        self.last_ask = None
        if self.bridge and self.bridge.send_task(text, self.stt_unclear): return
        try:
            r = requests.post(self.cfg['agent_ui_url'] + '/api/voice', json={'text': text}, timeout=120).json()
        except Exception as e:
            self.set_state('нет связи с Agent UI'); self.say_event('Нет связи с Agent U I.'); log(e); return
        if 'error' in r: self.set_state('ошибка: ' + str(r['error'])[:40]); return
        s = r.get('session')
        self.set_state(f'отправил в «{s}»' if s else 'ответ принят'); log('[route]', r, flush=True)

    def input_devices(self): return self.devices('max_input_channels')

    def output_devices(self): return self.devices('max_output_channels')

    def devices(self, key):  # [(index, name)] on the default host API (no duplicates)
        import sounddevice as sd
        try: api = sd.query_hostapis(sd.default.hostapi)['name']
        except Exception: api = None
        out, seen = [], set()
        for i, d in enumerate(sd.query_devices()):
            if d[key] < 1: continue
            if api and sd.query_hostapis(d['hostapi'])['name'] != api: continue
            n = d['name']
            if 'mapper' in n.lower() or 'primary sound' in n.lower() or n in seen: continue
            seen.add(n); out.append((i, n))
        return out

    def resolve_mic(self):
        import sounddevice as sd
        want = self.cfg['mic_device']
        if isinstance(want, str) and want.isdigit(): want = int(want)
        if isinstance(want, int): return want
        devs = self.input_devices()
        if isinstance(want, str) and want:
            for i, n in devs:
                if want.lower() in n.lower(): return i
            log('mic_device not found:', want)
        d = sd.default.device[0]
        if d is not None and d >= 0 and 'mapper' not in sd.query_devices(d)['name'].lower(): return d
        return devs[0][0] if devs else None

    def resolve_speaker(self):
        want = self.cfg.get('speaker_device')
        if not want: return None
        try:
            for i, n in self.output_devices():
                if want.lower() in n.lower(): return i
        except Exception as e: log('speaker lookup error', e)
        log('speaker_device not found:', want); return None

    def set_speaker(self, name):
        self.cfg['speaker_device'] = name; self.save(); log('[speaker] selected', name)
        self.msgs.put(('Привет, я Клод.', False, {}))

    def set_mic(self, name):
        self.cfg['mic_device'] = name; self.save(); self.mic_changed = True; log('[mic] selected', name)

    def listen_loop(self):
        while not self.quit:
            while (self.muted or self.meeting) and not self.quit: self.check_auto_mute(); time.sleep(0.25)       # no input stream at all while muted
            try: self.listen_once()
            except Exception:
                import traceback; log('listen_loop error, retry in 3s', traceback.format_exc()); time.sleep(3)

    def listen_once(self):
        import sounddevice as sd
        dev = self.resolve_mic(); self.mic_changed = False; self.current_mic = dev
        info = sd.query_devices(dev)
        log('[mic] using index', dev, info['name'], 'hostapi', sd.query_hostapis(info['hostapi'])['name'], 'default sr', info['default_samplerate'])
        log('[mic] all inputs:', '; '.join(f'{i}:{n}' for i, n in self.input_devices()))
        def cb(indata, frames, t, status):
            if status: self.mic_status = str(status)
            self.audio.put(indata[:, 0].copy())
        lvl_max, lvl_sum, lvl_n, lvl_t, in_speech, sp_t0, last_part = 0.0, 0.0, 0, time.time(), False, 0.0, ''
        with sd.InputStream(samplerate=SR, channels=1, dtype='int16', blocksize=512, device=dev, callback=cb):
            self.set_state('слушаю')
            while not self.quit and not self.mic_changed and not self.muted and not self.meeting:
                self.check_auto_mute()
                f = self.frame()
                if f is None:
                    if time.time() - lvl_t > 5: log('[mic] NO AUDIO FRAMES for 5s'); lvl_t = time.time()
                    continue
                rms = float(np.sqrt(np.mean(f.astype('float32') ** 2))); lvl_max = max(lvl_max, rms); lvl_sum += rms; lvl_n += 1
                if time.time() - lvl_t >= 5:
                    log(f'[level] rms avg={lvl_sum / max(1, lvl_n):.0f} max={lvl_max:.0f} (int16) frames={lvl_n} {self.mic_status}')
                    lvl_max, lvl_sum, lvl_n, lvl_t, self.mic_status = 0.0, 0.0, 0, time.time(), ''
                self.ring.append(f)
                p = self.vad(f)
                if p > 0.5 and not in_speech: in_speech, sp_t0 = True, time.time(); log('[vad] speech start p=%.2f rms=%.0f' % (p, rms))
                elif p < 0.35 and in_speech and time.time() - self.last_voice > 0.5: in_speech = False; log('[vad] speech end %.1fs' % (time.time() - sp_t0))
                if not self.speaking.is_set() and p > 0.5: self.last_voice = time.time()
                if self.cfg.get('barge_in', True) and self.barge_det.feed(p, rms, self.speaking.is_set(), time.time()):
                    log('[barge] user speech over TTS rms=%.0f' % rms); self.barge_hold = True; self.barge()
                    pre = list(self.ring); self.ring.clear(); self.barge_det.reset(); ok = False
                    try:
                        a = self.record(preroll=pre)
                        log('[barge] recorded', None if a is None else '%.1fs' % (len(a) / SR))
                        ok = a is not None and bool(self.handle(a, False))
                    finally: self.finish_barge(ok); self.set_state('слушаю')
                    continue
                if self.ptt:
                    log('[hotkey] push-to-talk start'); self.barge(); a = self.record(ptt=True)
                    log('[hotkey] push-to-talk end, audio', None if a is None else '%.1fs' % (len(a) / SR))
                    if a is None: self.flush()  # hotkey with empty speech
                    else: self.handle(a, False)
                    self.set_state('слушаю'); continue
                if self.followup:
                    self.followup = False; time.sleep(0.2)
                    while not self.audio.empty(): self.audio.get_nowait()
                    self.followup_rec = True; log('[followup] mic open')
                    try: a = self.record(initial_timeout=self.cfg['followup_timeout_sec'])
                    finally: self.followup_rec = False
                    if a is None: self.ui.release(self.cfg['bubble_hide_sec'] + 2)
                    if a is not None: self.handle(a, False)
                    else: self.set_state('слушаю')  # silence: the question stays queued (last_ask), ask for it by hotkey
                    continue
                if self.cfg['wake_word_enabled'] and (not self.speaking.is_set() or self.cfg['wake_bargein']):
                    part, hit, fin = self.rec.feed(f.tobytes())
                    if part and (fin or part != last_part): log('[vosk] final:' if fin else '[vosk] partial:', repr(part))
                    last_part = part
                    if hit:
                        log('[wake] detected in', repr(part)); self.rec.reset(); self.barge()
                        pre = list(self.ring); self.ring.clear()
                        a = self.record(preroll=pre)
                        log('[wake] recorded', None if a is None else '%.1fs' % (len(a) / SR))
                        if a is not None: self.handle(a, True)
                        else: self.set_state('слушаю')
        if self.muted or self.meeting:
            while not self.audio.empty(): self.audio.get_nowait()
            self.ring.clear(); self.rec.reset() if hasattr(self, 'rec') and self.rec else None
        log('[mic] stream closed, restarting' if self.mic_changed else '[mic] closed (muted)' if self.muted else '[mic] listen loop ended')

    def stop_all(self):
        # stop hotkey: hush, close the follow-up mic, drop pending questions
        log('[hotkey] stop'); self.followup = False; self.barge()
        if self.recording and not self.ptt: self.cancel_rec = True
        if self.bridge: self.bridge.dlg.reset()
        self.ui.release(0); self.set_state('слушаю')

    def pause_speech(self):
        """Voice 'стоп' / 'хватит' / 'замолчи' / 'тихо': hush at once. The interrupted message and the rest of the queue are KEPT; 'продолжай' / 'дальше' resumes."""
        log('[stop] voice command: speech paused, queue kept (%d)' % self.msgs.qsize())
        self.paused = True; self.stop_speak.set(); self.followup = False
        if self.cur_item and not self.redo: self.redo = self.cur_item
        self.ui.release(0); self.set_state('слушаю')

    def resume_speech(self):
        if not self.paused and not self.redo and self.msgs.empty(): self.msgs.put(('Больше ничего нет.', False, {})); return
        log('[stop] resume'); self.paused = False

    # ---- meeting recording (mic + PC audio -> local transcript; nothing is sent anywhere)
    def meeting_dir(self):
        base = self.cfg.get('meeting_dir') or str(Path.home() / 'Documents' / 'voxcrab' / 'meetings')
        return Path(base) / time.strftime('%Y-%m-%d_%H-%M-%S')

    def open_meetings_folder(self):
        root = Path(self.cfg.get('meeting_dir') or Path.home() / 'Documents' / 'voxcrab' / 'meetings')
        root.mkdir(parents=True, exist_ok=True)
        try: os.startfile(str(root))
        except OSError as e: log('[meeting] open folder failed:', e)

    def toggle_meeting(self):
        if self.meeting: self.stop_meeting()
        else: self.start_meeting()

    def start_meeting(self):
        if self.meeting_busy: self.ui.say('Расшифровка предыдущей записи ещё идёт.', 'say', None, None, 16.0, 4, True); return
        if self.muted:
            log('[meeting] refused: do-not-listen mode is on')
            self.ui.say('Включён режим «Не слушать». Выключи его (Ctrl+Alt+M), потом запись.', 'say', None, None, 16.0, 6, True); return
        self.meeting = True; self.followup = False; self.ptt = False
        if self.recording: self.cancel_rec = True
        self.barge(); self.ui.set_recording(True)
        threading.Thread(target=self._meeting_start, daemon=True).start()

    def _meeting_start(self):
        time.sleep(0.7)                       # the listening stream closes first, then the recorder opens the microphone
        self.meeting_dir_now = self.meeting_dir()
        rec = meeting.Recorder(self.resolve_mic(), log); rec.start(); self.meeting_rec = rec
        log('[meeting] recording ->', self.meeting_dir_now, 'loopback', rec.loopback_ok, rec.error)
        note = 'Идёт запись встречи' + ('' if rec.loopback_ok else ' (только микрофон: ' + (rec.error or 'звук ПК недоступен') + ')')
        self.ui.say(note + '. Ctrl+Alt+R чтобы закончить.', 'listen', None, None, 16.0, None, True)

    def stop_meeting(self):
        rec, self.meeting_rec = self.meeting_rec, None
        if rec is None: return
        self.ui.set_recording(False); self.meeting_busy = True
        threading.Thread(target=self._meeting_finish, args=(rec,), daemon=True).start()

    def _meeting_finish(self, rec):
        folder = self.meeting_dir_now
        try:
            started = time.strftime('%Y-%m-%d %H:%M', time.localtime(rec.t0))
            mic, others, dur = rec.stop(); self.meeting = False       # listening may resume; the transcript runs in the background
            files = meeting.save_tracks(folder, mic, others); log('[meeting] saved', folder, '%.0fs' % dur)
            total = len(mic) + len(others); done = {'n': 0}
            def prog(b): self.ui.say('Расшифровка: %d%%' % min(99, 100 * (done['n'] + b) // max(1, total)), 'say', None, None, 16.0, None, True)
            me = meeting.transcribe_track(self.stt.segments, mic, prog); done['n'] = len(mic)
            ot = meeting.transcribe_track(self.stt.segments, others, prog)
            segs = meeting.merge_segments(me, ot)
            md = meeting.render_md(segs, started, dur, files + ['transcript.md', 'transcript.txt'])
            txt = meeting.render_txt(segs)
            (folder / 'transcript.md').write_text(md, encoding='utf-8'); (folder / 'transcript.txt').write_text(txt, encoding='utf-8')
            self.last_transcript = txt; log('[meeting] transcript', len(segs), 'segments')
            self.ui.copy_text(txt)
            try: os.startfile(str(folder / 'transcript.md'))
            except Exception as e: log('[meeting] open failed', e)
            self.ui.say('Расшифровка готова: %s (текст в буфере обмена)' % folder, 'say', None, None, 16.0, 8, True)
            self.say_event('Расшифровка готова.', False, {'meeting': True})
        except Exception:
            import traceback; log('[meeting] failed', traceback.format_exc())
            self.ui.say('Не удалось сохранить или расшифровать запись, подробности в widget.log.', 'say', None, None, 16.0, 8, True)
        finally:
            self.meeting = False; self.meeting_busy = False
            if self.held: self.flush_held()

    def send_transcript(self):
        # only on an explicit menu click: hand the last transcript to Claude (never automatic)
        if not self.last_transcript: self.ui.say('Расшифровки пока нет.', 'say', None, None, 16.0, 4, True); return
        self.answer_or_send('Вот расшифровка нашей встречи. Сделай краткое резюме, решения и список задач:\n' + self.last_transcript[:20000])

    # ---- do not listen (microphone stream is closed, nothing is captured)
    def set_muted(self, on, auto=False):
        if on == self.muted: return
        self.muted = on; self.auto_muted = bool(on and auto)
        log('[mute]', 'ON' if on else 'off', '(auto: Discord uses the microphone)' if auto else '')
        self.followup = False; self.ptt = False
        if on and self.recording: self.cancel_rec = True
        try: self.ui.set_muted(on)
        except Exception as e: log('mute ui error', e)
        try: self.icon.title = 'Голос Claude: не слушаю' if on else 'Голос Claude'
        except Exception: pass
        self.say_event('Не слушаю.' if on else 'Слушаю.', False, {})

    def toggle_mute(self): self.set_muted(not self.muted)

    def check_auto_mute(self):
        if not self.cfg.get('mute_on_call') or time.time() - self._mic_chk < 3: return
        self._mic_chk = time.time()
        try: busy = winmode.mic_busy_by(('discord',))
        except Exception: return
        if busy and not self.muted: self.set_muted(True, auto=True)
        elif not busy and self.muted and self.auto_muted: self.set_muted(False)

    def finish_barge(self, answered):
        """The user interrupted a question and has been handled. Not understood -> the same question is asked again before the rest of the queue."""
        if not answered and self.cur_item and self.cur_item[1]: self.redo = self.cur_item; log('[barge] not understood, asking again')
        self.cur_item = None; self.barge_hold = False

    def barge(self):
        if self.speaking.is_set(): self.stop_speak.set(); time.sleep(0.15)

    # ---- agent-ui events
    def events_loop(self):
        import requests
        while not self.quit:
            try:
                with requests.get(self.cfg['agent_ui_url'] + '/api/voice/events', stream=True, timeout=(5, None)) as r:
                    for line in r.iter_lines(decode_unicode=True):
                        if self.quit: return
                        if line and line.startswith('data:'):
                            ev = json.loads(line[5:])
                            if ev.get('kind') == 'speak': self.say_event(ev['text'], bool(ev.get('ask')))
            except Exception: time.sleep(3)

    # ---- hotkey
    def hotkeys(self):  # (re)registers; called again when the settings window changes a hotkey
        import keyboard
        for h in getattr(self, '_hk', []):
            try: h()  # add_hotkey / on_release_key return their own remover
            except Exception: pass
        self._hk = []
        hk = self.cfg['hotkey']
        def press():
            log('[hotkey] pressed', hk)
            if not self.ptt and not self.muted: self.ptt = True
        self._hk.append(keyboard.add_hotkey(hk, press))
        if self.cfg['toggle_hotkey']: self._hk.append(keyboard.add_hotkey(self.cfg['toggle_hotkey'], lambda: (log('[hotkey] toggle'), self.ui.toggle())))
        if self.cfg.get('stop_hotkey'): self._hk.append(keyboard.add_hotkey(self.cfg['stop_hotkey'], self.stop_all))
        if self.cfg.get('meeting_hotkey'): self._hk.append(keyboard.add_hotkey(self.cfg['meeting_hotkey'], lambda: (log('[hotkey] meeting toggle'), self.toggle_meeting())))
        if self.cfg.get('mute_hotkey'): self._hk.append(keyboard.add_hotkey(self.cfg['mute_hotkey'], lambda: (log('[hotkey] mute toggle'), self.toggle_mute())))
        if self.cfg.get('settings_hotkey'): self._hk.append(keyboard.add_hotkey(self.cfg['settings_hotkey'], lambda: self.ui.open_settings()))
        self._hk.append(keyboard.on_release_key(hk.split('+')[-1], lambda e: setattr(self, 'ptt', False)))

    # ---- settings window glue (settings_ui / settings_apply)
    voices = SILERO_VOICES + EDGE_VOICES

    def log(self, *a): log(*a)

    def defaults(self): return dict(DEFAULTS)

    def apply_settings(self, changes): return settings_apply.apply(self, changes, WAKE_WORDS, log)

    def test_speaker(self, name):  # plays a phrase on the chosen (not yet saved) output device
        if not getattr(self, 'tts', None): raise RuntimeError('голос еще загружается')
        def run():
            try:
                import sounddevice as sd
                dev = next((i for i, n in self.output_devices() if name and name.lower() in n.lower()), None)
                wav, sr = self.tts.synth('Привет, я Клод. Проверка динамика.'); sd.play(wav, sr, device=dev); sd.wait()
            except Exception as e: log('test_speaker error', e)
        threading.Thread(target=run, daemon=True).start()

    # ---- ui
    def tray(self):
        import pystray
        from PIL import Image, ImageDraw
        try: img = Image.open(HERE / 'assets' / 'icons' / 'icon-64.png').convert('RGBA')      # the pixel crab
        except Exception: img = Image.new('RGBA', (64, 64), (0, 0, 0, 0)); ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=(133, 108, 255, 255))
        def on_req(item): return self.cfg['speak_mode'] == 'on_request'
        def toggle_mode(icon, item):
            self.cfg['speak_mode'] = 'immediate' if on_req(None) else 'on_request'; self.save()
            if self.cfg['speak_mode'] == 'immediate': self.flush_held()
        def toggle_wake(icon, item): self.cfg['wake_word_enabled'] = not self.cfg['wake_word_enabled']; self.save()
        def toggle_win(icon, item): self.ui.toggle()
        def voice_item(name):
            def pick(icon, item):
                self.cfg['tts_speaker'] = self.tts.speaker = name; self.save(); self.msgs.put(('Привет, я Клод.', False, {}))
            return pystray.MenuItem(name, pick, checked=lambda i: self.cfg['tts_speaker'] == name, radio=True)
        def rate_item(v):
            def pick(icon, item):
                self.cfg['tts_rate'] = self.tts.rate_mul = v; self.save(); self.msgs.put(('Привет, я Клод.', False, {}))
            return pystray.MenuItem('x%.2f' % v, pick, checked=lambda i: self.cfg['tts_rate'] == v, radio=True)
        def mic_item(i, name):
            def pick(icon, item): self.set_mic(name)
            return pystray.MenuItem(name, pick, checked=lambda it: self.current_mic == i, radio=True)
        def mic_menu():
            try:
                dflt = pystray.MenuItem('По умолчанию', lambda icon, item: self.set_mic(None), checked=lambda it: not self.cfg['mic_device'], radio=True)
                return [dflt] + [mic_item(i, n) for i, n in self.input_devices()]
            except Exception as e: log('mic menu error', e); return []
        def spk_item(name):
            return pystray.MenuItem(name, lambda icon, item: self.set_speaker(name), checked=lambda it: self.cfg.get('speaker_device') == name, radio=True)
        def spk_menu():
            try:
                dflt = pystray.MenuItem('По умолчанию', lambda icon, item: self.set_speaker(None), checked=lambda it: not self.cfg.get('speaker_device'), radio=True)
                return [dflt] + [spk_item(n) for _, n in self.output_devices()]
            except Exception as e: log('speaker menu error', e); return []
        def toggle_auto(icon, item): set_autostart(not autostart_enabled())
        def quit_(icon, item): self.exit()
        menu = pystray.Menu(
            pystray.MenuItem('Озвучивать по запросу', toggle_mode, checked=on_req),
            pystray.MenuItem('Запись встречи', lambda i, it: self.toggle_meeting(), checked=lambda i: self.meeting),
            pystray.MenuItem('Отправить расшифровку Клоду…', lambda i, it: self.send_transcript(), enabled=lambda i: bool(self.last_transcript)),
            pystray.MenuItem('Папка с записями', lambda i, it: self.open_meetings_folder()),
            pystray.MenuItem('Не слушать', lambda i, it: self.toggle_mute(), checked=lambda i: self.muted),
            pystray.MenuItem('Слово «Клод»', toggle_wake, checked=lambda i: self.cfg['wake_word_enabled']),
            pystray.MenuItem(lambda i: 'Показать Клода' if self.ui.hidden else 'Скрыть Клода', toggle_win, default=True),
            pystray.MenuItem('Голос', pystray.Menu(*[voice_item(v) for v in SILERO_VOICES + EDGE_VOICES])),
            pystray.MenuItem('Скорость речи', pystray.Menu(*[rate_item(v) for v in (0.85, 1.0, 1.15, 1.3)])),
            pystray.MenuItem('Микрофон', pystray.Menu(lambda: mic_menu())),
            pystray.MenuItem('Динамик', pystray.Menu(lambda: spk_menu())),
            pystray.MenuItem('Настройки…', lambda icon, item: self.ui.open_settings()),
            pystray.MenuItem('Запускать с Windows', toggle_auto, checked=lambda i: autostart_enabled()),
            pystray.MenuItem('Выход', quit_))
        self.icon = pystray.Icon('claude-voice', img, 'Голос Claude', menu); self.icon.run_detached()

    def flush_held(self):
        items, self.held = self.held, []
        for it in items: self.msgs.put(it)

    def ctx_menu(self, m):
        import tkinter as tk
        m.add_command(label='Показать Клода' if self.ui.hidden else 'Скрыть', command=self.ui.toggle)
        vm = tk.Menu(m, tearoff=0)
        for v in SILERO_VOICES + EDGE_VOICES:
            vm.add_command(label=('● ' if self.cfg['tts_speaker'] == v else '   ') + v, command=lambda v=v: self.set_voice(v))
        m.add_cascade(label='Голос', menu=vm)
        sm = tk.Menu(m, tearoff=0)
        for key, name in (('immediate', 'Озвучивать сразу'), ('on_request', 'По запросу')):
            sm.add_command(label=('● ' if self.cfg['speak_mode'] == key else '   ') + name, command=lambda k=key: self.set_mode(k))
        m.add_cascade(label='Режим речи', menu=sm)
        try:
            mm = tk.Menu(m, tearoff=0); cur = self.cfg['mic_device']
            for n in [None] + [n for _, n in self.input_devices()]:
                mm.add_command(label=('● ' if (cur or None) == n else '   ') + (n or 'По умолчанию'), command=lambda n=n: self.set_mic(n))
            m.add_cascade(label='Микрофон', menu=mm)
            km = tk.Menu(m, tearoff=0); cur = self.cfg.get('speaker_device')
            for n in [None] + [n for _, n in self.output_devices()]:
                km.add_command(label=('● ' if (cur or None) == n else '   ') + (n or 'По умолчанию'), command=lambda n=n: self.set_speaker(n))
            m.add_cascade(label='Динамик', menu=km)
        except Exception as e: log('device menu error', e)
        m.add_command(label='Остановить запись встречи' if self.meeting else 'Запись встречи', command=self.toggle_meeting)
        m.add_separator(); m.add_command(label='Настройки…', command=lambda: self.ui.open_settings()); m.add_command(label='Выход', command=self.exit)

    def set_voice(self, v):
        self.cfg['tts_speaker'] = self.tts.speaker = v; self.save(); self.msgs.put(('Привет, я Клод.', False, {}))

    def set_mode(self, k):
        self.cfg['speak_mode'] = k; self.save()
        if k == 'immediate': self.flush_held()

    def exit(self):
        import traceback; log('[exit] exit() called from', ''.join(traceback.format_stack(limit=4)))
        self.quit = True
        if self.bridge: self.bridge.stop()
        self.ui.stop()
        try: self.icon.stop()
        except Exception: pass

    def start(self):
        self.ui = Mascot(self.cfg, on_moved=self.save, menu_builder=self.ctx_menu, on_pick=self.pick_option, log=log)
        self.ui.settings_host = self; self.tray()
        def boot():
          try:
            if self.cfg['bridge_enabled']:
                try: self.bridge = Bridge(self).start(); log('bridge on', self.bridge.sc.cfg['port'], flush=True)
                except Exception as e: log('bridge unavailable:', e, flush=True)
            self.stt = STT(self.cfg); log('stt', self.stt.name, self.stt.device, flush=True)
            self.tts = TTS(self.cfg); self.vad, self.vad_reset = make_vad(); self.rec = make_wake(self.cfg)
            self.hotkeys()
            for fn in (self.speaker_loop, self.listen_loop, self.events_loop): threading.Thread(target=fn, daemon=True).start()
            self.ready = True; log('[boot] ready')
          except Exception:
            import traceback; log('BOOT FAILED', traceback.format_exc())
        threading.Thread(target=boot, daemon=True).start()
        try: self.ui.run()
        finally: log('[ui] Tk mainloop returned, quit flag =', self.quit)


def single_instance():
    import socket
    s = socket.socket()
    try: s.bind(('127.0.0.1', 47899))
    except OSError: return None
    return s


if __name__ == '__main__':
    if sys.platform != 'win32':
        sys.exit('voice-widget currently supports Windows 10/11 only (global hotkeys, Win32 window APIs, WebView2). See README, section Platforms.')
    if paths.FROZEN and '--web-child' in sys.argv:      # the packaged exe doubles as the Live2D/VRM renderer process
        sys.argv.remove('--web-child')
        from engines import web_child
        web_child.main(); sys.exit(0)
    install_logging()
    log('=== start pid', os.getpid(), sys.executable)
    _lock = single_instance()
    if _lock is None: log('another instance is running, exit'); sys.exit(0)
    Widget().start()
