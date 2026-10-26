# Third-party components and licenses

The MIT license in `LICENSE` covers this repository's own code and its built-in pixel characters (crab, cats, girls, robot).
Everything below keeps its own license.

## Vendored in the repository (`engines/web/vendor/`, license texts next to the files)

| Component | Version | License |
|---|---|---|
| PixiJS (`pixi.min.js`) | 6.5.10 | MIT |
| pixi-live2d-display (`cubism4.min.js`, Cubism 3/4/5 runtime wrapper) | 0.4.0 | MIT |
| three.js (inside `vrm.bundle.js`) | 0.180 | MIT |
| @pixiv/three-vrm (inside `vrm.bundle.js`) | 3.4.5 | MIT |

`vrm.bundle.js` is built from `engines/web/vrm_entry.js` with esbuild (`engines/web/build.mjs`).

## NOT included, supplied by the user

- **Live2D Cubism Core** (`live2dcubismcore.min.js`) is proprietary software of Live2D Inc. It is not in this repository and must never be added.
  Download it yourself from <https://www.live2d.com/en/sdk/download/web/> and set its path in Settings, tab "Characters and engines".
  Using it means accepting the Live2D license; check its terms for your use (personal / commercial).
- **Live2D and VRM models.** No third-party model ships with this project. Live2D sample models are under the Live2D Free Material License,
  every VRM has its own license (shown in the import dialog). You are responsible for the models you import.

## Downloaded at first run (never bundled)

| Model | Source | License | Note |
|---|---|---|---|
| Silero TTS `v5_ru` | models.silero.ai | **CC BY-NC-SA 4.0 (non-commercial)** | Voice output only. Do not use the Silero voices in a commercial product without a license from Silero. Alternative voice: edge-tts. |
| Vosk `vosk-model-small-ru-0.22` | alphacephei.com/vosk/models | Apache-2.0 | wake word "Клод" |
| Whisper (via faster-whisper) | Hugging Face | MIT | speech recognition |
| Silero VAD | `silero-vad` pip package | MIT | pause detection |

## Python dependencies (installed with pip, not vendored)

numpy, sounddevice, pystray, pillow, keyboard, requests, scipy, soundfile (BSD/MIT/LGPL-style permissive), vosk (Apache-2.0),
faster-whisper (MIT), torch (BSD-3), silero-vad (MIT), pywebview (BSD-3), nvidia-cublas-cu12 / nvidia-cudnn-cu12 (NVIDIA proprietary license, optional GPU speed-up),
edge-tts (LGPL-3.0; it talks to Microsoft's online read-aloud service, which is not an official API; used only as a fallback voice).
