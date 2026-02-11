@echo off
echo ============================================
echo   Building elib_downloader executable...
echo ============================================
echo.

REM Activate virtualenv if present
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Install dependencies
pip install -r requirements.txt

REM Build with PyInstaller
pyinstaller elib_downloader_gui.spec --clean

echo.
echo ============================================
echo   Done! Check the dist\ folder.
echo ============================================
pause
