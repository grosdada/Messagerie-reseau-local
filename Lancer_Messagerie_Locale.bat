@echo off
setlocal
cd /d "%~dp0"

set "PY="
set "PYARG="
if exist "python_path.txt" (
  set /p CAND=<python_path.txt
  if defined CAND call :probe "%CAND%"
)
if not defined PY call :probe "py" "-3"
if not defined PY call :probe "python"
if not defined PY (
  echo Python 3 est introuvable. Installez Python, ou renseignez python_path.txt.
  pause
  exit /b 1
)
"%PY%" %PYARG% lan_messenger.py
pause
exit /b

:probe
"%~1" %~2 -c "import sys; assert sys.version_info[0] == 3" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%~1"
set "PYARG=%~2"
exit /b 0
