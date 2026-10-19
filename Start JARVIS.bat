@echo off
setlocal EnableExtensions DisableDelayedExpansion
title JARVIS
pushd "%~dp0"
if errorlevel 1 goto :folder_failed
if not exist "run.py" goto :not_extracted
if not exist "requirements.txt" goto :not_extracted
if not exist "constraints.txt" goto :not_extracted

echo JARVIS - checking your installation...
if exist ".venv\Scripts\python.exe" goto :check_environment

rem Accept either Python's launcher or an interpreter on PATH.
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto :use_py
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto :use_python
python3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto :use_python3
goto :missing_python

:use_py
set "JARVIS_PYTHON=py -3"
goto :create_environment
:use_python
set "JARVIS_PYTHON=python"
goto :create_environment
:use_python3
set "JARVIS_PYTHON=python3"

:create_environment
echo Creating the JARVIS Python environment...
%JARVIS_PYTHON% -m venv ".venv"
if errorlevel 1 goto :environment_failed

:check_environment
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"
if errorlevel 1 goto :environment_failed
if not exist ".jarvis-data" mkdir ".jarvis-data"
if not exist ".jarvis-data" goto :folder_failed
echo Installing dependencies. The first run needs Internet access and may take a few minutes.
echo Setup output is saved to .jarvis-data\setup.log
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt > ".jarvis-data\setup.log" 2>&1
if errorlevel 1 goto :install_failed
if /i "%~1"=="--setup-only" goto :setup_complete
echo.
echo JARVIS is starting. Keep this window open for reminders. Press Ctrl+C to stop.
".venv\Scripts\python.exe" run.py %*
set "JARVIS_EXIT_CODE=%errorlevel%"
if not "%JARVIS_EXIT_CODE%"=="0" goto :application_failed
popd
exit /b 0

:setup_complete
echo JARVIS dependencies are installed.
popd
exit /b 0

:not_extracted
echo.
echo JARVIS files are missing from this folder.
echo Right-click the downloaded ZIP, choose Extract All, and open the extracted folder.
echo Run Start JARVIS.bat from there, alongside run.py and requirements.txt.
goto :failure

:missing_python
echo.
echo No working Python 3.11 or newer installation was found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable Add Python to PATH, finish installation, then reopen this launcher.
goto :failure

:environment_failed
echo.
echo The local Python environment could not be created or started.
echo Check the error above. If you installed a newer Python, rename the .venv folder
echo in this JARVIS folder to .venv-old, then try again.
echo Your assistant's saved memory is stored separately and will not be removed.
goto :failure

:install_failed
echo.
echo Dependency installation failed. Here is the setup output:
type ".jarvis-data\setup.log"
echo.
echo Copy the error above or open .jarvis-data\setup.log for troubleshooting.
goto :failure

:application_failed
echo.
echo JARVIS could not start or stopped unexpectedly. Copy the error above for troubleshooting.
echo If port 8765 is already in use, close any other JARVIS window before trying again.
goto :failure

:folder_failed
echo.
echo This folder could not be opened or written to.
echo Extract the complete ZIP to a folder you own, such as Downloads or Documents.

:failure
echo.
echo JARVIS has stopped. This window will stay open so you can read the error.
if not defined JARVIS_NO_PAUSE pause
popd
exit /b 1
