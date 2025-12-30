import platform  # 記得在檔案最上面 import platform
import subprocess
import os
import sys
import logging
import warnings
import json
import smtplib
import requests
import traceback
import re
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager  # 新增
from database_manager import DatabaseManager
from keyword_manager import KeywordManager


load_dotenv()

# ==================== 1. 設定與日誌過濾 ====================
warnings.filterwarnings('ignore')
logging.getLogger('selenium').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

if os.name == 'nt':
    class ErrorFilter:
        def __init__(self, stream):
            self.stream = stream
        def write(self, text):
            if any(k in text for k in ['ERROR:net', 'handshake failed', 'DEPRECATED_ENDPOINT']): 
                return
            self.stream.write(text)
        def flush(self): 
            self.stream.flush()
    sys.stderr = ErrorFilter(sys.stderr)

os.environ['WDM_LOG_LEVEL'] = '0'

# ==================== 2. Teams 通知類別 (Incoming Webhook 專用) ====================
class TeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    def _fix_url(self, url):
        """修正 URL 格式，處理相對路徑"""
        if not url: 
            return "https://www.msa.gov.cn/page/outter/weather.jsp"
        url = url.strip()
        if url.startswith('/'): 
            return f"https://www.msa.gov.cn{url}"
        if url.startswith(('http://', 'https://')): 
            return url
        if url.startswith(('javascript:', '#')): 
            return "https://www.msa.gov.cn/page/outter/weather.jsp"
        return f"https://www.msa.gov.cn/{url}"
    
    def _create_adaptive_card(self, title, body_elements, actions=None):
        """
        建立 Adaptive Card 格式 (針對 Incoming Webhook)
        """
        card_content = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock", 
                    "text": title, 
                    "weight": "Bolder", 
                    "size": "Large", 
                    "color": "Attention"
                }
            ] + body_elements
        }
        
        if actions:
            card_content["actions"] = actions
        
        # Incoming Webhook 格式
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content
            }]
        }

    def send_warning_notification(self, warning_data):
        """發送單個警告通知"""
        if not self.webhook_url: 
            return False
        
        try:
            warning_id, bureau, title, link, pub_time, keywords, scrape_time = warning_data
            fixed_link = self._fix_url(link)
            
            body = [
                {
                    "type": "TextBlock", 
                    "text": "💡 點擊按鈕若失敗，請複製下方連結", 
                    "size": "Small", 
                    "isSubtle": True, 
                    "wrap": True
                },
                {
                    "type": "FactSet", 
                    "facts": [
                        {"title": "🏢 海事局:", "value": bureau},
                        {"title": "📋 標題:", "value": title},
                        {"title": "📅 時間:", "value": pub_time},
                        {"title": "🔍 關鍵字:", "value": keywords}
                    ]
                },
                {
                    "type": "TextBlock", 
                    "text": "🔗 連結:", 
                    "weight": "Bolder", 
                    "size": "Small"
                },
                {
                    "type": "TextBlock", 
                    "text": fixed_link, 
                    "wrap": True, 
                    "size": "Small", 
                    "fontType": "Monospace"
                }
            ]
            
            actions = [
                {
                    "type": "Action.OpenUrl", 
                    "title": "🌐 開啟公告", 
                    "url": fixed_link
                },
                {
                    "type": "Action.OpenUrl", 
                    "title": "🏠 海事局首頁", 
                    "url": "https://www.msa.gov.cn/page/outter/weather.jsp"
                }
            ]
            
            payload = self._create_adaptive_card("🚨 航行警告通知", body, actions)
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                print(f"  ✅ Teams 通知發送成功 (ID: {warning_id})")
                return True
            else:
                print(f"  ❌ Teams 通知失敗: {response.status_code} - {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"❌ Teams 單發失敗: {e}")
            traceback.print_exc()
            return False

    def send_batch_notification(self, warnings_list):
        """發送批量警告通知"""
        if not self.webhook_url or not warnings_list: 
            return False
        
        try:
            body_elements = [
                {
                    "type": "TextBlock", 
                    "text": f"發現 **{len(warnings_list)}** 個新的航行警告", 
                    "size": "Medium", 
                    "weight": "Bolder"
                },
                {
                    "type": "TextBlock", 
                    "text": "━━━━━━━━━━━━━━━━━━━━", 
                    "wrap": True
                }
            ]
            
            actions = []
            
            # 顯示前 8 筆
            for idx, w in enumerate(warnings_list[:8], 1):
                _, bureau, title, link, pub_time, _, _ = w
                fixed_link = self._fix_url(link)
                
                body_elements.extend([
                    {
                        "type": "TextBlock", 
                        "text": f"**{idx}. {bureau}**", 
                        "weight": "Bolder", 
                        "color": "Accent", 
                        "spacing": "Medium"
                    },
                    {
                        "type": "TextBlock", 
                        "text": title[:100], 
                        "wrap": True
                    },
                    {
                        "type": "TextBlock", 
                        "text": f"📅 {pub_time}", 
                        "size": "Small", 
                        "isSubtle": True
                    },
                    {
                        "type": "TextBlock", 
                        "text": f"🔗 {fixed_link}", 
                        "size": "Small", 
                        "fontType": "Monospace", 
                        "wrap": True
                    }
                ])
                
                if len(actions) < 4:
                    actions.append({
                        "type": "Action.OpenUrl", 
                        "title": f"📄 公告 {idx}", 
                        "url": fixed_link
                    })

            if len(warnings_list) > 8:
                body_elements.append({
                    "type": "TextBlock", 
                    "text": f"*...還有 {len(warnings_list)-8} 筆未顯示*", 
                    "isSubtle": True
                })

            actions.append({
                "type": "Action.OpenUrl", 
                "title": "🏠 海事局首頁", 
                "url": "https://www.msa.gov.cn/page/outter/weather.jsp"
            })
            
            payload = self._create_adaptive_card(
                f"🚨 批量警告通知 ({len(warnings_list)})", 
                body_elements, 
                actions
            )
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                print(f"✅ Teams 批量通知發送成功 ({len(warnings_list)} 筆)")
                return True
            else:
                print(f"❌ Teams 批量通知失敗: {response.status_code}")
                print(f"   回應內容: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"❌ Teams 批量發送失敗: {e}")
            traceback.print_exc()
            return False


# ==================== 3. Gmail 發信類別 ====================
class GmailRelayNotifier:
    def __init__(self, user, password, target_email):
        self.user = user
        self.password = password
        self.target = target_email

    def send_trigger_email(self, report_data: dict, report_html: str) -> bool:
        if not self.user or not self.password or not self.target: 
            print("⚠️ Email 設定不完整，跳過發送")
            return False
        
        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = "GITHUB_TRIGGER_CN_MSA_REPORT"
        
        msg.attach(MIMEText(json.dumps(report_data, ensure_ascii=False, indent=2), 'plain', 'utf-8'))
        msg.attach(MIMEText(report_html, 'html', 'utf-8'))

        try:
            print(f"📧 發送 Email 給 {self.target}...")
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.starttls()
            server.login(self.user, self.password)
            server.sendmail(self.user, self.target, msg.as_string())
            server.quit()
            print("✅ Email 發送成功")
            return True
        except Exception as e:
            print(f"❌ Email 發送失敗: {e}")
            traceback.print_exc()
            return False


# ==================== 4. 主爬蟲類別 ====================
class MSANavigationWarningsScraper:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', headless=True, 
             mail_user=None, mail_pass=None, target_email=None):
        print("🚀 初始化海事局爬蟲...")
        
        self.keyword_manager = KeywordManager()
        self.keywords = self.keyword_manager.get_keywords()
        print(f"📋 載入 {len(self.keywords)} 個監控關鍵字")
        
        self.db_manager = DatabaseManager()
        
        # Teams 初始化
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        self.teams_notifier = TeamsNotifier(webhook_url) if self.enable_teams else None
        
        if self.enable_teams:
            print(f"✅ Teams 通知已啟用 (模式: {send_mode})")
        else:
            print("⚠️ Teams 通知未啟用")
        
        # Email 初始化
        self.email_notifier = GmailRelayNotifier(mail_user, mail_pass, target_email)
        
        # ========== 關鍵修正：WebDriver 設定 ==========
        print("🌐 正在啟動 Chrome WebDriver...")
        
        options = webdriver.ChromeOptions()
        
        # 基本設定
        if headless:
            options.add_argument('--headless=new')  # 使用新版 headless 模式
        
        # 穩定性設定
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-extensions')
        
        # 效能優化
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--silent')
        
        # 網路設定
        options.add_argument('--dns-prefetch-disable')
        options.add_argument('--disable-web-security')
        
        # User Agent
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 視窗大小（即使 headless 也需要）
        options.add_argument('--window-size=1920,1080')
        
        # 忽略證書錯誤
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--ignore-ssl-errors')
        
        # 禁用圖片載入（加速）
        prefs = {
            'profile.managed_default_content_settings.images': 2,
            'profile.default_content_setting_values.notifications': 2,
        }
        options.add_experimental_option('prefs', prefs)
        
        # 排除自動化標記
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        # 設定 Service（關鍵！）
        from selenium.webdriver.chrome.service import Service
        service = Service(ChromeDriverManager().install())
        if platform.system() == 'Windows':
            service.creation_flags = subprocess.CREATE_NO_WINDOW
        
        
        try:
            # 初始化 WebDriver（增加重試機制）
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    print(f"  嘗試啟動 WebDriver (第 {attempt + 1}/{max_retries} 次)...")
                    self.driver = webdriver.Chrome(service=service, options=options)
                    self.driver.set_page_load_timeout(120)  # 頁面載入超時
                    self.driver.set_script_timeout(30)      # 腳本執行超時
                    print("  ✅ WebDriver 啟動成功")
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"  ⚠️ 啟動失敗，{3}秒後重試...")
                        time.sleep(3)
                    else:
                        raise Exception(f"WebDriver 啟動失敗（已重試 {max_retries} 次）: {e}")
            
            self.wait = WebDriverWait(self.driver, 15)  # 增加等待時間
            
        except Exception as e:
            print(f"❌ WebDriver 初始化失敗: {e}")
            raise
        
        self.three_days_ago = datetime.now() - timedelta(days=3)
        self.new_warnings = []
        self.captured_warnings_data = []
        
        print("✅ 爬蟲初始化完成\n")

    def check_keywords(self, text):
        """檢查文字中是否包含關鍵字"""
        return [k for k in self.keywords if k.lower() in text.lower()]

    def parse_date(self, date_str):
        """解析日期字串"""
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%Y-%m-%d %H:%M:%S']:
            try: 
                return datetime.strptime(date_str.strip(), fmt)
            except: 
                continue
        return None

    def scrape_bureau_warnings(self, bureau_name, bureau_element):
        """抓取單一海事局警告"""
        print(f"\n🔍 抓取: {bureau_name}")
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", bureau_element)
            time.sleep(2)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
            items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a")
            
            for item in items:
                try:
                    title = item.get_attribute('title') or item.text.strip()
                    title = re.sub(r'\s*\d{4}-\d{2}-\d{2}\s*$', '', title)
                    if not title: 
                        continue

                    matched = self.check_keywords(title)
                    if not matched: 
                        continue

                    link = item.get_attribute('href') or ''
                    if link.startswith('/'): 
                        link = f"https://www.msa.gov.cn{link}"
                    
                    # 抓取時間
                    try: 
                        publish_time = item.find_element(By.CSS_SELECTOR, ".time").text.strip()
                    except: 
                        match = re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text)
                        publish_time = match.group() if match else ""

                    if publish_time:
                        p_date = self.parse_date(publish_time)
                        if p_date and p_date < self.three_days_ago: 
                            continue

                    # 存入資料庫
                    db_data = (
                        bureau_name, 
                        title, 
                        link, 
                        publish_time, 
                        ', '.join(matched), 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    )
                    is_new, w_id = self.db_manager.save_warning(db_data)
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id, 
                            'bureau': bureau_name, 
                            'title': title, 
                            'link': link, 
                            'time': publish_time, 
                            'keywords': matched
                        })
                        print(f"  ✅ 新警告: {title[:40]}...")
                        
                        # 逐筆發送模式
                        if self.enable_teams and self.send_mode == 'individual':
                            if self.teams_notifier.send_warning_notification((w_id,) + db_data):
                                self.db_manager.mark_as_notified(w_id)
                            time.sleep(1)
                            
                except Exception as e:
                    print(f"  ⚠️ 處理項目時出錯: {e}")
                    continue
                    
        except Exception as e:
            print(f"❌ 抓取 {bureau_name} 錯誤: {e}")
            traceback.print_exc()

    def send_batch_teams(self):
        """Teams 批量發送"""
        if not self.enable_teams or not self.new_warnings: 
            return
        
        print(f"\n📤 準備 Teams 批量發送 ({len(self.new_warnings)} 筆)...")
        
        # 從 DB 撈取完整資料
        warnings_to_send = []
        for w_id in self.new_warnings:
            unnotified = self.db_manager.get_unnotified_warnings()
            for w in unnotified:
                if w[0] == w_id:
                    warnings_to_send.append(w)
                    break
        
        if warnings_to_send:
            if self.teams_notifier.send_batch_notification(warnings_to_send):
                for w_id in self.new_warnings: 
                    self.db_manager.mark_as_notified(w_id)
                print("✅ Teams 批量發送完成，已標記為已通知")
            else:
                print("❌ Teams 批量發送失敗")

    def _generate_report(self, duration):
        """生成報告資料 (JSON & HTML)"""
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', sans-serif;"
        count = len(self.captured_warnings_data)
        status_color = "#2E7D32" if count == 0 else "#D9534F"
        
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')

        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')
        
        # HTML 內容
        html =  f"""
        <html><body style="{font_style} color:#333; line-height:1.5;">
            <div style="background:#003366; color:white; padding:20px; border-radius:6px 6px 0 0;">
                <h2 style="margin: 0; font-size: 25px; font-weight: 700; letter-spacing: 0.5px;"> 
                🚢 中國海事局(CN_MSA) 航行警告監控系統
                </h2>
                <div style="margin-top: 8px; font-size: 12px; color: #a3cbe8; font-weight: 500;">
                📅 Last Update: {now_str_LT} (TPE) <span style="opacity: 0.5;">|</span> {now_str_UTC} (UTC)
                </div>
            </div>
            <div style="background:#f8f9fa; border:1px solid #ddd; padding:15px; margin-bottom:20px;">
                <strong style="color:{status_color};">📊 航行警告報告: {'新增 ' + str(count) + ' 個新警告' if count > 0 else '無新增航行警告'}</strong><br>
            </div>
        """
        
        if count > 0:
            html += f"""<table style="width:100%; border-collapse:collapse; font-size:14px; border:1px solid #ddd;">
                <tr style="background:#f0f4f8; text-align:left;">
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈海事局(Issuing MSA)</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">航行警告標題(Navigation Warning Title)</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈時間(Published Time)</th>
                </tr>"""
            
            for i, item in enumerate(self.captured_warnings_data):
                bg = "#fff" if i % 2 == 0 else "#f9f9f9"
                kw_html = "".join([
                    f"<span style='background:#fff3cd; padding:2px 5px; margin-right:5px; border-radius:3px; font-size:12px;'>關鍵字:{k}</span>" 
                    for k in item['keywords']
                ])
                html += f"""<tr style="background:{bg};">
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{item['bureau']}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee;">
                        <a href="{item['link']}" style="color:#0056b3; text-decoration:none; font-weight:bold;">{item['title']}</a><br>
                        <div style="margin-top:5px;">{kw_html}</div>
                    </td>
                    <td style="padding:10px; border-bottom:1px solid #eee; color:#666;">{item['time']}</td>
                </tr>"""
            html += "</table>"
        else:
            html += "<p style='text-align:center; color:#666; padding:20px;'>本次執行未發現新的航行警告</p>"
                # Footer
        html += f"""
            <div style="margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 20px; font-size: 15px; color: #9ca3af; text-align: center; {font_style}">
                <p style="margin: 0;">Wan Hai Lines Ltd. | Marine Technology Division</p>
                <p style="margin: 0;color: blue;">Present by Fleet Risk Department</p>
                <p style="margin: 0 0 0 0;">Data Source: China Maritime Safety Administration. (CN_MSA) | Automated System</p>
            </div>
        </body>
        </html>
        """
            
        html += "</body></html>"
        
        json_data = {
            "execution_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "duration": round(duration, 2),
            "new_warnings_count": count,
            "new_warnings": self.captured_warnings_data
        }
        return json_data, html

    def run(self):
        """主執行流程"""
        start = datetime.now()
        try:
            print(f"⏱️ 開始執行... (通知模式: {self.send_mode})")
            
            # ========== 關鍵修正：增加重試機制 ==========
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    print(f"🌐 正在載入海事局網站 (第 {attempt + 1}/{max_retries} 次)...")
                    self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
                    
                    # 等待頁面完全載入
                    time.sleep(5)
                    
                    # 驗證頁面是否載入成功
                    if "海事" in self.driver.title or len(self.driver.page_source) > 1000:
                        print("✅ 頁面載入成功")
                        break
                    else:
                        raise Exception("頁面內容異常")
                        
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"⚠️ 載入失敗: {e}，5秒後重試...")
                        time.sleep(5)
                    else:
                        raise Exception(f"網頁載入失敗（已重試 {max_retries} 次）: {e}")
            
            # 點擊「航行警告」按鈕
            try:
                print("🔍 尋找「航行警告」按鈕...")
                nav_btn = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '航行警告')]"))
                )
                self.driver.execute_script("arguments[0].click();", nav_btn)
                time.sleep(3)
                print("✅ 已點擊「航行警告」")
            except Exception as e:
                print(f"❌ 找不到「航行警告」按鈕: {e}")
                # 嘗試截圖除錯（如果不是 headless）
                try:
                    self.driver.save_screenshot('error_screenshot.png')
                    print("📸 已儲存錯誤截圖: error_screenshot.png")
                except:
                    pass
                raise
            
            # 獲取海事局列表
            try:
                bureaus = [
                    b.text.strip() 
                    for b in self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text") 
                    if b.text.strip()
                ]
                
                if not bureaus:
                    raise Exception("未找到任何海事局")
                
                print(f"📍 找到 {len(bureaus)} 個海事局")
                
            except Exception as e:
                print(f"❌ 獲取海事局列表失敗: {e}")
                raise
            
            # 遍歷海事局
            for b_name in bureaus:
                try:
                    elem = self.driver.find_element(
                        By.XPATH, 
                        f"//div[@class='nav_lv2_text' and contains(text(), '{b_name}')]"
                    )
                    self.scrape_bureau_warnings(b_name, elem)
                except Exception as e:
                    print(f"⚠️ 跳過 {b_name}: {e}")
                    continue
            
            # 批量發送模式
            if self.send_mode == 'batch':
                self.send_batch_teams()
            
            duration = (datetime.now() - start).total_seconds()
            print(f"\n{'='*60}")
            print(f"✅ 執行完成")
            print(f"⏱️ 耗時: {duration:.2f} 秒")
            print(f"📊 新警告: {len(self.new_warnings)} 筆")
            print(f"{'='*60}\n")
            
            # 生成並發送報告 (Email)
            if self.new_warnings:
                print("📧 正在生成並發送 Email 報告...")
                j_data, h_data = self._generate_report(duration)
                self.email_notifier.send_trigger_email(j_data, h_data)
                
                # 匯出 Excel
                print("📊 正在匯出 Excel...")
                self.db_manager.export_to_excel()
            else:
                print("ℹ️ 無新警告，跳過 Email 和 Excel 匯出")
            
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"❌ 執行錯誤: {e}")
            print(f"{'='*60}")
            traceback.print_exc()
            
            # 嘗試儲存錯誤資訊
            try:
                with open('error_log.txt', 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"時間: {datetime.now()}\n")
                    f.write(f"錯誤: {e}\n")
                    f.write(traceback.format_exc())
                    f.write(f"{'='*60}\n")
                print("📝 錯誤日誌已儲存到 error_log.txt")
            except:
                pass
                
        finally:
            try:
                self.driver.quit()
                print("🔚 瀏覽器已關閉")
            except:
                print("⚠️ 瀏覽器關閉時發生錯誤")


# ==================== 5. 主程式進入點 ====================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚢 MSA 航行警告監控系統")
    print("="*60 + "\n")
    
    # 從環境變數讀取設定
    TEAMS_WEBHOOK = os.getenv('TEAMS_WEBHOOK_URL')
    MAIL_USER = os.getenv('MAIL_USER')
    MAIL_PASS = os.getenv('MAIL_PASSWORD')
    TARGET_EMAIL = os.getenv('TARGET_EMAIL')
    
    # 檢查必要設定
    if not TEAMS_WEBHOOK:
        print("⚠️ 警告: 未設定 TEAMS_WEBHOOK_URL 環境變數")
    
    if not MAIL_USER or not MAIL_PASS:
        print("⚠️ 警告: 未設定 Email 帳號或密碼")
    
    if not TARGET_EMAIL:
        print("⚠️ 警告: 未設定 TARGET_EMAIL")
    
    print()  # 空行
    
    # 初始化爬蟲
    scraper = MSANavigationWarningsScraper(
        webhook_url=TEAMS_WEBHOOK,
        enable_teams=bool(TEAMS_WEBHOOK),
        send_mode='batch',  # 可選: 'batch' 或 'individual'
        headless=True,
        mail_user=MAIL_USER,
        mail_pass=MAIL_PASS,
        target_email=TARGET_EMAIL
    )
    
    # 執行
    scraper.run()

