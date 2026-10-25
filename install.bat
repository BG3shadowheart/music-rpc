@echo off
setlocal
cd /d "%~dp0"
if not exist Spotify.exe (
  echo Spotify.exe was not found. Run Spotify-Setup.exe or build.bat first.
  pause
  exit /b 1
)
echo Installing to %LOCALAPPDATA%\Programs\Spotify ...
start "" "%~dp0Spotify.exe" --install
timeout /t 4 >nul
echo Done. Spotify music presence is installed, running, and set to start with Windows.
