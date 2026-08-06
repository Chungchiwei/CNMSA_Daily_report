@echo off
setlocal
chcp 65001 >nul
title 海事警告監控系統 - Email 預覽

cd /d "%~dp0"

echo ========================================
echo   產生 Email 預覽 HTML（--dry-run --no-notify，絕不寄送真實郵件）
echo   專案目錄: %CD%
echo   結果會存在 reports\ 目錄下，可直接用瀏覽器開啟查看
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.10 以上版本
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo [錯誤] 找不到虛擬環境，請先執行 setup.bat 完成首次安裝
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

if not exist ".env" (
    echo [提醒] 找不到 .env，仍可產生預覽（本操作本來就不會寄信），
    echo         但若也想測試實際抓取資料，建議先設定 .env。
    echo.
)

python n8n_msa_monitor.py --dry-run --no-notify --preview-email
set EXIT_CODE=%ERRORLEVEL%

echo.
echo ========================================
if "%EXIT_CODE%"=="0" (
    echo   預覽已產生，請至 reports\ 目錄查看 email_preview_*.html
) else (
    echo   執行時發生錯誤（Exit Code: %EXIT_CODE%）
)
echo ========================================
echo.
echo 按任意鍵結束...
pause >nul
exit /b %EXIT_CODE%
