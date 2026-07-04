@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set COUNT=%~1
set EMAIL=%~2
if "%COUNT%"=="" set COUNT=4

echo ========================================
echo  Split api_code mother email
echo  Count per mother: %COUNT%
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please run start.bat once first.
    pause
    exit /b 1
)

if "%EMAIL%"=="" (
    ".venv\Scripts\python.exe" "scripts\split_mother_email.py" --count %COUNT%
) else (
    ".venv\Scripts\python.exe" "scripts\split_mother_email.py" --count %COUNT% --email "%EMAIL%"
)

echo.
echo Done.
pause