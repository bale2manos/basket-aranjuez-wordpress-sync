@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Primero ejecuta install_windows.bat.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto :error
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
call ".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error
call ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --name BasketWordPressSync --paths src wordpress_sync.py
if errorlevel 1 goto :error

if exist "dist\BasketWordPressSync\config" rmdir /s /q "dist\BasketWordPressSync\config"
xcopy /e /i /y "config" "dist\BasketWordPressSync\config" >nul
copy /y "config.py" "dist\BasketWordPressSync\config.py" >nul
if exist "resultados\fotos\basket_aranjuez.png" (
  if not exist "dist\BasketWordPressSync\resultados\fotos" mkdir "dist\BasketWordPressSync\resultados\fotos"
  copy /y "resultados\fotos\basket_aranjuez.png" "dist\BasketWordPressSync\resultados\fotos\basket_aranjuez.png" >nul
)
if exist "browsers" xcopy /e /i /y "browsers" "dist\BasketWordPressSync\browsers" >nul

echo.
echo Ejecutable creado en dist\BasketWordPressSync\BasketWordPressSync.exe
echo Al abrirlo se mostrara el asistente grafico.
pause
exit /b 0

:error
echo.
echo No se pudo crear el ejecutable.
pause
exit /b 1
