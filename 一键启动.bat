@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM chatgpt-register-sub2api one-click launcher
REM Double click to run full pipeline with config.yaml

cd /d "%~dp0"

echo ========================================
echo  chatgpt-register-sub2api launcher
echo  Dir: %cd%
echo ========================================
echo.

if not exist "config.yaml" (
    echo [ERROR] config.yaml not found
    echo Please check: %cd%\config.yaml
    pause
    exit /b 1
)

if not exist ".venv\Scripts\chatgpt-register.exe" (
    echo [INFO] .venv not found, installing...
    echo.

    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3.12 -m venv .venv
    ) else (
        python -m venv .venv
    )

    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )

    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -e .

    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

echo [INFO] Config: %cd%\config.yaml
echo [INFO] Starting...
echo.

if "%~1"=="" (
    ".venv\Scripts\chatgpt-register.exe" run -v
) else (
    ".venv\Scripts\chatgpt-register.exe" %*
)

set EXIT_CODE=%errorlevel%
echo.
echo ========================================
if "%EXIT_CODE%"=="0" (
    echo  Done
) else (
    echo  Failed, exit code: %EXIT_CODE%
)
echo ========================================
echo.
pause
exit /b %EXIT_CODE%