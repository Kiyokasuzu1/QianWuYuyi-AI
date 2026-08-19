@echo off
rem ============================================================
rem  start.bat — QianWuYuyi-AI v1.1.0-stable Windows 启动脚本
rem ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

echo [start] 启动 QianWuYuyi-AI API 服务 (端口 = YUYI_API_PORT, 默认 5000)
python api_server.py %*

if errorlevel 1 (
    echo.
    echo [start] 服务退出。检查 logs\ 或控制台输出。
    pause
)
