@echo off
title Yuyi Local Agent

cd /d "%~dp0"

echo ========================================
echo   Yuyi Local Agent Launcher
echo ========================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found in PATH
    echo Please install Python 3.11+ and check "Add to PATH"
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [INFO] Python path:
where python
echo.

if not exist "local_agent\config.yaml" (
    echo [WARN] config.yaml not found
    if exist "local_agent\config.yaml.example" (
        echo [INFO] Copying default config...
        copy "local_agent\config.yaml.example" "local_agent\config.yaml"
        echo [WARN] Please edit local_agent\config.yaml first
        echo.
        pause
        exit /b 1
    ) else (
        echo [ERROR] config.yaml.example not found
        echo.
        pause
        exit /b 1
    )
)

echo [INFO] Installing dependencies...
call python -m pip install -r local_agent\requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [WARN] Dependencies install failed, trying to start anyway...
    echo.
)

echo.
echo [INFO] Starting local agent...
echo ========================================
echo.
call python local_agent\agent.py

echo.
echo ========================================
echo [INFO] Agent stopped. Press any key to close.
pause >nul
