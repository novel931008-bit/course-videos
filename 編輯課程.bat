@echo off
setlocal
title Course Editor - close this window to stop the editor
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "tools\editor.py" %*
) else (
  python "tools\editor.py" %*
)
if errorlevel 1 (
  echo.
  echo [ERROR] The editor could not start. Python 3 is required: https://www.python.org/downloads/
  pause
)
endlocal
