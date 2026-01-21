#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一海事警告監控系統 (中國海事局 + 台灣航港局)
支援經緯度提取、Teams 通知、Email 報告
"""

import platform
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
from email.mime.image import MIMEImage
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import urllib3
from database_manager import DatabaseManager
from keyword_manager import KeywordManager

# 停用警告
os.environ['WDM_SSL_VERIFY'] = '0'
load_dotenv()
warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger('selenium').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

# 錯誤過濾器 (Windows)
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


# ==================== 2. 座標提取器 (增強版) ====================
class CoordinateExtractor:
    """提取文本中的經緯度座標（增強版）"""
    
    def __init__(self):
        # 各種經緯度格式的正則表達式
        self.patterns = [
            # 格式1: 18-17.37N 109-22.17E (度-分.小數)
            r'(\d{1,3})-(\d{1,2}\.\d+)\s*([NSns北南])\s+(\d{1,3})-(\d{1,2}\.\d+)\s*([EWew東西])',
            
            # 格式2: 18-17N 109-22E (度-分)
            r'(\d{1,3})-(\d{1,2})\s*([NSns北南])\s+(\d{1,3})-(\d{1,2})\s*([EWew東西])',
            
            # 格式3: 25°30'N 121°20'E
            r'(\d{1,3})[°度]\s*(\d{1,2})[\'′分]?\s*([NSns北南])\s+(\d{1,3})[°度]\s*(\d{1,2})[\'′分]?\s*([EWew東西])',
            
            # 格式4: 25°30.5'N 121°20.8'E (含小數分)
            r'(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([NSns北南])\s+(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([EWew東西])',
            
            # 格式5: N25°30' E121°20'
            r'([NSns北南])\s*(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s+([EWew東西])\s*(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?',
            
            # 格式6: 25.5N 121.3E (十進制度)
            r'(\d{1,3}\.\d+)\s*[°度]?\s*([NSns北南])\s+(\d{1,3}\.\d+)\s*[°度]?\s*([EWew東西])',
            
            # 格式7: 北緯25度30分 東經121度20分
            r'[北南緯]\s*(\d{1,3})\s*度\s*(\d{1,2})\s*分\s+[東西經]\s*(\d{1,3})\s*度\s*(\d{1,2})\s*分',
        ]
        
        print("  🗺️ 座標提取器初始化完成")
    
    def extract_coordinates(self, text):
        """
        從文本中提取所有經緯度座標
        返回: [(lat, lon), ...] 列表，座標為十進制度數
        """
        coordinates = []
        
        if not text:
            return coordinates
        
        # 預處理：移除中文頓號、全形逗號等
        text = text.replace('、', ' ').replace('，', ' ').replace('。', ' ')
        
        for pattern in self.patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    coord = self._parse_match(match, pattern)
                    if coord and self._validate_coordinate(coord):
                        coordinates.append(coord)
                except Exception as e:
                    continue
        
        # 去重（保留唯一座標，容許0.01度誤差）
        unique_coords = []
        for coord in coordinates:
            is_duplicate = False
            for existing in unique_coords:
                if abs(coord[0] - existing[0]) < 0.01 and abs(coord[1] - existing[1]) < 0.01:
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_coords.append(coord)
        
        return unique_coords
    
    def _parse_match(self, match, pattern):
        """解析正則匹配結果為十進制座標"""
        groups = match.groups()
        
        # 格式6: 十進制度數 (25.5N 121.3E)
        if len(groups) == 4 and '\\.' in pattern and 'degree' not in pattern:
            try:
                lat = float(groups[0])
                lat_dir = groups[1].upper()
                lon = float(groups[2])
                lon_dir = groups[3].upper()
                
                if lat_dir in ['S', 's', '南']:
                    lat = -lat
                if lon_dir in ['W', 'w', '西']:
                    lon = -lon
                
                return (lat, lon)
            except:
                return None
        
        # 格式5: N25°30' E121°20'
        if len(groups) >= 6 and groups[0] in ['N', 'S', 'n', 's', '北', '南']:
            try:
                lat_dir = groups[0].upper()
                lat_deg = float(groups[1])
                lat_min = float(groups[2])
                lon_dir = groups[3].upper()
                lon_deg = float(groups[4])
                lon_min = float(groups[5])
                
                lat = lat_deg + lat_min / 60
                lon = lon_deg + lon_min / 60
                
                if lat_dir in ['S', 's', '南']:
                    lat = -lat
                if lon_dir in ['W', 'w', '西']:
                    lon = -lon
                
                return (lat, lon)
            except:
                return None
        
        # 格式1, 2, 3, 4, 7: 度分格式
        if len(groups) >= 6:
            try:
                lat_deg = float(groups[0])
                lat_min = float(groups[1])
                lat_dir = groups[2].upper() if len(groups[2]) > 0 else 'N'
                lon_deg = float(groups[3])
                lon_min = float(groups[4])
                lon_dir = groups[5].upper() if len(groups[5]) > 0 else 'E'
                
                lat = lat_deg + lat_min / 60
                lon = lon_deg + lon_min / 60
                
                if lat_dir in ['S', 's', '南']:
                    lat = -lat
                if lon_dir in ['W', 'w', '西']:
                    lon = -lon
                
                return (lat, lon)
            except:
                return None
        
        return None
    
    def _validate_coordinate(self, coord):
        """驗證座標是否合理"""
        if not coord or len(coord) != 2:
            return False
        
        lat, lon = coord
        
        # 緯度範圍: -90 到 90
        if lat < -90 or lat > 90:
            return False
        
        # 經度範圍: -180 到 180
        if lon < -180 or lon > 180:
            return False
        
        # 亞太海域大致範圍檢查
        # 緯度: -60°N - 60°N, 經度: 60°E - 180°E
        if not (-60 <= lat <= 60 and 60 <= lon <= 180):
            return False
        
        return True
    
    def extract_from_html(self, html_content):
        """
        從 HTML 內容中提取座標
        專門處理海事局網頁格式
        """
        try:
            # 使用 BeautifulSoup 解析
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 尋找主要內容區域（中國海事局格式）
            content_div = soup.find('div', {'class': 'text', 'id': 'ch_p'})
            if content_div:
                text = content_div.get_text()
                return self.extract_coordinates(text)
            
            # 如果找不到特定區域，從整個內容提取
            return self.extract_coordinates(html_content)
            
        except Exception as e:
            print(f"    ⚠️ HTML 解析失敗: {e}")
            return []
    
    def format_coordinates(self, coordinates):
        """格式化座標列表為字串"""
        if not coordinates:
            return "無座標資訊"
        
        formatted = []
        for lat, lon in coordinates:
            # 判斷方向
            lat_dir = 'N' if lat >= 0 else 'S'
            lon_dir = 'E' if lon >= 0 else 'W'
            
            formatted.append(f"{abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}")
        
        return " | ".join(formatted)


# ==================== 3. 統一 Teams 通知系統 ====================
class UnifiedTeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    def _fix_url(self, url, base_domain=""):
        """修正 URL 格式"""
        if not url: 
            return base_domain or "https://www.msa.gov.cn/page/outter/weather.jsp"
        url = url.strip()
        if url.startswith('/'): 
            return f"{base_domain}{url}" if base_domain else f"https://www.msa.gov.cn{url}"
        if url.startswith(('http://', 'https://')): 
            return url
        if url.startswith(('javascript:', '#')): 
            return base_domain or "https://www.msa.gov.cn/page/outter/weather.jsp"
        return f"{base_domain}/{url}" if base_domain else f"https://www.msa.gov.cn/{url}"
    
    def _create_adaptive_card(self, title, body_elements, actions=None):
        """建立 Adaptive Card 格式"""
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
        
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content
            }]
        }

    def send_batch_notification(self, warnings_list, source_type="CN_MSA"):
        """發送批量警告通知 (含座標資訊)"""
        if not self.webhook_url or not warnings_list: 
            return False
        
        try:
            # 根據來源設定圖示和名稱
            if source_type == "TW_MPB":
                source_icon = "🇹🇼"
                source_name = "台灣航港局"
                home_url = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
                base_domain = "https://www.motcmpb.gov.tw"
            else:
                source_icon = "🇨🇳"
                source_name = "中國海事局"
                home_url = "https://www.msa.gov.cn/page/outter/weather.jsp"
                base_domain = "https://www.msa.gov.cn"
            
            body_elements = [
                {
                    "type": "TextBlock", 
                    "text": f"{source_icon} **{source_name}** 發現 **{len(warnings_list)}** 個新的航行警告", 
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
                _, bureau, title, link, pub_time, _, _, coordinates = w
                fixed_link = self._fix_url(link, base_domain)
                
                # 座標摘要
                coord_summary = "無座標"
                if coordinates:
                    try:
                        coord_list = json.loads(coordinates) if isinstance(coordinates, str) else coordinates
                        if coord_list:
                            coord_summary = f"📍 {len(coord_list)} 個座標點"
                    except:
                        coord_summary = "座標格式錯誤"
                
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
                        "text": f"📅 {pub_time} | {coord_summary}", 
                        "size": "Small", 
                        "isSubtle": True
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
                "title": f"🏠 {source_name}首頁", 
                "url": home_url
            })
            
            payload = self._create_adaptive_card(
                f"🚨 {source_name} 批量警告通知 ({len(warnings_list)})", 
                body_elements, 
                actions
            )
            
            print(f"  📤 正在發送 Teams 通知到: {self.webhook_url[:50]}...")
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30,
                verify=False
            )
            
            if response.status_code in [200, 202]:
                print(f"✅ {source_name} Teams 批量通知發送成功 ({len(warnings_list)} 筆)")
                return True
            else:
                print(f"❌ {source_name} Teams 批量通知失敗: HTTP {response.status_code}")
                print(f"   回應內容: {response.text[:200]}")
                return False
                
        except requests.exceptions.SSLError as e:
            print(f"❌ {source_name} Teams SSL 錯誤: {e}")
            print(f"   💡 建議: 檢查網路代理設定或憑證")
            return False
        except requests.exceptions.Timeout as e:
            print(f"❌ {source_name} Teams 連線逾時: {e}")
            return False
        except requests.exceptions.ConnectionError as e:
            print(f"❌ {source_name} Teams 連線錯誤: {e}")
            return False
        except Exception as e:
            print(f"❌ {source_name} Teams 批量發送失敗: {e}")
            traceback.print_exc()
            return False


# ==================== 4. Email 通知系統 ====================
class GmailRelayNotifier:
    """Gmail SMTP 郵件通知系統"""
    def __init__(self, mail_user, mail_pass, target_email):
        self.mail_user = mail_user
        self.mail_pass = mail_pass
        self.target_email = target_email
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        if not all([mail_user, mail_pass, target_email]):
            print("⚠️ Email 通知未完整設定")
            self.enabled = False
        else:
            self.enabled = True
            print("✅ Email 通知系統已啟用")
    
    def send_trigger_email(self, warnings_data):
        """發送觸發郵件（含座標資訊）"""
        if not self.enabled:
            print("ℹ️ Email 通知未啟用")
            return False
        
        try:
            msg = MIMEMultipart('related')
            msg['Subject'] = f"🌊 航行警告監控報告 - {(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}(TPE) / {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}(UTC)"
            msg['From'] = self.mail_user
            msg['To'] = self.target_email
            
            # 生成 HTML 內容
            html_content = self._generate_html_report(warnings_data)
            
            msg_alternative = MIMEMultipart('alternative')
            msg.attach(msg_alternative)
            msg_alternative.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 發送郵件
            print(f"📧 正在發送郵件至 {self.target_email}...")
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            
            print(f"✅ 郵件發送成功")
            return True
            
        except Exception as e:
            print(f"❌ 郵件發送失敗: {e}")
            traceback.print_exc()
            return False
    
    def _generate_html_report(self, warnings_data):
        """生成 HTML 報告（含座標資訊）"""
        coord_extractor = CoordinateExtractor()
        
        html = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Microsoft JhengHei', Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 1000px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #003366; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
                .warning-item {{ background: #f9f9f9; padding: 15px; margin: 15px 0; border-left: 4px solid #0066cc; border-radius: 5px; }}
                .warning-title {{ font-weight: bold; color: #003366; font-size: 16px; }}
                .warning-meta {{ color: #666; font-size: 14px; margin-top: 5px; }}
                .coordinates {{ background: #e3f2fd; padding: 10px; margin-top: 10px; border-radius: 5px; font-family: 'Courier New', monospace; font-size: 13px; }}
                .coord-item {{ margin: 3px 0; }}
                .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
                .source-icon {{ font-size: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌊 海事警告監控報告</h1>
                <p><strong>報告時間：</strong>{(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}(TPE) / {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}(UTC)"</p>
                <p><strong>警告數量：</strong>{len(warnings_data)} 筆</p>
                <hr>
        """
        
        for idx, w in enumerate(warnings_data, 1):
            source_icon = "🇹🇼" if w.get('source') == 'TW_MPB' else "🇨🇳"
            
            # 格式化座標
            coords = w.get('coordinates', [])
            coord_html = ""
            if coords:
                coord_html = '<div class="coordinates"><strong>📍 座標資訊：</strong><br>'
                for i, (lat, lon) in enumerate(coords, 1):
                    lat_dir = 'N' if lat >= 0 else 'S'
                    lon_dir = 'E' if lon >= 0 else 'W'
                    coord_html += f'<div class="coord-item">{i}. {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}</div>'
                coord_html += '</div>'
            
            html += f"""
                <div class="warning-item">
                    <div class="warning-title"><span class="source-icon">{source_icon}</span> {idx}. {w.get('title', 'N/A')}</div>
                    <div class="warning-meta">
                        📋 發布單位：{w.get('bureau', 'N/A')}<br>
                        📅 發布時間：{w.get('time', 'N/A')}<br>
                        🔑 關鍵字：{', '.join(w.get('keywords', [])) if isinstance(w.get('keywords'), list) else w.get('keywords', 'N/A')}<br>
                        🔗 <a href="{w.get('link', '#')}">查看詳情</a>
                    </div>
                    {coord_html}
                </div>
            """
        
        html += """
                <div class="footer">
                    <p>此為自動發送的郵件，請勿直接回覆</p>
                    <p>航行警告監控系統 </p>
                    <p>Navigation Warning Monitor System </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html


# ==================== 5. 台灣航港局爬蟲 ====================
class TWMaritimePortBureauScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, days=0):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        self.coord_extractor = coord_extractor
        
        self.base_url = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
        
        self.days = days
        self.cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.new_warnings = []
        self.captured_warnings_data = []
        
        # 定義要抓取的分類
        self.target_categories = {
            '333': '礙航公告',
            '334': '射擊公告'
        }
        
        print(f"  📅 台灣航港局爬蟲設定: 僅抓取當天資料 ({self.cutoff_date.strftime('%Y-%m-%d')})")
        
        # ========== 初始化 Selenium WebDriver ==========
        print("  🌐 正在啟動 Chrome WebDriver (台灣航港局)...")
        
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        prefs = {
            'profile.default_content_setting_values.notifications': 2,
        }
        options.add_experimental_option('prefs', prefs)
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        try:
            service = Service(ChromeDriverManager().install())
            if platform.system() == 'Windows':
                service.creation_flags = subprocess.CREATE_NO_WINDOW
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(60)
            self.wait = WebDriverWait(self.driver, 20)
            print("  ✅ WebDriver 啟動成功 (台灣航港局)")
            
        except Exception as e:
            print(f"  ❌ WebDriver 啟動失敗: {e}")
            raise
    
    def check_keywords(self, text):
        """檢查關鍵字"""
        if not text:
            return []
        
        matched = []
        for k in self.keywords:
            if k.lower() in text.lower():
                matched.append(k)
        
        # 額外檢查礙航和射擊
        if '礙航' in text and '礙航' not in matched:
            matched.append('礙航')
        if '射擊' in text and '射擊' not in matched:
            matched.append('射擊')
        
        return matched
    
    def parse_date(self, date_string):
        """解析日期 (支援民國年)"""
        try:
            date_string = date_string.strip()
            
            # 處理民國年格式 (例如: 114-01-13 或 2026-01-13)
            date_match = re.match(r'^(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})$', date_string)
            if date_match:
                year = int(date_match.group(1))
                month = int(date_match.group(2))
                day = int(date_match.group(3))
                
                # 判斷是民國年還是西元年
                if year < 1000:  # 民國年
                    year += 1911
                
                return datetime(year, month, day)
            
            return None
        except Exception as e:
            return None
    
    def is_within_date_range(self, date_string):
        """檢查日期範圍"""
        if not date_string:
            return True
        
        parsed_date = self.parse_date(date_string)
        if parsed_date:
            is_valid = parsed_date >= self.cutoff_date
            if not is_valid:
                print(f"          ⏭️ 日期過舊: {date_string}")
            return is_valid
        
        return True
    
    def click_category_tab(self, category_id):
        """點擊分類標籤"""
        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.tabs a"))
            )
            
            if category_id:
                tab_xpath = f"//div[@class='tabs']//a[@data-val='{category_id}']"
                tab = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
            else:
                tab_xpath = "//div[@class='tabs']//a[@class='active']"
                tab = self.driver.find_element(By.XPATH, tab_xpath)
            
            self.driver.execute_script("arguments[0].scrollIntoView(true);", tab)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", tab)
            print(f"    ✅ 已點擊分類標籤")
            time.sleep(3)
            
            return True
            
        except Exception as e:
            print(f"    ⚠️ 點擊分類標籤失敗: {e}")
            return False
    
    def get_notices_selenium(self, page=1, base_category_id=None):
        """使用 Selenium 爬取指定頁面（含座標提取）"""
        try:
            category_name = self.target_categories.get(base_category_id, '全部') if base_category_id else '全部'
            print(f"  正在請求台灣航港局 [{category_name}] 第 {page} 頁...")
            
            if page == 1:
                print(f"    🌐 載入主頁面...")
                self.driver.get(self.base_url)
                time.sleep(3)
                
                if base_category_id:
                    if not self.click_category_tab(base_category_id):
                        return {'has_data': False, 'notices': [], 'processed': 0}
            else:
                try:
                    next_button = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "li.next a"))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                    time.sleep(0.5)
                    self.driver.execute_script("arguments[0].click();", next_button)
                    print(f"    ✅ 已點擊下一頁")
                    time.sleep(3)
                except Exception as e:
                    print(f"    ⚠️ 無法翻頁: {e}")
                    return {'has_data': False, 'notices': [], 'processed': 0}
            
            try:
                self.wait.until(EC.presence_of_element_located((By.ID, "table")))
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#table dl")))
                print(f"    ✅ 頁面內容載入完成")
            except Exception as e:
                print(f"    ⚠️ 等待內容載入超時: {e}")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            table_div = soup.find('div', id='table')
            
            if not table_div:
                print(f"    ⚠️ 找不到 table div")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            contents_div = table_div.find('div', class_='contents')
            if not contents_div:
                print(f"    ⚠️ 找不到 contents div")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            all_dl_list = contents_div.find_all('dl')
            data_dl_list = [dl for dl in all_dl_list if 'con-title' not in dl.get('class', [])]
            
            print(f"    📋 找到 {len(data_dl_list)} 個資料列")
            
            if len(data_dl_list) == 0:
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            processed_count = 0
            
            for idx, dl in enumerate(data_dl_list, 1):
                try:
                    dt_list = dl.find_all('dt')
                    dd = dl.find('dd')
                    
                    if len(dt_list) < 2 or not dd:
                        print(f"    ⚠️ 第 {idx} 列結構不完整")
                        continue
                    
                    processed_count += 1
                    
                    number = dt_list[0].get_text(strip=True)
                    date = dt_list[1].get_text(strip=True)
                    unit = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else '台灣航港局'
                    
                    link_tag = dd.find('a')
                    if link_tag:
                        title = link_tag.get_text(strip=True)
                        link = link_tag.get('href', '')
                        
                        if link and not link.startswith('http'):
                            if link.startswith('/'):
                                link = f"https://www.motcmpb.gov.tw{link}"
                            else:
                                link = f"https://www.motcmpb.gov.tw/{link}"
                    else:
                        title = dd.get_text(strip=True)
                        link = ''
                    
                    print(f"    [{idx}] {number} | {date} | {title[:40]}...")
                    
                    if not self.is_within_date_range(date):
                        continue
                    
                    matched_keywords = self.check_keywords(title)
                    if not matched_keywords:
                        print(f"        ⏭️ 無關鍵字匹配")
                        continue
                    
                    print(f"        ✅ 關鍵字匹配: {', '.join(matched_keywords)}")
                    
                    # ========== 提取座標 ==========
                    print(f"        📍 正在提取座標...")
                    coordinates = []
                    
                    # 1. 從標題提取
                    title_coords = self.coord_extractor.extract_coordinates(title)
                    if title_coords:
                        coordinates.extend(title_coords)
                        print(f"          ✅ 從標題提取到 {len(title_coords)} 個座標")
                    
                    # 2. 從連結頁面提取（台灣航港局特殊處理）
                    if link:
                        try:
                            print(f"          🌐 正在訪問詳細頁面...")
                            
                            self.driver.execute_script("window.open('');")
                            self.driver.switch_to.window(self.driver.window_handles[1])
                            
                            self.driver.get(link)
                            time.sleep(2)
                            
                            detail_soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                            
                            # 台灣航港局的內容區域
                            content_div = (
                                detail_soup.find('div', class_='editor_Content') or
                                detail_soup.find('div', class_='content') or
                                detail_soup.find('div', id='content') or
                                detail_soup.find('article') or
                                detail_soup.find('div', id='container')
                            )
                            
                            if content_div:
                                page_text = content_div.get_text()
                                page_coords = self.coord_extractor.extract_coordinates(page_text)
                                
                                if page_coords:
                                    for pc in page_coords:
                                        if pc not in coordinates:
                                            coordinates.append(pc)
                                    print(f"          ✅ 從頁面提取到 {len(page_coords)} 個座標")
                            
                            self.driver.close()
                            self.driver.switch_to.window(self.driver.window_handles[0])
                            time.sleep(1)
                            
                        except Exception as e:
                            print(f"          ⚠️ 無法從網頁提取座標: {e}")
                            try:
                                if len(self.driver.window_handles) > 1:
                                    self.driver.close()
                                    self.driver.switch_to.window(self.driver.window_handles[0])
                            except:
                                pass
                    
                    if coordinates:
                        print(f"        📍 總共提取到 {len(coordinates)} 個座標")
                    else:
                        print(f"        ℹ️ 未找到座標資訊")
                    
                    # 存入資料庫
                    db_data = (
                        unit,
                        title,
                        link,
                        date,
                        ', '.join(matched_keywords),
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        coordinates
                    )
                    
                    is_new, w_id = self.db_manager.save_warning(db_data, source_type="TW_MPB")
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id,
                            'bureau': unit,
                            'title': title,
                            'link': link,
                            'time': date,
                            'keywords': matched_keywords,
                            'source': 'TW_MPB',
                            'category': category_name,
                            'coordinates': coordinates
                        })
                        print(f"        💾 新資料已存入 (ID: {w_id})")
                    else:
                        print(f"        ℹ️ 資料已存在")
                    
                except Exception as e:
                    print(f"    ⚠️ 處理項目 {idx} 時出錯: {e}")
                    traceback.print_exc()
                    continue
            
            print(f"    📊 處理 {processed_count} 筆")
            
            return {
                'has_data': processed_count > 0,
                'notices': [],
                'processed': processed_count
            }
            
        except Exception as e:
            print(f"  ❌ 請求失敗: {e}")
            traceback.print_exc()
            return {'has_data': False, 'notices': [], 'processed': 0}
    
    def scrape_all_pages(self, max_pages=3):
        """爬取所有頁面"""
        print(f"\n🇹🇼 開始爬取台灣航港局航行警告...")
        print(f"  🌐 目標網址: {self.base_url}")
        
        try:
            for category_id, category_name in self.target_categories.items():
                print(f"\n  📋 爬取分類: {category_name} (ID: {category_id})")
                
                for page in range(1, max_pages + 1):
                    result = self.get_notices_selenium(page, category_id)
                    
                    if not result['has_data']:
                        print(f"    🛑 第 {page} 頁沒有資料，停止")
                        break
                    
                    if result['processed'] < 10:
                        print(f"    ℹ️ 第 {page} 頁資料較少，可能已接近最後一頁")
                    
                    time.sleep(2)
            
        except Exception as e:
            print(f"❌ 台灣航港局爬取過程發生錯誤: {e}")
            traceback.print_exc()
        finally:
            try:
                self.driver.quit()
                print("  🔒 WebDriver 已關閉 (台灣航港局)")
            except:
                pass
        
        print(f"\n🇹🇼 台灣航港局爬取完成，新增 {len(self.new_warnings)} 筆警告")
        return self.new_warnings


# ==================== 6. 中國海事局爬蟲 ====================
class CNMSANavigationWarningsScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, headless=True):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        self.coord_extractor = coord_extractor
        
        print("🇨🇳 初始化中國海事局爬蟲...")
        
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless=new')
        
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        prefs = {'profile.managed_default_content_settings.images': 2}
        options.add_experimental_option('prefs', prefs)
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        try:
            service = Service(ChromeDriverManager().install())
            if platform.system() == 'Windows':
                service.creation_flags = subprocess.CREATE_NO_WINDOW
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(120)
            self.wait = WebDriverWait(self.driver, 15)
            print("  ✅ WebDriver 啟動成功")
            
        except Exception as e:
            print(f"  ❌ WebDriver 啟動失敗: {e}")
            raise
        
        self.three_days_ago = datetime.now() - timedelta(days=3)
        self.new_warnings = []
        self.captured_warnings_data = []
    
    def check_keywords(self, text):
        return [k for k in self.keywords if k.lower() in text.lower()]
    
    def parse_date(self, date_str):
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日']:
            try: 
                return datetime.strptime(date_str.strip(), fmt)
            except: 
                continue
        return None
    
    def scrape_bureau_warnings(self, bureau_name, bureau_element):
        """抓取單一海事局警告（含座標提取，修正 Stale Element）"""
        print(f"  🔍 抓取: {bureau_name}")
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", bureau_element)
            time.sleep(2)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
            
            processed_count = 0
            max_items = 100
            
            while processed_count < max_items:
                try:
                    items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a")
                    
                    if processed_count >= len(items):
                        break
                    
                    item = items[processed_count]
                    
                    try:
                        title = item.get_attribute('title') or item.text.strip()
                        title = re.sub(r'\s*\d{4}-\d{2}-\d{2}\s*$', '', title)
                        if not title:
                            processed_count += 1
                            continue

                        matched = self.check_keywords(title)
                        if not matched:
                            processed_count += 1
                            continue

                        link = item.get_attribute('href') or ''
                        if link.startswith('/'):
                            link = f"https://www.msa.gov.cn{link}"
                        
                        try:
                            publish_time = item.find_element(By.CSS_SELECTOR, ".time").text.strip()
                        except:
                            match = re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text)
                            publish_time = match.group() if match else ""

                        if publish_time:
                            p_date = self.parse_date(publish_time)
                            if p_date:
                                today = datetime.now().date()
                                if p_date.date() != today:
                                    print(f"      ⏭️ 非當天日期: {publish_time}")
                                    processed_count += 1
                                    continue
                                else:
                                    print(f"      ✅ 當天日期: {publish_time}")
                            else:
                                print(f"      ⚠️ 無法解析日期: {publish_time}")
                                processed_count += 1
                                continue
                        else:
                            print(f"      ⚠️ 無日期資訊")
                            processed_count += 1
                            continue
                        
                        # ========== 提取座標 ==========
                        print(f"    📍 正在提取座標: {title[:40]}...")
                        coordinates = []
                        
                        # 從標題提取
                        title_coords = self.coord_extractor.extract_coordinates(title)
                        if title_coords:
                            coordinates.extend(title_coords)
                            print(f"      ✅ 從標題提取到 {len(title_coords)} 個座標")
                        
                        # 從連結頁面提取（中國海事局專用）
                        if link and not link.startswith('javascript'):
                            try:
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", item)
                                time.sleep(0.5)
                                self.driver.execute_script("arguments[0].click();", item)
                                time.sleep(2)
                                
                                try:
                                    # 使用增強版 HTML 提取
                                    page_html = self.driver.page_source
                                    page_coords = self.coord_extractor.extract_from_html(page_html)
                                    
                                    if page_coords:
                                        for pc in page_coords:
                                            if pc not in coordinates:
                                                coordinates.append(pc)
                                        print(f"      ✅ 從頁面提取到 {len(page_coords)} 個座標")
                                except Exception as e:
                                    print(f"      ⚠️ 頁面內容提取失敗: {e}")
                                
                                self.driver.back()
                                time.sleep(2)
                                self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
                                
                            except Exception as e:
                                print(f"      ⚠️ 無法從網頁提取座標: {e}")
                                try:
                                    self.driver.back()
                                    time.sleep(2)
                                except:
                                    pass
                        
                        if coordinates:
                            print(f"      📍 總共提取到 {len(coordinates)} 個座標")
                        else:
                            print(f"      ⚠️ 未找到座標資訊")
                        
                        # 存入資料庫
                        db_data = (
                            bureau_name,
                            title,
                            link,
                            publish_time,
                            ', '.join(matched),
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            coordinates
                        )
                        
                        is_new, w_id = self.db_manager.save_warning(db_data, source_type="CN_MSA")
                        
                        if is_new and w_id:
                            self.new_warnings.append(w_id)
                            self.captured_warnings_data.append({
                                'id': w_id,
                                'bureau': bureau_name,
                                'title': title,
                                'link': link,
                                'time': publish_time,
                                'keywords': matched,
                                'source': 'CN_MSA',
                                'coordinates': coordinates
                            })
                            print(f"      ✅ 新警告: {title[:40]}...")
                        else:
                            print(f"      ⏭️ 已存在")
                    
                    except Exception as e:
                        print(f"    ⚠️ 處理項目 {processed_count + 1} 時出錯: {e}")
                    
                    processed_count += 1
                    
                except Exception as e:
                    print(f"    ⚠️ 獲取項目列表時出錯: {e}")
                    break
            
            print(f"    ✅ {bureau_name} 處理完成，共處理 {processed_count} 個項目")
                        
        except Exception as e:
            print(f"  ❌ 抓取 {bureau_name} 錯誤: {e}")
    
    def scrape_all_bureaus(self):
        """爬取所有海事局"""
        print(f"\n🇨🇳 開始爬取中國海事局航行警告...")
        
        try:
            print("  📡 正在載入中國海事局網站...")
            self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
            time.sleep(5)
            
            print("  🖱️ 點擊航行警告選項...")
            nav_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '航行警告')]"))
            )
            self.driver.execute_script("arguments[0].click();", nav_btn)
            time.sleep(3)
            
            print("  📋 獲取海事局列表...")
            bureaus = [
                b.text.strip() 
                for b in self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text") 
                if b.text.strip()
            ]
            
            print(f"  📍 找到 {len(bureaus)} 個海事局")
            
            for b_name in bureaus:
                try:
                    elem = self.driver.find_element(
                        By.XPATH, 
                        f"//div[@class='nav_lv2_text' and contains(text(), '{b_name}')]"
                    )
                    self.scrape_bureau_warnings(b_name, elem)
                    time.sleep(1)
                except Exception as e:
                    print(f"    ⚠️ 跳過 {b_name}: {e}")
                    continue
            
        except Exception as e:
            print(f"❌ 中國海事局爬取錯誤: {e}")
            traceback.print_exc()
        finally:
            try:
                self.driver.quit()
            except:
                pass
        
        print(f"🇨🇳 中國海事局爬取完成，新增 {len(self.new_warnings)} 筆警告")
        return self.new_warnings


# ==================== 環境變數讀取 ====================
print("📋 正在讀取環境變數...")

TEAMS_WEBHOOK = os.getenv("TEAMS_WEBHOOK_URL", "")
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
TARGET_EMAIL = os.getenv("TARGET_EMAIL", "")
MAIL_SMTP_SERVER = os.getenv("MAIL_SMTP_SERVER", "smtp.gmail.com")
MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "587"))

DB_FILE_PATH = os.getenv("DB_FILE_PATH", "navigation_warnings.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
MAX_BACKUP_FILES = int(os.getenv("MAX_BACKUP_FILES", "7"))

SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "3600"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

KEYWORDS_CONFIG = os.getenv("KEYWORDS_CONFIG", "keywords_config.json")
CHROME_HEADLESS = os.getenv("CHROME_HEADLESS", "true").lower() == "true"

ENABLE_EMAIL_NOTIFICATIONS = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "true").lower() == "true"
ENABLE_TEAMS_NOTIFICATIONS = os.getenv("ENABLE_TEAMS_NOTIFICATIONS", "true").lower() == "true"

ENABLE_CN_MSA = os.getenv("ENABLE_CN_MSA", "true").lower() == "true"
ENABLE_TW_MPB = os.getenv("ENABLE_TW_MPB", "true").lower() == "true"

print("\n" + "="*70)
print("⚙️  系統設定檢查")
print("="*70)
print(f"📧 Email 通知: {'✅ 啟用' if ENABLE_EMAIL_NOTIFICATIONS and MAIL_USER else '❌ 停用'}")
print(f"📢 Teams 通知: {'✅ 啟用' if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK else '❌ 停用'}")
print(f"💾 資料庫: {DB_FILE_PATH}")
print(f"🔍 資料來源: CN_MSA={'✅' if ENABLE_CN_MSA else '❌'} | TW_MPB={'✅' if ENABLE_TW_MPB else '❌'}")
print("="*70 + "\n")


# ==================== 8. 主程式進入點 ====================
if __name__ == "__main__":
    try:
        print("\n" + "="*70)
        print("🌊 海事警告監控系統啟動")
        print("="*70)
        
        # 初始化資料庫管理器
        print("\n📦 初始化資料庫...")
        db_manager = DatabaseManager(db_name=DB_FILE_PATH)
        print(f"  ✅ 資料庫初始化成功: {DB_FILE_PATH}")
        
        # 初始化關鍵字管理器
        print("🔑 初始化關鍵字管理器...")
        keyword_manager = KeywordManager(config_file=KEYWORDS_CONFIG)
        
        # 初始化座標提取器
        print("🗺️  初始化座標提取器...")
        coord_extractor = CoordinateExtractor()
        
        # 初始化 Teams 通知器
        teams_notifier = None
        if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK:
            print("📢 初始化 Teams 通知器...")
            teams_notifier = UnifiedTeamsNotifier(TEAMS_WEBHOOK)
        
        # 初始化 Email 通知器
        email_notifier = None
        if ENABLE_EMAIL_NOTIFICATIONS and all([MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL]):
            print("📧 初始化 Email 通知器...")
            email_notifier = GmailRelayNotifier(MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL)
        
        # 初始化爬蟲
        cn_scraper = None
        tw_scraper = None
        
        if ENABLE_CN_MSA:
            print("🇨🇳 初始化中國海事局爬蟲...")
            cn_scraper = CNMSANavigationWarningsScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                headless=CHROME_HEADLESS
            )
        
        if ENABLE_TW_MPB:
            print("🇹🇼 初始化台灣航港局爬蟲...")
            tw_scraper = TWMaritimePortBureauScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                days=3
            )
        
        print("\n" + "="*70)
        print("✅ 所有模組初始化完成")
        print("="*70)
        
        # ========== 開始爬取 ==========
        print("\n🚀 開始爬取海事警告...")
        
        all_new_warnings = []
        all_captured_data = []
        
        # 爬取中國海事局
        if cn_scraper:
            print("\n🇨🇳 爬取中國海事局...")
            cn_warnings = cn_scraper.scrape_all_bureaus()
            all_new_warnings.extend(cn_warnings)
            all_captured_data.extend(cn_scraper.captured_warnings_data)
        
        # 爬取台灣航港局
        if tw_scraper:
            print("\n🇹🇼 爬取台灣航港局...")
            tw_warnings = tw_scraper.scrape_all_pages()
            all_new_warnings.extend(tw_warnings)
            all_captured_data.extend(tw_scraper.captured_warnings_data)
        
        # ========== 發送通知 ==========
        if all_new_warnings:
            print(f"\n📢 發現 {len(all_new_warnings)} 個新警告，準備發送通知...")
            
            # Teams 通知
            if teams_notifier and ENABLE_TEAMS_NOTIFICATIONS:
                # 分別發送中國和台灣的警告
                cn_warnings_data = [w for w in all_captured_data if w.get('source') == 'CN_MSA']
                tw_warnings_data = [w for w in all_captured_data if w.get('source') == 'TW_MPB']
                
                if cn_warnings_data:
                    print("\n📤 發送中國海事局通知...")
                    cn_list = [(
                        w.get('id'),
                        w.get('bureau'),
                        w.get('title'),
                        w.get('link'),
                        w.get('time'),
                        ', '.join(w.get('keywords', [])) if isinstance(w.get('keywords'), list) else w.get('keywords', ''),
                        '',
                        json.dumps(w.get('coordinates', []))
                    ) for w in cn_warnings_data]
                    teams_notifier.send_batch_notification(cn_list, "CN_MSA")
                
                if tw_warnings_data:
                    print("\n📤 發送台灣航港局通知...")
                    tw_list = [(
                        w.get('id'),
                        w.get('bureau'),
                        w.get('title'),
                        w.get('link'),
                        w.get('time'),
                        ', '.join(w.get('keywords', [])) if isinstance(w.get('keywords'), list) else w.get('keywords', ''),
                        '',
                        json.dumps(w.get('coordinates', []))
                    ) for w in tw_warnings_data]
                    teams_notifier.send_batch_notification(tw_list, "TW_MPB")
            
            # Email 通知
            if email_notifier and ENABLE_EMAIL_NOTIFICATIONS:
                print("\n📧 發送 Email 通知...")
                email_notifier.send_trigger_email(all_captured_data)
        else:
            print("\n✅ 沒有新的警告")
        
        # ========== 生成摘要 ==========
        print("\n" + "="*70)
        print("📊 執行摘要")
        print("="*70)
        
        cn_count = len([w for w in all_captured_data if w.get('source') == 'CN_MSA'])
        tw_count = len([w for w in all_captured_data if w.get('source') == 'TW_MPB'])
        total_coords = sum(len(w.get('coordinates', [])) for w in all_captured_data)
        
        print(f"🇨🇳 中國海事局: {cn_count} 筆新警告")
        print(f"🇹🇼 台灣航港局: {tw_count} 筆新警告")
        print(f"📍 總座標點數: {total_coords}")
        
        # 顯示資料庫統計
        print("\n" + "="*70)
        db_manager.print_statistics()
        
        print("\n" + "="*70)
        print("🎉 系統執行完成")
        print("="*70)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
    except Exception as e:
        print(f"\n❌ 執行失敗: {e}")
        traceback.print_exc()
