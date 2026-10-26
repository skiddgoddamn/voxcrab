"""Where things live. Source checkout: next to the code (as before). Frozen build (PyInstaller): read-only resources inside the app folder,
writable data (models, config.json, widget.log) in %LOCALAPPDATA%\voice-widget so the app also works when installed under Program Files."""
import os, sys
from pathlib import Path

FROZEN = bool(getattr(sys, 'frozen', False))
HERE = Path(getattr(sys, '_MEIPASS', None) or Path(__file__).resolve().parent)        # code and bundled resources
DATA = (Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'voice-widget') if FROZEN else HERE   # writable
if FROZEN: DATA.mkdir(parents=True, exist_ok=True)
