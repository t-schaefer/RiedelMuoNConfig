@echo off
rem Double-click: installs the MuoN Config Tool as a background service
rem (scheduled task as SYSTEM, starts at boot, reachable from the network
rem on port 8091 - same setup as the Fusion dashboard on 8090).
rem Asks for administrator rights (UAC) itself.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - approve the prompt that appears...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0config-tool\install-task-windows.ps1"
echo.
pause
