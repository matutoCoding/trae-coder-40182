@echo off
echo ========================================
echo  呼叫中心合规转写与预警服务 - 启动脚本
echo ========================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+
    pause
    exit /b 1
)

if not exist "venv" (
    echo [信息] 正在创建虚拟环境...
    python -m venv venv
)

echo [信息] 激活虚拟环境...
call venv\Scripts\activate.bat

echo [信息] 安装/更新依赖...
pip install -r requirements.txt

echo.
echo [信息] 启动服务 (端口: 8000)...
echo [提示] 访问 http://localhost:8000/docs 查看交互式 API 文档
echo.

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pause
