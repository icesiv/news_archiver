@echo off
setlocal enabledelayedexpansion

echo === News Archiver Windows Build ===

if not exist ".venv-win" (
    echo Creating Windows virtual environment...
    python -m venv .venv-win
    if errorlevel 1 exit /b 1
)

call .venv-win\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

rem Optional: pass --bundle-chromium to embed Chromium inside the installer for 100%% offline PCs
if /I "%~1"=="--bundle-chromium" (
    echo Installing Playwright Chromium for offline bundling...
    set "PLAYWRIGHT_BROWSERS_PATH=%CD%\build-browsers"
    playwright install chromium
    if errorlevel 1 exit /b 1
    set "NEWS_ARCHIVER_PLAYWRIGHT_DIR=%CD%\build-browsers"
)

echo Building PyInstaller bundle...
pyinstaller --clean --noconfirm news_archiver.spec
if errorlevel 1 exit /b 1

set "ISCC_EXE="
where ISCC.exe >nul 2>&1
if %errorlevel%==0 (
    set "ISCC_EXE=ISCC.exe"
) else if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
) else if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)

if defined ISCC_EXE (
    echo Building Inno Setup installer...
    "%ISCC_EXE%" installer\news_archiver.iss
    if errorlevel 1 exit /b 1
    echo Installer ready at: dist\installer\NewsArchiver-Setup.exe
) else (
    echo Inno Setup ^(ISCC.exe^) not found; skipping installer build.
    echo Portable app folder is ready at: dist\NewsArchiver\NewsArchiver.exe
)

echo Done!
endlocal
pause
