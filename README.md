# 海事警告監控與自動通知系統

中國海事局、台灣航港局、UKMTO 三方海事航行警告監控，含關鍵字篩選、風險評分、Email / Microsoft Teams 通知。

本次更新聚焦兩個核心痛點：**中國海事局來源可靠度**（多來源、可降級架構）與 **Email 報告品質**（摘要／風險等級／建議行動），並同步完成安全性修正與資料庫安全遷移。詳見文末「本次交付範圍與已知限制」。

## 快速開始（Windows）

```
setup.bat              首次安裝：建立虛擬環境、安裝套件、複製 .env.example -> .env
start.bat               日常執行（爬取 + 通知），套件已安裝時不會重新安裝
test.bat                執行 pytest 測試套件（不會連真實網路/信箱/Webhook）
preview_email.bat       產生 Email 預覽 HTML（--dry-run --no-notify，絕不寄信），存於 reports\ 目錄
```

四個 BAT 檔皆以 `%~dp0` 切換到腳本所在目錄，可從任意工作目錄雙擊或呼叫；找不到 Python／虛擬環境／`.env` 時會顯示明確提示；執行結束會回傳對應的 Exit Code（0 = 成功）。

## 命令列參數

```
python n8n_msa_monitor.py [選項]

--source {cn,tw,ukmto}   只執行指定來源
--dry-run                只爬取並列印結果，不寫入資料庫、不發送任何通知
--save-debug             解析失敗或列表為空時，保存 HTML 快照到 debug/ 目錄
--backfill-days N        覆寫抓取天數範圍
--send-test-email        用資料庫近期資料寄一封測試信（不重新爬取）
--no-notify              爬取並寫入資料庫，但不發送通知
--preview-email          產生 Email 預覽 HTML 到 reports/（不寄信）
```

範例：

```
python n8n_msa_monitor.py --source cn --dry-run --save-debug
python n8n_msa_monitor.py --preview-email --no-notify
python n8n_msa_monitor.py --send-test-email
```

## 環境設定

1. 複製 `.env.example` 為 `.env`，填入實際的 Email / Teams / 資料庫等設定。
2. `.env` 不會、也不應提交版本控制（已列在 `.gitignore`）。
3. 若公司網路使用自訂 CA（例如企業 Proxy 憑證攔截），設定 `CA_BUNDLE_PATH` 指向 CA bundle 檔案；預設一律驗證 HTTPS 憑證，不再全域停用 SSL 驗證。
4. 中國海事局各來源設定於 `config/maritime_sources.json`，可個別啟用/停用、修改網址與 selector，不需要修改主程式。

## 專案結構（本次新增部分）

```
cn_sources/           中國海事局多來源抓取（BaseMaritimeSource 抽象介面、中央/地方 adapter、registry）
services/             風險評分、內容清理、規則式摘要
templates/            Email HTML / 純文字報告樣板
config/maritime_sources.json   中國各來源設定檔
tests/                pytest 測試與 fixture HTML
debug/                解析失敗時的除錯快照（已加入 .gitignore，不會提交）
reports/              Email 預覽 / 匯出報表輸出目錄
```

## 資料庫變更

本次為**安全的加欄位遷移**，不刪除、不覆蓋既有資料：

- 新增豐富化欄位：`notice_number`、`canonical_url`、`content_hash`、`effective_start/end`、`status`、`cleaned_content`、`summary_zh_tw`、`operational_impact`、`recommended_action`、`relevance_score`、`risk_score`、`risk_level`、`matched_categories`、`scoring_reasons`、`confidence`、`action_required`、`first_seen_at`、`last_seen_at`、`last_changed_at` 等。
- 新增 `notification_deliveries` 資料表，Email 與 Teams 各自獨立記錄發送狀態（成功/失敗、重試次數、最後錯誤）。
- 新增 `upsert_rich_warning()`：以 `canonical_url` 或內容雜湊（`content_hash`）判斷新增/相同/內容變更，取代舊版單純依賴「海事局＋標題＋發布時間＋來源」的脆弱唯一鍵。
- 修正 `source_country` 對應錯誤：原本「不是台灣就等於中國」的邏輯，已改為 `CN_MSA→CN / TW_MPB→TW / UKMTO→GB` 明確對照表，並回填既有資料。
- 程式啟動時自動執行遷移（`ALTER TABLE ADD COLUMN`，全部為新增欄位），已在既有資料庫的複本上驗證資料筆數遷移前後一致。

## 中國海事局資料來源清單

見 `config/maritime_sources.json`。目前狀態：

| 來源 | 方式 | 狀態 |
|---|---|---|
| 中央入口（weather.jsp） | Selenium（JS 動態選單） | 沿用既有已知結構，改為 selector 候選清單＋單一海事局失敗不中止 |
| 浙江海事局 | requests + BeautifulSoup | 網址已透過搜尋確認存在，**selector 待實際連線驗證** |
| 江蘇海事局 | requests + BeautifulSoup | 同上 |
| 上海海事局 | requests + BeautifulSoup | 同上 |
| 海南海事局 | requests + BeautifulSoup | 同上 |
| 福建/廣東/山東/遼寧/深圳海事局 | requests + BeautifulSoup | 網域為推測命名慣例，**預設停用**，待驗證後於設定檔啟用 |

**本沙箱環境無法連線至中國官方網站**（`msa.gov.cn` 系列網域皆逾時或連線被拒），因此上述 selector 為根據既有程式邏輯與常見政府網站 CMS 結構設計的候選清單，並非實地驗證結果。請在可連線中國網路的環境執行：

```
python n8n_msa_monitor.py --source cn --save-debug --dry-run
```

執行後查看 `debug/` 目錄下的 HTML 快照與終端機印出的健康狀態表，依實際結構調整 `config/maritime_sources.json` 的 `selectors`。

## 新增或調整的環境變數

見 `.env.example`，新增項目：

- `CA_BUNDLE_PATH`：自訂 CA bundle 路徑（取代全域停用 SSL 驗證）
- `CN_MSA_SOURCES_CONFIG`：中國多來源設定檔路徑
- `SAVE_DEBUG_ON_FAILURE` / `DEBUG_OUTPUT_DIR`：除錯快照開關與輸出目錄
- `ENABLE_AI_SUMMARY` / `AI_SUMMARY_PROVIDER` / `AI_SUMMARY_API_KEY`：選配外部 AI 摘要（預設關閉，失敗會自動回退規則式摘要）

## 測試

```
test.bat
```

或

```
python -m pytest tests/ -v
```

46 項測試涵蓋：SSL 預設驗證、Email escape／URL 驗證、風險評分（含標題無關鍵字但內文命中仍保留）、selector fallback、單一來源失敗不影響其他來源、HTTP 200 但 0 筆視為 EMPTY、資料庫 upsert 去重與內容變更更新、UKMTO 國家代碼修正、通知管道各自獨立記錄狀態等。測試全程不連接真實 SMTP / Teams Webhook / 中國官方網站。

## Email 修改前後差異

**修改前：** 全部卡片皆為同一種紅色、無風險等級、無摘要、無有效期間、無建議行動、標題/連結/關鍵字未 `html.escape`、無純文字 alternative、歷史資料全部展開。

**修改後：** 主旨含最高風險等級與重點事件；主管摘要區塊（今日新增/有效警告/最高風險/主要影響海域/需立即關注事項/各來源健康狀態）；卡片依風險等級（CRITICAL/HIGH/MEDIUM/LOW/INFO）分色排序；每卡片含摘要、有效期間、影響海域、建議行動、命中原因、座標＋Google Maps 連結、原始公告連結、解析可信度；全面 `html.escape` 並僅允許 http/https 連結；同時提供純文字 MIME alternative；歷史資料僅完整展開最高風險前 5 筆，其餘僅顯示統計。

範例輸出：`reports/email_preview_sample.html`（使用範例資料產生，可直接用瀏覽器開啟預覽排版）。

## 已知限制與後續建議

1. **中國地方海事局 selector 尚未實地驗證**（沙箱環境無法連線），需在可連線環境執行 `--source cn --save-debug` 後依 debug 輸出調整。
2. 本次僅重構中國海事局來源與 Email 報告；**Teams 通知、程式完整拆分為 `sources/parsers/services/repositories` 多檔架構、`setup.bat`/`start.bat` 拆分**等 claude.md 中列出的其餘項目尚未執行，建議列入下一階段。
3. 台灣航港局／UKMTO 爬蟲本次未修改（避免既有功能退化），僅在通知前統一補上風險評分；若要讓其標題/內文判斷邏輯與中國來源一致（先抓詳情再判斷關鍵字），需另外排期處理。
4. `geofence_and_risk_module.py`（船位地理圍欄）目前未整合進主流程，因系統尚無即時船位資料來源；`shapely` 已補進 `requirements.txt` 但屬選用功能。
5. 通知去重目前以「新增或內容變更」為準；「風險等級上升」「有效期間變更」的精確比對邏輯可在下一階段強化（目前內容變更會一併觸發重新通知，已涵蓋大部分情境但未做欄位級別的差異判斷）。

## GitHub Actions 自動執行

本專案內建兩個 workflow（`.github/workflows/`）：

- `ci.yml`：每次 push／PR 自動執行 `python -m compileall` 與 `pytest`（不連真實網路/信箱/Webhook）。
- `main.yml`：排程執行 `n8n_msa_monitor.py`（預設每 6 小時一次，UTC 時區，可在檔案內調整 cron），
  也可在 Actions 頁面手動觸發（`workflow_dispatch`），並可選擇 `dry_run` 或 `source` 參數。

### 設定步驟

1. 到 GitHub Repository → Settings → Secrets and variables → Actions，新增以下 Repository secrets：

   | Secret 名稱 | 說明 | 必填 |
   |---|---|---|
   | `MAIL_USER` | 寄件 Gmail 帳號 | 是（若要 Email 通知） |
   | `MAIL_PASSWORD` | Gmail 應用程式密碼（非登入密碼） | 是（若要 Email 通知） |
   | `TARGET_EMAIL` | 收件者信箱 | 是（若要 Email 通知） |
   | `TEAMS_WEBHOOK_URL` | Microsoft Teams Webhook 網址 | 是（若要 Teams 通知） |
   | `MAIL_SMTP_SERVER` | SMTP 伺服器，預設 `smtp.gmail.com` | 否 |
   | `MAIL_SMTP_PORT` | SMTP 埠號，預設 `587` | 否 |
   | `CA_BUNDLE_PATH` | 自訂 CA bundle 路徑（企業網路才需要） | 否 |

   **不要**把上述任何值寫進程式碼、`.env`、或直接提交到 Git；一律透過上方 Secrets 設定，workflow 執行時只會以環境變數形式注入，不會落地成檔案。

2. Repository 建議設為 **Private**（尤其若 `.env.example` 以外還放了任何內部資訊）。
3. 確認 Actions 分頁已啟用（Settings → Actions → General → Allow all actions）。
4. 手動觸發一次 `main.yml`（Actions → Maritime Warning Monitor → Run workflow），先用 `dry_run=true` 確認可正常執行、Chrome 安裝成功、Secrets 讀取正確，再改回正常排程執行。
5. 每次執行完成後，資料庫（`navigation_warnings.db`）、`reports/`、`logs/` 會作為 Artifact 上傳（保留 7～30 天），可在該次 Run 頁面下載；這些檔案不會、也不應提交回 Git。
6. 中國海事局各地方來源 selector 仍待在可連線中國網路的環境驗證（見上方「已知限制」）；建議先以 `workflow_dispatch` + `source=cn` + `dry_run=true` 手動跑一次，觀察 log 判斷各來源健康狀態。
