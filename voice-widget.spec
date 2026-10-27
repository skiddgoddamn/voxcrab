# PyInstaller spec: onedir, windowed. Build: python -m PyInstaller --noconfirm voice-widget.spec  ->  dist/voice-widget/voice-widget.exe
# Models (Whisper, Vosk, Silero) are NOT bundled: they download into %LOCALAPPDATA%\voice-widget\models on first start.
# NVIDIA CUDA libraries are excluded too (CPU recognition in the packaged build; GPU speed-up is for source installs).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hidden = [], [], []
for pkg in ('faster_whisper', 'ctranslate2', 'silero_vad', 'vosk', 'webview', 'pystray', 'edge_tts', 'tokenizers', 'onnxruntime', 'pyaudiowpatch'):
    d, b, h = collect_all(pkg); datas += d; binaries += b; hidden += h
hidden += collect_submodules('engines') + collect_submodules('vscode_bridge') + ['pystray._win32', 'PIL._tkinter_finder', 'sounddevice', 'soundfile', 'keyboard']

datas += [
    ('engines/web/index.html', 'engines/web'), ('engines/web/host.js', 'engines/web'), ('engines/web/vendor', 'engines/web/vendor'),
    ('characters', 'characters'), ('assets', 'assets'), ('vscode_bridge', 'vscode_bridge'),
    ('config.example.json', '.'), ('LICENSE', '.'), ('THIRD_PARTY.md', '.'), ('README.md', '.'),
]
excludes = ['matplotlib', 'IPython', 'notebook', 'pytest', 'tests_engines', 'tests_bridge', 'tests_settings', 'torchvision', 'torchaudio']

a = Analysis(['widget.py'], pathex=['.'], binaries=binaries, datas=datas, hiddenimports=hidden, excludes=excludes, noarchive=False)
# the CUDA wheels are huge and optional
a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in ('cublas', 'cudnn', 'nvrtc', 'cudart'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='voice-widget', console=False, icon='assets/icons/icon.ico', upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='voice-widget', upx=False)
