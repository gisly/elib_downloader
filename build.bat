@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Building elib_downloader
echo ============================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

"%PYTHON%" -m PyInstaller elib_downloader_gui.spec --clean --noconfirm
if errorlevel 1 goto :failed

echo.
echo Finished: dist\elib_downloader.exe
echo Build complete.
pause
exit /b 0

:failed
echo Build failed. See the error message above.
pause
exit /b 1
