@echo off
setlocal
cd /d "%~dp0"
set "BOOTSTRAP_PYTHON="
set "BOOTSTRAP_ARGS="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "BOOTSTRAP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined BOOTSTRAP_PYTHON where py >nul 2>nul && (set "BOOTSTRAP_PYTHON=py"& set "BOOTSTRAP_ARGS=-3")
if not defined BOOTSTRAP_PYTHON where python >nul 2>nul && set "BOOTSTRAP_PYTHON=python"
if not defined BOOTSTRAP_PYTHON (echo Python 3.10 or newer is required. Install Python from python.org.& exit /b 1)
if not exist .venv\Scripts\python.exe call "%BOOTSTRAP_PYTHON%" %BOOTSTRAP_ARGS% -m venv .venv
if not exist tools mkdir tools
if not exist tools\cloudflared.exe powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'tools\cloudflared.exe'"
if not exist tools\cloudflared.exe (echo Could not download Cloudflare Tunnel helper.& exit /b 1)
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if exist dist-zig rmdir /s /q dist-zig
call .venv\Scripts\python.exe -m nuitka --zig --mode=onefile --windows-console-mode=disable --windows-icon-from-ico=assets\spotify.ico --company-name=Spotify --product-name=Spotify --file-description=Spotify --file-version=2.2.0.0 --product-version=2.2.0.0 --include-package=winrt --include-package=pypresence --include-module=fps_boost --include-data-files=tools\cloudflared.exe=cloudflared.exe --output-dir=dist-zig --output-filename=Spotify-2.2.0.exe --assume-yes-for-downloads app.py
if errorlevel 1 exit /b 1
set "ISCC="
where ISCC.exe >nul 2>nul && set "ISCC=ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (echo Inno Setup 6 is required to build the installer.& exit /b 1)
call "%ISCC%" installer\Spotify.iss
if errorlevel 1 exit /b 1
echo App build complete: %CD%\dist-zig\Spotify-2.2.0.exe
echo Installer build complete: %CD%\dist-installer\Spotify.exe
