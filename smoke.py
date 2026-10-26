"""Smoke: TTS -> wav, STT reads it back, wake grammar loads, VAD runs."""
import sys, wave
import numpy as np
import widget

cfg = widget.load_config()
tts = widget.TTS(cfg)
audio, sr = tts.synth('Привет, я готов к работе.')
pcm = (audio * 32767).astype('int16')
with wave.open('smoke.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
print('tts ok', len(audio) / sr, 's', 'silero' if tts.model is not None else 'edge')
idx = np.arange(0, len(audio), sr / widget.SR).astype(int)
a16 = audio[idx[idx < len(audio)]]
stt = widget.STT(cfg)
print('stt', stt.name, stt.device, '->', stt(a16))
rec = widget.make_wake(); print('wake grammar ok')
vad, _ = widget.make_vad()
print('vad speech prob', max(vad((a16[i:i + 512] * 32767).astype('int16')) for i in range(0, len(a16) - 512, 512)))
