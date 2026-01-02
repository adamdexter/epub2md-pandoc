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

REM Create virtual environment and install Flask
echo Setting up virtual environment...
if exist .venv (
    echo [INFO] Virtual environment already exists
) else (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% equ 0 (
        echo [OK] Virtual environment created
    ) else (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
)

echo Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

echo Installing dependencies from requirements.txt...
if exist requirements.txt (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if %errorlevel% equ 0 (
        echo [OK] All dependencies installed successfully
    ) else (
        echo [WARN] Some dependencies failed, trying individually...
        .venv\Scripts\python.exe -m pip install flask requests trafilatura beautifulsoup4 readability-lxml
        echo [INFO] Installing Medium article support...
        .venv\Scripts\python.exe -m pip install setuptools selenium webdriver-manager undetected-chromedriver
    )
) else (
    echo [INFO] requirements.txt not found, installing core dependencies...
    .venv\Scripts\python.exe -m pip install flask requests trafilatura beautifulsoup4 readability-lxml
    echo [INFO] Installing Medium article support...
    .venv\Scripts\python.exe -m pip install setuptools selenium webdriver-manager undetected-chromedriver
)
echo [OK] Dependencies installed

REM Create GUI launcher script
echo Creating GUI launcher script...
(
echo @echo off
echo REM EPUB to Markdown Converter - GUI Launcher
echo REM This script activates the virtual environment and runs the GUI
echo.
echo if not exist .venv (
echo     echo Error: Virtual environment not found!
echo     echo Please run install.bat first
echo     pause
echo     exit /b 1
echo ^)
echo.
echo call .venv\Scripts\activate.bat
echo python gui.py
) > run_gui.bat

echo [OK] Launcher script created

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
echo    run_gui.bat
echo    Then open http://localhost:3763 in your browser
echo    (Port 3763 = 'EPMD' on phone keypad - easy to remember!)
echo.
pause
