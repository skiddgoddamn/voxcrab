"""Meeting recording: your microphone plus the PC output (WASAPI loopback of the default render device) as two tracks, then a local
Whisper transcript with speakers separated by source ("Я" = microphone, "Собеседники" = loopback), merged by time.

Pure functions (mix, assemble, split_points, merge_segments, render_*) are testable with synthetic audio; Recorder needs Windows audio.
Loopback uses PyAudioWPatch (no virtual driver). Record only with the consent of everybody who is heard."""
import threading, time
from math import gcd
from pathlib import Path

import numpy as np

SR = 16000
ME, OTHERS = 'Я', 'Собеседники'


# ---------------------------------------------------------------- pure helpers
def to_mono_16k(block, channels, rate):
    """Interleaved float block in [-1, 1] -> float32 mono at 16 kHz."""
    a = np.asarray(block, dtype=np.float32)
    if channels > 1: a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != SR:
        from scipy.signal import resample_poly
        g = gcd(int(rate), SR); a = resample_poly(a, SR // g, int(rate) // g).astype(np.float32)
    return a


def assemble(blocks, t0, total_s):
    """blocks: [(arrival time of the block END, float32 mono 16k)] -> one array of total_s seconds.
    WASAPI loopback delivers nothing while the PC is silent, so each block is placed by the wall clock and gaps stay silent."""
    out = np.zeros(int(total_s * SR) + SR, dtype=np.float32); cursor = 0
    for t_end, a in blocks:
        pos = max(cursor, int((t_end - t0) * SR) - len(a))
        end = min(len(out), pos + len(a))
        if end > pos: out[pos:end] = a[:end - pos]
        cursor = end
    return out[: int(total_s * SR)]


def mix(a, b):
    """Two tracks -> one, no clipping (each at 0.7, hard limit at 1)."""
    n = max(len(a), len(b)); m = np.zeros(n, dtype=np.float32)
    m[:len(a)] += a * 0.7; m[:len(b)] += b * 0.7
    return np.clip(m, -1.0, 1.0)


def rms(a): return float(np.sqrt(np.mean(np.square(a)))) if len(a) else 0.0


def split_points(audio, chunk_s=300.0, search_s=10.0):
    """[(start, end)] sample ranges of about chunk_s each; every cut moves to the quietest 0.2 s window in the last search_s seconds."""
    n = len(audio); size = int(chunk_s * SR); out = []; a = 0
    while a < n:
        b = min(n, a + size)
        if b < n:
            lo = max(a + 1, b - int(search_s * SR)); win = int(0.2 * SR)
            best, best_e = b, None
            for s in range(lo, b - win, win // 2):
                e = rms(audio[s:s + win])
                if best_e is None or e < best_e: best_e, best = e, s + win // 2
            b = best
        out.append((a, b)); a = b
    return out


def merge_segments(me, others):
    """Each list: [(t0, t1, text)] -> [{'t0','t1','who','text'}] ordered by time; same-start ties keep 'Я' first."""
    allseg = [dict(t0=s, t1=e, who=ME, text=t) for s, e, t in me] + [dict(t0=s, t1=e, who=OTHERS, text=t) for s, e, t in others]
    return sorted((x for x in allseg if x['text'].strip()), key=lambda x: (round(x['t0'], 2), x['who'] != ME))


def fmt_ts(sec):
    sec = int(max(0, sec)); return '%02d:%02d:%02d' % (sec // 3600, sec % 3600 // 60, sec % 60)


def render_txt(segs):
    return '\n'.join('[%s] %s: %s' % (fmt_ts(s['t0']), s['who'], s['text'].strip()) for s in segs) + '\n'


def render_md(segs, started, duration, files):
    head = ['# Расшифровка встречи', '', '- Дата: %s' % started, '- Длительность: %s' % fmt_ts(duration),
            '- Источники: «%s» микрофон, «%s» звук с компьютера (loopback)' % (ME, OTHERS), '- Файлы: ' + ', '.join(files), '']
    body = ['**[%s] %s:** %s' % (fmt_ts(s['t0']), s['who'], s['text'].strip()) for s in segs] or ['_Речь не обнаружена._']
    return '\n'.join(head + body) + '\n'


def transcribe_track(transcribe, audio, progress=None, chunk_s=300.0, silent_rms=0.002):
    """transcribe(chunk float32) -> [(start, end, text)] relative to the chunk. -> the same with absolute times.
    Silent chunks are skipped; progress(done_samples) is called after each chunk."""
    out = []
    for a, b in split_points(audio, chunk_s):
        if rms(audio[a:b]) >= silent_rms:
            for s, e, t in transcribe(audio[a:b]): out.append((a / SR + s, a / SR + e, t))
        if progress: progress(b)
    return out


def save_tracks(folder, mic, others):
    """mic.wav + others.wav (16 kHz mono) and mixed.flac. -> list of file names."""
    import soundfile as sf
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    sf.write(str(folder / 'mic.wav'), mic, SR, subtype='PCM_16')
    sf.write(str(folder / 'others.wav'), others, SR, subtype='PCM_16')
    sf.write(str(folder / 'mixed.flac'), mix(mic, others), SR)
    return ['mic.wav', 'others.wav', 'mixed.flac']


# ---------------------------------------------------------------- recorder (Windows audio)
class Recorder:
    def __init__(self, mic_device=None, log=print):
        self.mic_device, self.log = mic_device, log
        self.mic, self.loop = [], []
        self.t0 = 0.0; self.running = False; self.loopback_ok = False; self.error = ''
        self._threads = []

    def start(self):
        self.t0 = time.time(); self.running = True
        t = threading.Thread(target=self._mic, daemon=True); t.start(); self._threads.append(t)
        t = threading.Thread(target=self._loopback, daemon=True); t.start(); self._threads.append(t)
        time.sleep(0.8)

    def _mic(self):
        import sounddevice as sd
        def cb(indata, frames, t, status): self.mic.append((time.time(), indata[:, 0].astype(np.float32).copy() / 32768.0))
        try:
            with sd.InputStream(samplerate=SR, channels=1, dtype='int16', blocksize=1024, device=self.mic_device, callback=cb):
                while self.running: time.sleep(0.1)
        except Exception as e: self.error = 'микрофон: %s' % e; self.log('[meeting] mic failed', e)

    def _loopback(self):
        try: import pyaudiowpatch as pa
        except ImportError: self.log('[meeting] pyaudiowpatch is not installed: no PC audio'); self.error = self.error or 'нет pyaudiowpatch'; return
        p = pa.PyAudio()
        try:
            dev = p.get_default_wasapi_loopback(); ch, rate = int(dev['maxInputChannels']), int(dev['defaultSampleRate'])
            def cb(data, frames, ti, status):
                self.loop.append((time.time(), to_mono_16k(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0, ch, rate))); return (None, pa.paContinue)
            st = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True, input_device_index=dev['index'], frames_per_buffer=1024, stream_callback=cb)
            self.loopback_ok = True; self.log('[meeting] loopback:', dev['name'], rate, ch)
            while self.running: time.sleep(0.1)
            st.stop_stream(); st.close()
        except Exception as e: self.error = self.error or 'звук ПК: %s' % e; self.log('[meeting] loopback failed', e)
        finally: p.terminate()

    def stop(self):
        """-> (mic, others, duration seconds): two float32 16 kHz arrays aligned by the wall clock."""
        self.running = False
        for t in self._threads: t.join(timeout=3)
        dur = max(0.1, time.time() - self.t0)
        return assemble(self.mic, self.t0, dur), assemble(self.loop, self.t0, dur), dur
