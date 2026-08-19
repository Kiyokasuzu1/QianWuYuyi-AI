@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem === 项目根目录 ===
set PROJECT_ROOT=%~dp0..

rem === 1. 检查依赖 ===
python -c "import PySide6" 2>nul
if %errorlevel% neq 0 (
    echo [安装依赖] PySide6 未找到，正在安装...
    pip install PySide6 websockets edge-tts
)

python -c "import edge_tts" 2>nul
if %errorlevel% neq 0 (
    echo [安装依赖] edge-tts 未找到，正在安装...
    pip install edge-tts
)

rem === 2. 检查并启动 VTube Studio ===
tasklist /FI "IMAGENAME eq VTube Studio.exe" 2>nul | find /I "VTube Studio.exe" >nul
if %errorlevel% neq 0 (
    echo [启动] 正在启动 VTube Studio...
    if exist "%PROJECT_ROOT%\VTube Studio\VTube Studio.exe" (
        start "" "%PROJECT_ROOT%\VTube Studio\VTube Studio.exe"
        echo [等待] VTube Studio 启动中，等待 10 秒...
        timeout /t 10 /nobreak >nul
    ) else (
        echo [警告] 未找到 VTube Studio.exe
        echo        请确认 VTube Studio 文件夹在项目根目录下
        echo        路径: %PROJECT_ROOT%\VTube Studio\VTube Studio.exe
    )
) else (
    echo [OK] VTube Studio 已在运行
)

rem === 3. 启动控制面板 ===
echo.
echo ========================================
echo   羽依 VTS 控制面板启动中...
echo   模型目录: %PROJECT_ROOT%\A.雪芽2.0
echo ========================================
echo.

python -u main_vts.py
pause
