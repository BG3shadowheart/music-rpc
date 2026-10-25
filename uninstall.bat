@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $app=Join-Path $env:LOCALAPPDATA 'Programs\Spotify\Spotify.exe'; if (Test-Path -LiteralPath $app) { Start-Process -FilePath $app -ArgumentList '--uninstall' -Wait }; Start-Sleep -Milliseconds 900; Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Spotify') -Recurse -Force; Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'SpotifyPresence') -Recurse -Force; Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'YumeTune') -Recurse -Force; Write-Host 'Spotify music presence removed (startup entries, browser host registrations, app data).'"
pause
