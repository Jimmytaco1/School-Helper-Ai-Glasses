@echo off
rem Starts My Notebook: one window that also watches your inbox folder.
rem Keep this file in the same folder as the notebook_*.py files.
cd /d "%~dp0"
python notebook_gui.py
if errorlevel 1 (
  echo.
  echo Something stopped the notebook. The message above says why.
  echo See TROUBLESHOOTING.md for common fixes.
  pause
)
