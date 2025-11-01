@echo off
cd /d "%~dp0"
python -m PyInstaller --onefile --name "cleanup_windows" --console --clean cleanup_windows.py
pause

