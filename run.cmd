@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
rem no console window: pythonw, all output goes to widget.log
start "" .venv\Scripts\pythonw.exe widget.py
