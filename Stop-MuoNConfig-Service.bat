@echo off
rem Double-click: stops the running MuoN Config Tool service. The scheduled
rem task stays installed, so the tool starts again at the next boot or with
rem Start-MuoNConfig-Service.bat. Asks for administrator rights (UAC) itself.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - approve the prompt that appears...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo Stopping the tool interrupts an "Apply" that is still running.
echo Make sure no apply is in progress.
choice /c YN /m "Stop the MuoN Config Tool now"
if errorlevel 2 exit /b

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service-control.ps1" -Action Stop
echo.
pause
