@echo off
setlocal
cd /d "%~dp0"
set "BOOTSTRAP_PYTHON="
where py >nul 2>nul && set "BOOTSTRAP_PYTHON=py -3"
if not defined BOOTSTRAP_PYTHON where python >nul 2>nul && set "BOOTSTRAP_PYTHON=python"
if not defined BOOTSTRAP_PYTHON (echo Python 3.10 or newer is required. Install Python from python.org.& exit /b 1)
if not exist .venv\Scripts\python.exe %BOOTSTRAP_PYTHON% -m venv .venv
if not exist tools mkdir tools
if not exist tools\cloudflared.exe powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'tools\cloudflared.exe'"
if not exist tools\cloudflared.exe (echo Could not download Cloudflare Tunnel helper.& exit /b 1)
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if exist dist-zig rmdir /s /q dist-zig
call .venv\Scripts\python.exe -m nuitka --zig --mode=onefile --windows-console-mode=disable --windows-icon-from-ico=assets\spotify.ico --company-name=Spotify --product-name=Spotify "--file-description=Spotify music presence" --file-version=2.0.0.0 --product-version=2.0.0.0 --include-package=winrt --include-package=pypresence --include-data-files=tools\cloudflared.exe=cloudflared.exe --output-dir=dist-zig --output-filename=Spotify.exe --assume-yes-for-downloads app.py
if errorlevel 1 exit /b 1
copy /y dist-zig\Spotify.exe Spotify.exe >nul
if errorlevel 1 exit /b 1
echo Nuitka build complete: %CD%\Spotify.exe
