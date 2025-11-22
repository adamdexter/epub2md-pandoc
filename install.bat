@echo off
REM EPUB to Markdown Converter - Windows Installer Script
REM Automatically installs all dependencies for the converter

echo ==========================================
echo EPUB to Markdown Converter - Installer
echo ==========================================
echo.

REM Check Python installation
echo Checking Python installation...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Python is installed
    python --version
) else (
    echo [ERROR] Python is not installed
    echo.
    echo Please install Python 3 from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)

echo.

REM Check Pandoc installation
echo Checking Pandoc installation...
pandoc --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Pandoc is installed
    for /f "tokens=2" %%i in ('pandoc --version ^| findstr /C:"pandoc"') do echo Version: %%i
) else (
    echo [ERROR] Pandoc is not installed
    echo.
    echo Please install Pandoc from: https://pandoc.org/installing.html
    echo Download the Windows installer and run it.
    echo.
    pause
    exit /b 1
)

echo.

REM Install Flask
echo Installing Flask for GUI...
python -m pip install --user flask >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Flask installed successfully
) else (
    echo [WARNING] Flask installation may have failed
    echo You can try installing it manually with: pip install flask
)

echo.
echo ==========================================
echo Installation complete!
echo ==========================================
echo.
echo You can now use the converter in two ways:
echo.
echo 1. Command line:
echo    python epub_to_md_converter.py C:\path\to\epub\folder
echo.
echo 2. GUI (recommended):
echo    python gui.py
echo    Then open http://localhost:5000 in your browser
echo.
pause
