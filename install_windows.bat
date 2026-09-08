@echo off
setlocal
cd /d "%~dp0"

echo Creando entorno virtual...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto :error

echo Instalando dependencias...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Instalando Chromium para Playwright...
call ".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Instalacion completada. Usa wordpress_sync.bat para ejecutar la herramienta.
pause
exit /b 0

:error
echo.
echo La instalacion no se pudo completar. Revisa el mensaje anterior.
pause
exit /b 1
