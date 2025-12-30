@echo off
chcp 65001 >nul
echo ========================================
echo 航行警告自動抓取程式 (含設定)
echo ========================================
echo 開始時間: %date% %time%
echo.

cd /d "%~dp0"

REM 載入設定檔
if exist config.bat (
    call config.bat
    echo ✓ 已載入設定檔
) else (
    echo ✗ 找不到 config.bat，請先建立設定檔
    pause
    exit /b 1
)

REM 檢查 Webhook URL 是否已設定
if "%TEAMS_WEBHOOK_URL%"=="YOUR_TEAMS_WEBHOOK_URL_HERE" (
    echo ✗ 請先在 config.bat 中設定 Teams Webhook URL
    pause
    exit /b 1
)

echo.
echo 正在執行抓取程式...
echo.

REM 啟動 Python 程式
python n8n_msa_monitor.py

echo.
echo 結束時間: %date% %time%
echo ========================================
echo.

REM 記錄執行日誌
echo [%date% %time%] 程式執行完成 >> execution.log
