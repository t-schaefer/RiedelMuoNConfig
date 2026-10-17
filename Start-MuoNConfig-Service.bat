@echo off
rem Double-click: starts the installed MuoN Config Tool service (scheduled
rem task MuoNConfigToolService). Asks for administrator rights (UAC) itself.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - approve the prompt that appears...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service-control.ps1" -Action Start
echo.
pause
