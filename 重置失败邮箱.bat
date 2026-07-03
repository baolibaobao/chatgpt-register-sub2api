@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo  Reset failed Outlook mailboxes only
echo  Dir: %cd%
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please run start.bat once first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\reset_failed_mailboxes.py"

echo.
echo Done.
pause
exit /b 0