@echo off
rem Double-click: git pull + restart of the MuoN Config Tool service.
rem Asks for administrator rights (UAC) itself.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - approve the prompt that appears...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-windows.ps1"
echo.
pause
