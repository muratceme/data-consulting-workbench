@echo off
cd /d "%~dp0"
call npm run build
if errorlevel 1 goto done
call npm start
:done
pause
