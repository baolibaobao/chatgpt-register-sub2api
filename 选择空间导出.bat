@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: 选择空间导出.bat WORKSPACE_ID [INPUT_JSON] [OUTPUT_JSON] [--relogin]
  echo Example: 选择空间导出.bat your-workspace-uuid registered_accounts-your-batch.json sub2api-your-workspace.json
  pause
  exit /b 1
)

set WORKSPACE_ID=%~1
set INPUT_JSON=%~2
set OUTPUT_JSON=%~3
set EXTRA=%~4

if "%INPUT_JSON%"=="" set INPUT_JSON=registered_accounts.json
if "%OUTPUT_JSON%"=="" set OUTPUT_JSON=sub2api-workspace-%WORKSPACE_ID:~0,8%.json

".venv\Scripts\python.exe" "scripts\select_workspace_export.py" --workspace-id "%WORKSPACE_ID%" --input "%INPUT_JSON%" --output "%OUTPUT_JSON%" %EXTRA%

echo.
pause