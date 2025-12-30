@echo off
REM ========================================
REM 航行警告抓取程式設定檔
REM ========================================

REM 設定 Teams Workflow URL
set TEAMS_WEBHOOK_URL=https://default2b20eccf1c1e43ce93400edfe3a226.6f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/f59bfeccf30041d5b8a51cbd4ee617fe/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=zJiQpFVAzZyaag3zbAmzpfy1yXWW3gZ2AcAMQUpOEBQ

echo 環境變數已設定
echo Teams Webhook URL: %TEAMS_WEBHOOK_URL%
