<div align="center">

# 🦀 voxcrab

**A voice mascot for AI coding agents: talk to [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions, hear short summaries and answer questions out loud, while a pixel crab (or a Live2D / VRM model) lives on your desktop.**

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![Claude Code](https://img.shields.io/badge/Claude_Code-hooks-6E56CF)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-34c759)

**English** · [Русский](./README.ru.md)

![characters](docs/media/characters.gif)

</div>

> Unofficial, not affiliated with Anthropic. Speech runs on your machine (Vosk wake word, faster-whisper, Silero voice); only the task text goes to Claude Code.

## Why

Agent sessions finish, ask for permission or need a clarification while you are looking at another window. `voxcrab` sits on top of your screen, tells you what happened in one short sentence, asks the question out loud and sends your spoken answer straight back into the right session. You can also start a new task by voice: "Клод, fix the failing tests in project X".

- 🎙️ **Hands-free**: wake word "Клод" (Vosk) or a push-to-talk hotkey, silence detection (Silero VAD), faster-whisper recognition
- 🔊 **Talks back**: Silero TTS (Russian) with an edge-tts fallback; numbers, versions and Latin words are spelled out before synthesis so nothing is skipped
- 🧩 **Plugs into Claude Code**: hooks from VS Code and CLI sessions. `Stop` becomes a short summary, permission prompts and `AskUserQuestion` become spoken questions
- 🧭 **Picks the session by meaning**: "in the crab project, add a test" goes to the matching live session, or starts a new one
- 🐾 **Characters**: built-in pixel crab, cats, girls and a robot; your own GIF / PNG / sprite sheets; **Live2D** (Cubism 3-5) and **VRM** (0.x / 1.0) models in a transparent always-on-top window; desktop-pet mode
- 🎮 **Game friendly**: never injects into other processes; hides itself in exclusive fullscreen, stays on top of borderless windows

## How it works

```
 you ── voice ──▶  wake word / hotkey ─▶ VAD ─▶ Whisper ─▶ intent (new task / answer / "repeat" / "stop")
                                                              │
   Claude Code session(s) ◀── messaging socket ◀── vscode_bridge sidecar (127.0.0.1, token) ◀──┘
        │  hooks: SessionStart · Stop · PreToolUse(AskUserQuestion) · PermissionRequest
        ▼
   short summary / question ─▶ Silero TTS ─▶ speech bubble + mouth movement on the mascot
```

The widget starts a small loopback-only sidecar. Claude Code hooks (`vscode_bridge/hook.py`, see [`vscode_bridge/INSTALL.md`](vscode_bridge/INSTALL.md)) post session events to it.
Session tokens stay in `~/.voice-bridge` (per-user ACL) and are never logged. A voice task is routed with `claude -p --model haiku` to a live session, or opens a new headless session listed in VS Code.

## Requirements

- **Windows 10 / 11** (see [Platforms](#platforms))
- Python 3.12 and a microphone
- Claude Code CLI (and optionally the VS Code extension)
- about 3 GB of disk for models on first run; an NVIDIA GPU makes recognition faster but is optional
- Live2D / VRM mode only: Microsoft Edge WebView2 runtime (already on Windows 11)

## Quick start (Windows)

```bash
git clone https://github.com/skiddgoddamn/voxcrab.git
cd voxcrab
run.cmd            # first run: creates .venv, installs dependencies; models download on first start
```

1. Models (Whisper, Vosk wake word, Silero voice) are downloaded into `models/` the first time you run. They are **not** part of the repository.
2. Add the hooks to `~/.claude/settings.json`: `python vscode_bridge/gen_settings.py` prints the block.
3. Say **"Клод, привет"** or hold `Ctrl+Alt+Space` and talk.
4. Right-click the mascot, then **Settings** (voice, hotkeys, characters, engines, window mode).

Self-check: `python smoke.py` (TTS, wav, STT, wake word grammar, VAD). Tests: `python -m pytest tests_engines tests_settings tests_bridge`.

## Windows build

Tagged releases (`v*`) are built by GitHub Actions ([`.github/workflows/build.yml`](.github/workflows/build.yml)): a zip of the onedir app and an Inno Setup installer.
Local build: `pip install pyinstaller && python -m PyInstaller --noconfirm voice-widget.spec` gives `dist/voice-widget/voice-widget.exe` (about 830 MB unpacked, mostly torch).
Models are **not** bundled: they download on the first start into `%LOCALAPPDATA%\voice-widget\models`, settings and `widget.log` live there too. The packaged build uses CPU recognition (CUDA libraries are left out; use a source install for the GPU).
Claude Code hooks call `python vscode_bridge/hook.py` (standard library only), so the hooks need a system Python.

## Voice commands

| You say | What happens |
|---|---|
| "Клод, …task…" | routed to the best matching live session, or a new one |
| "Клод, что там" / "что нового" | speaks the queued messages |
| "повтори", "ещё раз", "что?", "не расслышал" | repeats the last spoken text (a pending question first) |
| "да" / "нет" / an option name | answers a permission prompt or `AskUserQuestion` |
| "в новой сессии …" / "в проекте X …" | starts a new session (default or named project) |
| `Ctrl+Alt+X` | stops speech and listening, drops the pending question |
| `Ctrl+Alt+H` | show / hide the mascot |

The wake word and recognition language are Russian today; the dialog grammar lives in `dialog_state.py` and `bridge_voice.py`.

## Characters & engines

| Kind | What | Where |
|---|---|---|
| Built-in pixel | crab, cat, robot, three girls, three cats, drawn in code (MIT) | [`characters.py`](characters.py) |
| Your art | GIF / PNG frames / sprite sheets per emotion (idle, listening, thinking, speaking, success, error, question, confirm, sleep) | [`characters/README.md`](characters/README.md) |
| Live2D | Cubism 3, 4 and 5 `.model3.json`, expressions, motions, blink, mouth from the TTS audio level | [`engines/README.md`](engines/README.md) |
| VRM | 0.x and 1.0, expressions, blink, visemes, procedural body motion | [`engines/README.md`](engines/README.md) |

![emotions](docs/media/emotions.png)

Importing a model validates it, shows its license, previews it and stores it in `%APPDATA%\voice-widget\characters`. Any renderer problem falls back to the standard character with a message in the bubble.

**Not included, you provide them:** the proprietary **Live2D Cubism Core** (`live2dcubismcore.min.js`, download it from live2d.com and set the path in Settings) and every Live2D / VRM model. No third-party model or Core is shipped here. See [THIRD_PARTY.md](THIRD_PARTY.md).

## Configuration

`config.json` is created on the first change in Settings (it is git-ignored). [`config.example.json`](config.example.json) lists every key with its default.
Main keys: `hotkey`, `wake_word_enabled`, `speak_mode` (`immediate` or `on_request`), `whisper_model` (`auto`), `tts_speaker`, `tts_rate`, `mic_device`, `confirm_mode`, `bridge_enabled`, `bridge_hold`, `default_project_dir`, `character`, `window_mode` (normal or pet), `fullscreen_policy`, `click_through`, `render_fps`, `live2d_core_path`.

## Platforms

| OS | Status |
|---|---|
| Windows 10 / 11 | ✅ supported and tested |
| macOS, Linux | ❌ not yet |

The core logic (dialog, bridge, TTS text normalisation, character engine) is plain Python. These parts are Windows-only today: the `keyboard` global hotkeys, tkinter colour-key transparency, `ctypes` Win32 calls (`winmode.py`: fullscreen detection, always-on-top, work area), per-pixel transparency with `DwmExtendFrameIntoClientArea` and WebView2 in `engines/web_child.py`, the `pythonw` launcher and the ACL handling of the bridge folder. A port needs a replacement for each of them.

## Limitations

- Exclusive (true) fullscreen DirectX games hide any overlay; use borderless windowed mode.
- Wake word and speech are Russian-first. The "Клод" wake word can false-trigger from speakers; headphones are better.
- The Silero voices are licensed **CC BY-NC-SA 4.0 (non-commercial)** and are downloaded at runtime, not bundled. Use edge-tts or your own voice for commercial use.
- DPI was verified on a 125 % monitor, not on 150 %.

## License

MIT, see [LICENSE](LICENSE). Vendored JS (PixiJS, pixi-live2d-display, three.js, three-vrm) is MIT; downloaded models and Python dependencies keep their own licenses, see [THIRD_PARTY.md](THIRD_PARTY.md).
