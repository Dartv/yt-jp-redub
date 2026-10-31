@echo off
rem Windows: double-click to start yt-jp-redub and open the library in the browser.
rem The first time, it installs everything first (install.ps1).
cd /d "%~dp0"
if not exist .venv\Scripts\yt-jp-redub.exe goto install
if not exist third_party\index-tts\checkpoints\gpt.pth goto install
goto run
:install
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
if errorlevel 1 goto failed
:run
rem uv also brings the dependencies up to date after an update; without it on PATH, the venv runs as is
where uv >nul 2>nul
if errorlevel 1 (.venv\Scripts\yt-jp-redub.exe serve --open) else (uv run yt-jp-redub serve --open)
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo yt-jp-redub didn't start; the messages above say why.
pause
exit /b 1
