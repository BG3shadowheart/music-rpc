@echo off
setlocal
cd /d "%~dp0"
if not exist dist-installer\Spotify.exe (
  echo dist-installer\Spotify.exe installer was not found. Run build.bat first.
  exit /b 1
)
echo Starting Spotify Setup. Choose the installation folder and startup option.
start /wait "" "%~dp0dist-installer\Spotify.exe"
if errorlevel 1 (
  echo Installation did not complete. Approve the Windows UAC prompt and try again.
  exit /b 1
)
echo Setup finished.
endlocal
