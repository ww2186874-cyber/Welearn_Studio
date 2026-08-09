@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo WeLearn Studio environment is not installed.
  echo Create .venv and install the project first.
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m welearn_studio.app
