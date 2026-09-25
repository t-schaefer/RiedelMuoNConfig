@echo off
rem Sets (or changes) the login password of the MuoN Config Tool.
rem Stored only as a salted PBKDF2 hash in config-tool\auth.json (not in git).
rem Takes effect for new logins immediately, no restart needed.
cd /d "%~dp0config-tool"
python server.py --set-password
echo.
pause
