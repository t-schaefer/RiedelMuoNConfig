@echo off
rem Starts the MuoN / Fusion config tool on http://localhost:8091/
rem Close this window to stop it.
cd /d "%~dp0config-tool"
start "" http://localhost:8091/
python server.py
pause
