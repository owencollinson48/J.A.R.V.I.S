@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install Python from python.org, then try again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failure
)
.venv\Scripts\python.exe -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'"
if errorlevel 1 goto :failure
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failure
echo.
echo JARVIS is starting. Keep this window open for reminders. Press Ctrl+C to stop.
.venv\Scripts\python.exe run.py
exit /b %errorlevel%
:failure
echo.
echo Setup could not finish. Read the error above, then try again.
pause
exit /b 1
