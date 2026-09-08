@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Primero ejecuta install_windows.bat.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" wordpress_sync.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
