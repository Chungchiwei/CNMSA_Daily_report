@echo off
REM 設定編碼為 UTF-8 以顯示中文
chcp 65001 >nul

echo ========================================
echo Debug Mode: 啟動檢查程序
echo ========================================
echo.

REM 1. 檢查 Python 是否存在
echo [Step 1] 檢查 Python...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 python 指令!
    echo 請確認已安裝 Python 並且有勾選 "Add Python to PATH"
    pause
    exit /b
)
python --version
echo Python 檢查通過。
echo.

REM 2. 檢查虛擬環境
echo [Step 2] 檢查虛擬環境 (venv)...
if not exist "venv" (
    echo 正在建立 venv...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [錯誤] 建立 venv 失敗!
        pause
        exit /b
    )
)

REM 3. 啟動虛擬環境
echo [Step 3] 啟動虛擬環境...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [錯誤] 找不到 venv\Scripts\activate.bat
    echo 請嘗試刪除 venv 資料夾後重新執行。
    pause
    exit /b
)


python n8n_msa_monitor.py

if %errorlevel% neq 0 (
    echo.
    echo [錯誤] 程式執行發生錯誤 (Error Code: %errorlevel%)
)

echo.
echo 程式已結束。
pause
