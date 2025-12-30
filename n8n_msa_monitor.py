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
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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
            if any(k in text for k in ['ERROR:net', 'handshake failed', 'DEPRECATED_ENDPOINT']): return
            self.stream.write(text)
        def flush(self): self.stream.flush()
    sys.stderr = ErrorFilter(sys.stderr)

os.environ['WDM_LOG_LEVEL'] = '0'

# 請確保您的環境中有這兩個檔案，或將其邏輯也一併整合
try:
    from database_manager import DatabaseManager
    from keyword_manager import KeywordManager
except ImportError:
    print("❌ 錯誤: 找不到 database_manager.py 或 keyword_manager.py")
    print("請確保這些檔案在同一目錄下，或將其程式碼整合至此檔案。")
    sys.exit(1)

# ==================== 2. Teams 通知類別 (整合版) ====================
class TeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    def _fix_url(self, url):
        """修正 URL 格式，處理相對路徑"""
        if not url: return "https://www.msa.gov.cn/page/outter/weather.jsp"
        url = url.strip()
        if url.startswith('/'): return f"https://www.msa.gov.cn{url}"
        if url.startswith(('http://', 'https://')): return url
        if url.startswith(('javascript:', '#')): return "https://www.msa.gov.cn/page/outter/weather.jsp"
        return f"https://www.msa.gov.cn/{url}"
    
    def _create_adaptive_card(self, title, body_elements, actions=None):
        """
        修正版：針對 Power Automate Workflow 優化
        移除 type: message 外殼，直接回傳 AdaptiveCard 的 Content
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
            
        # 注意：Power Automate 的 "Post Card" 動作通常只需要這個 content 字典
        # 為了相容性，我們通常發送含有 attachments 的結構，
        # 但如果遇到 Branching 錯誤，建議改發送純 type: message 結構 (針對 Workflows) 
        # 或者 這裡我們發送一個特殊的結構讓 Power Automate 更好解析
        
        # === 關鍵修改 ===
        # 對於 Power Automate Workflows，我們發送完整的 message 結構，
        # 但請確保您的 Flow 裡面使用的是 "Post card in a chat or channel" 
        # 並且接收的是 "attachments[0].content" 或者直接接收卡片 JSON
        
        # 如果您在 Flow 用的是 "Post adaptive card in a chat or channel"
        # 它通常期待的是下面的 card_content (純卡片)，而不是外層的 message
        
        # 為了最通用的解法，我們先回傳純卡片結構，
        # 如果您的 Flow 需要 attachments 結構，請用下方註解掉的那段
        
        # 方案 A: 針對 Power Automate Workflow (直接貼卡片內容) -> 推薦嘗試這個
        return card_content

        # 方案 B: 針對 Incoming Webhook Connector (舊版)
        # return {
        #     "type": "message",
        #     "attachments": [{
        #         "contentType": "application/vnd.microsoft.card.adaptive",
        #         "content": card_content
        #     }]
        # }

    def send_warning_notification(self, warning_data):
        """發送單個警告通知"""
        if not self.webhook_url: return False
        try:
            warning_id, bureau, title, link, pub_time, keywords, scrape_time = warning_data
            fixed_link = self._fix_url(link)
            
            body = [
                {"type": "TextBlock", "text": "💡 點擊按鈕若失敗，請複製下方連結", "size": "Small", "isSubtle": True, "wrap": True},
                {"type": "FactSet", "facts": [
                    {"title": "🏢 海事局:", "value": bureau},
                    {"title": "📋 標題:", "value": title},
                    {"title": "📅 時間:", "value": pub_time},
                    {"title": "🔍 關鍵字:", "value": keywords}
                ]},
                {"type": "TextBlock", "text": "🔗 連結:", "weight": "Bolder", "size": "Small"},
                {"type": "TextBlock", "text": fixed_link, "wrap": True, "size": "Small", "fontType": "Monospace"}
            ]
            
            actions = [
                {"type": "Action.OpenUrl", "title": "🌐 開啟公告", "url": fixed_link},
                {"type": "Action.OpenUrl", "title": "🏠 海事局首頁", "url": "https://www.msa.gov.cn/page/outter/weather.jsp"}
            ]
            
            # 使用修正後的 create 方法
            payload = self._create_adaptive_card("🚨 航行警告通知", body, actions)
            
            # 這裡增加一個判斷：如果是 Power Automate Workflow，有時候需要包在 'body' 裡，
            # 但大部份直接傳送 JSON 即可。
            
            requests.post(self.webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            return True
        except Exception as e:
            print(f"Teams 單發失敗: {e}")
            return False

    def send_batch_notification(self, warnings_list):
        """發送批量警告通知"""
        if not self.webhook_url or not warnings_list: return False
        
        try:
            body_elements = [
                {"type": "TextBlock", "text": f"發現 **{len(warnings_list)}** 個新的航行警告", "size": "Medium", "weight": "Bolder"},
                {"type": "TextBlock", "text": "━━━━━━━━━━━━━━━━━━━━", "wrap": True}
            ]
            
            actions = []
            # 顯示前 8 筆
            for idx, w in enumerate(warnings_list[:8], 1):
                # 解包數據
                _, bureau, title, link, pub_time, _, _ = w
                fixed_link = self._fix_url(link)
                
                body_elements.extend([
                    {"type": "TextBlock", "text": f"**{idx}. {bureau}**", "weight": "Bolder", "color": "Accent", "spacing": "Medium"},
                    {"type": "TextBlock", "text": title[:100], "wrap": True},
                    {"type": "TextBlock", "text": f"📅 {pub_time}", "size": "Small", "isSubtle": True},
                    {"type": "TextBlock", "text": f"🔗 {fixed_link}", "size": "Small", "fontType": "Monospace", "wrap": True}
                ])
                
                if len(actions) < 4:
                    actions.append({"type": "Action.OpenUrl", "title": f"📄 公告 {idx}", "url": fixed_link})

            if len(warnings_list) > 8:
                body_elements.append({"type": "TextBlock", "text": f"*...還有 {len(warnings_list)-8} 筆未顯示*", "isSubtle": True})

            actions.append({"type": "Action.OpenUrl", "title": "🏠 海事局首頁", "url": "https://www.msa.gov.cn/page/outter/weather.jsp"})
            
            # 使用修正後的 create 方法
            payload = self._create_adaptive_card(f"🚨 批量警告通知 ({len(warnings_list)})", body_elements, actions)
            
            res = requests.post(self.webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            
            if res.status_code == 202:
                return True
            else:
                # 就算失敗也印出回應，方便除錯
                print(f"Teams 回應碼: {res.status_code}, 回應: {res.text}")
                return False
                
        except Exception as e:
            print(f"Teams 批量發送失敗: {e}")
            return False

# ==================== 3. Gmail 發信類別 ====================
class GmailRelayNotifier:
    def __init__(self, user, password, target_email):
        self.user = user
        self.password = password
        self.target = target_email

    def send_trigger_email(self, report_data: dict, report_html: str) -> bool:
        if not self.user or not self.password: return False
        
        msg = MIMEMultipart('alternative')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = f"MSA 航行警告通知 - {datetime.now().strftime('%Y-%m-%d')}"
        
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
            return False

# ==================== 4. 主爬蟲類別 ====================
class MSANavigationWarningsScraper:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', headless=True, 
                 mail_user=None, mail_pass=None, target_email=None):
        print("🚀 初始化海事局爬蟲...")
        
        self.keyword_manager = KeywordManager()
        self.keywords = self.keyword_manager.get_keywords()
        self.db_manager = DatabaseManager()
        
        # Teams 初始化 (使用內部的 TeamsNotifier)
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        self.teams_notifier = TeamsNotifier(webhook_url) if self.enable_teams else None
        
        # Email 初始化
        self.email_notifier = GmailRelayNotifier(mail_user, mail_pass, target_email)
        
        # 瀏覽器設定
        options = webdriver.ChromeOptions()
        if headless: options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-logging')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36')
        
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 10)
        self.three_days_ago = datetime.now() - timedelta(days=3)
        self.new_warnings = []
        self.captured_warnings_data = []

    def check_keywords(self, text):
        return [k for k in self.keywords if k.lower() in text.lower()]

    def parse_date(self, date_str):
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%Y-%m-%d %H:%M:%S']:
            try: return datetime.strptime(date_str.strip(), fmt)
            except: continue
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
                    if not title: continue

                    matched = self.check_keywords(title)
                    if not matched: continue

                    link = item.get_attribute('href') or ''
                    if link.startswith('/'): link = f"https://www.msa.gov.cn{link}"
                    
                    # 抓取時間
                    try: publish_time = item.find_element(By.CSS_SELECTOR, ".time").text.strip()
                    except: publish_time = (re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text) or sorted([''])).group()

                    if publish_time:
                        p_date = self.parse_date(publish_time)
                        if p_date and p_date < self.three_days_ago: continue

                    # 存入資料庫
                    db_data = (bureau_name, title, link, publish_time, ', '.join(matched), datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    is_new, w_id = self.db_manager.save_warning(db_data)
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id, 'bureau': bureau_name, 'title': title, 
                            'link': link, 'time': publish_time, 'keywords': matched
                        })
                        print(f"  ✓ 新警告: {title[:30]}...")
                        
                        # 逐筆發送模式
                        if self.enable_teams and self.send_mode == 'individual':
                            self.teams_notifier.send_warning_notification((w_id,) + db_data)
                            self.db_manager.mark_as_notified(w_id)
                            time.sleep(1)
                except: continue
        except Exception as e:
            print(f"抓取 {bureau_name} 錯誤: {e}")

    def send_batch_teams(self):
        """Teams 批量發送"""
        if not self.enable_teams or not self.new_warnings: return
        print(f"\n📤 準備 Teams 批量發送 ({len(self.new_warnings)} 筆)...")
        
        # 從 DB 撈取完整資料以符合 tuple 結構
        warnings_to_send = []
        for w_id in self.new_warnings:
            # 假設 get_unnotified_warnings 返回列表，且第一欄是 ID
            unnotified = self.db_manager.get_unnotified_warnings()
            for w in unnotified:
                if w[0] == w_id:
                    warnings_to_send.append(w)
                    break
        
        if warnings_to_send:
            if self.teams_notifier.send_batch_notification(warnings_to_send):
                for w_id in self.new_warnings: self.db_manager.mark_as_notified(w_id)
                print("✓ Teams 批量發送完成")

    def _generate_report(self, duration):
        """生成報告資料 (JSON & HTML)"""
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', sans-serif;"
        count = len(self.captured_warnings_data)
        status_color = "#2E7D32" if count == 0 else "#D9534F"
        
        # HTML 內容
        html = f"""
        <html><body style="{font_style} color:#333; line-height:1.5;">
            <div style="background:#003366; color:white; padding:20px; border-radius:6px 6px 0 0;">
                <h2 style="margin:0;">🚢 MSA 航行警告監控</h2>
                <p style="margin:5px 0 0 0; opacity:0.9; font-size:13px;">Update: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
            </div>
            <div style="background:#f8f9fa; border:1px solid #ddd; padding:15px; margin-bottom:20px;">
                <strong style="color:{status_color};">📊 監控狀態: {'發現 ' + str(count) + ' 則新警告' if count > 0 else '無新警告'}</strong>
            </div>
        """
        
        if count > 0:
            html += f"""<table style="width:100%; border-collapse:collapse; font-size:14px; border:1px solid #ddd;">
                <tr style="background:#f0f4f8; text-align:left;">
                    <th style="padding:10px; border-bottom:2px solid #ccc;">地區</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">標題</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">時間</th>
                </tr>"""
            for i, item in enumerate(self.captured_warnings_data):
                bg = "#fff" if i % 2 == 0 else "#f9f9f9"
                kw_html = "".join([f"<span style='background:#fff3cd; padding:2px 5px; margin-right:5px; border-radius:3px; font-size:12px;'>{k}</span>" for k in item['keywords']])
                html += f"""<tr style="background:{bg};">
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{item['bureau']}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee;">
                        <a href="{item['link']}" style="color:#0056b3; text-decoration:none; font-weight:bold;">{item['title']}</a><br>
                        <div style="margin-top:5px;">{kw_html}</div>
                    </td>
                    <td style="padding:10px; border-bottom:1px solid #eee; color:#666;">{item['time']}</td>
                </tr>"""
            html += "</table>"
            
        html += "</body></html>"
        
        json_data = {
            "execution_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "duration": round(duration, 2),
            "new_warnings": self.captured_warnings_data
        }
        return json_data, html

    def run(self):
        start = datetime.now()
        try:
            print(f"⏱️ 開始執行... (模式: {self.send_mode})")
            self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
            time.sleep(3)
            
            nav_btn = self.wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), '航行警告')]")))
            self.driver.execute_script("arguments[0].click();", nav_btn)
            time.sleep(2)
            
            bureaus = [b.text.strip() for b in self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text") if b.text.strip()]
            
            for b_name in bureaus:
                try:
                    elem = self.driver.find_element(By.XPATH, f"//div[@class='nav_lv2_text' and contains(text(), '{b_name}')]")
                    self.scrape_bureau_warnings(b_name, elem)
                except: continue
            
            if self.send_mode == 'batch':
                self.send_batch_teams()
            
            duration = (datetime.now() - start).total_seconds()
            print(f"\n✅ 執行完成 | 耗時: {duration:.2f}s | 新警告: {len(self.new_warnings)}")
            
            # 生成並發送報告 (Email)
            if self.new_warnings:
                print("📧 正在發送 Email 報告...")
                j_data, h_data = self._generate_report(duration)
                self.email_notifier.send_trigger_email(j_data, h_data)
                self.db_manager.export_to_excel()
            
        except Exception as e:
            print(f"❌ 執行錯誤: {e}")
            traceback.print_exc()
        finally:
            self.driver.quit()

if __name__ == "__main__":
    # ========== 環境變數設定 ==========
    TEAMS_WEBHOOK = os.getenv('TEAMS_WEBHOOK_URL', 'https://default2b20eccf1c1e43ce93400edfe3a226.6f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/f59bfeccf30041d5b8a51cbd4ee617fe/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=zJiQpFVAzZyaag3zbAmzpfy1yXWW3gZ2AcAMQUpOEBQ')
    MAIL_USER = os.getenv('MAIL_USER', 'harry810403@gmail.com')
    MAIL_PASS = os.getenv('MAIL_PASSWORD', 'nsvhlultlthluogg')
    TARGET_EMAIL = "harry_chung@wanhai.com"
    
    scraper = MSANavigationWarningsScraper(
        webhook_url=TEAMS_WEBHOOK,
        enable_teams=True,
        send_mode='batch',
        headless=True,
        mail_user=MAIL_USER,
        mail_pass=MAIL_PASS,
        target_email=TARGET_EMAIL
    )
    scraper.run()