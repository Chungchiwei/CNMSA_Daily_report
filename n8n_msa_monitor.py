#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一海事警告監控系統 (中國海事局 + 台灣航港局 + UKMTO)
支援經緯度提取、Teams 通知、Email 報告
版本: 3.0 - 新增 UKMTO 航行警告來源
"""

import platform
import subprocess
import os
import sys
import ssl
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

# ==================== 1. 全域初始化 ====================
# 停用警告 & SSL 繞過（企業網路自簽憑證）
ssl._create_default_https_context = ssl._create_unverified_context
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
        self.patterns = [
            r'(\d{1,3})-(\d{1,2}\.\d+)\s*([NSns北南])\s+(\d{1,3})-(\d{1,2}\.\d+)\s*([EWew東西])',
            r'(\d{1,3})-(\d{1,2})\s*([NSns北南])\s+(\d{1,3})-(\d{1,2})\s*([EWew東西])',
            r'(\d{1,3})[°度]\s*(\d{1,2})[\'′分]?\s*([NSns北南])\s+(\d{1,3})[°度]\s*(\d{1,2})[\'′分]?\s*([EWew東西])',
            r'(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([NSns北南])\s+(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([EWew東西])',
            r'([NSns北南])\s*(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s+([EWew東西])\s*(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?',
            r'(\d{1,3}\.\d+)\s*[°度]?\s*([NSns北南])\s+(\d{1,3}\.\d+)\s*[°度]?\s*([EWew東西])',
            r'[北南緯]\s*(\d{1,3})\s*度\s*(\d{1,2})\s*分\s+[東西經]\s*(\d{1,3})\s*度\s*(\d{1,2})\s*分',
        ]
        print("  🗺️ 座標提取器初始化完成")

    def extract_coordinates(self, text):
        coordinates = []
        if not text:
            return coordinates
        text = text.replace('、', ' ').replace('，', ' ').replace('。', ' ')
        for pattern in self.patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    coord = self._parse_match(match, pattern)
                    if coord and self._validate_coordinate(coord):
                        coordinates.append(coord)
                except Exception:
                    continue
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
        groups = match.groups()
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
        if not coord or len(coord) != 2:
            return False
        lat, lon = coord
        if lat < -90 or lat > 90:
            return False
        if lon < -180 or lon > 180:
            return False
        if not (-60 <= lat <= 60 and 60 <= lon <= 180):
            return False
        return True

    def extract_from_html(self, html_content):
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            content_div = soup.find('div', {'class': 'text', 'id': 'ch_p'})
            if content_div:
                text = content_div.get_text()
                return self.extract_coordinates(text)
            return self.extract_coordinates(html_content)
        except Exception as e:
            print(f"    ⚠️ HTML 解析失敗: {e}")
            return []

    def format_coordinates(self, coordinates):
        if not coordinates:
            return "無座標資訊"
        formatted = []
        for lat, lon in coordinates:
            lat_dir = 'N' if lat >= 0 else 'S'
            lon_dir = 'E' if lon >= 0 else 'W'
            formatted.append(f"{abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}")
        return " | ".join(formatted)


# ==================== 3. 統一 Teams 通知系統 (增強版) ====================
class UnifiedTeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def _fix_url(self, url, base_domain=""):
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

    def send_batch_notification(self, warnings_list, source_type="CN_MSA", is_today=True):
        """
        發送批量警告通知 (含座標資訊，區分今日/歷史)
        source_type: CN_MSA / TW_MPB / UKMTO
        """
        if not self.webhook_url or not warnings_list:
            return False

        try:
            # 根據來源設定圖示和名稱
            source_config = {
                "TW_MPB": {
                    "icon": "🇹🇼",
                    "name": "台灣航港局",
                    "home_url": "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483",
                    "base_domain": "https://www.motcmpb.gov.tw"
                },
                "UKMTO": {
                    "icon": "🇬🇧",
                    "name": "UKMTO 航行警告",
                    "home_url": "https://www.ukmto.org/recent-incidents",
                    "base_domain": "https://www.ukmto.org"
                },
                "CN_MSA": {
                    "icon": "🇨🇳",
                    "name": "中國海事局",
                    "home_url": "https://www.msa.gov.cn/page/outter/weather.jsp",
                    "base_domain": "https://www.msa.gov.cn"
                },
            }
            cfg = source_config.get(source_type, source_config["CN_MSA"])
            source_icon = cfg["icon"]
            source_name = cfg["name"]
            home_url    = cfg["home_url"]
            base_domain = cfg["base_domain"]

            time_badge   = "🆕 今日新增" if is_today else "📚 歷史資料 (近30天)"
            title_color  = "Attention" if is_today else "Good"

            body_elements = [
                {
                    "type": "TextBlock",
                    "text": f"{source_icon} **{source_name}** | {time_badge}",
                    "size": "Medium",
                    "weight": "Bolder",
                    "color": title_color
                },
                {
                    "type": "TextBlock",
                    "text": f"發現 **{len(warnings_list)}** 個航行警告",
                    "size": "Medium"
                },
                {
                    "type": "TextBlock",
                    "text": "━━━━━━━━━━━━━━━━━━━━",
                    "wrap": True
                }
            ]

            actions = []

            for idx, w in enumerate(warnings_list[:8], 1):
                _, bureau, title, link, pub_time, _, _, coordinates = w
                fixed_link = self._fix_url(link, base_domain)

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

            card_title = f"{'🚨' if is_today else '📋'} {source_name} - {time_badge} ({len(warnings_list)})"
            payload = self._create_adaptive_card(card_title, body_elements, actions)

            print(f"  📤 正在發送 Teams 通知 [{time_badge}] 到: {self.webhook_url[:50]}...")

            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                verify=False
            )

            if response.status_code in [200, 202]:
                print(f"✅ {source_name} Teams 通知發送成功 [{time_badge}] ({len(warnings_list)} 筆)")
                return True
            else:
                print(f"❌ {source_name} Teams 通知失敗: HTTP {response.status_code}")
                print(f"   回應內容: {response.text[:200]}")
                return False

        except requests.exceptions.SSLError as e:
            print(f"❌ Teams SSL 錯誤: {e}")
            return False
        except requests.exceptions.Timeout as e:
            print(f"❌ Teams 連線逾時: {e}")
            return False
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Teams 連線錯誤: {e}")
            return False
        except Exception as e:
            print(f"❌ Teams 發送失敗: {e}")
            traceback.print_exc()
            return False


# ==================== 4. Email 通知系統 (增強版) ====================
class GmailRelayNotifier:
    """Gmail SMTP 郵件通知系統"""

    def __init__(self, mail_user, mail_pass, target_email):
        self.mail_user    = mail_user
        self.mail_pass    = mail_pass
        self.target_email = target_email
        self.smtp_server  = "smtp.gmail.com"
        self.smtp_port    = 587

        if not all([mail_user, mail_pass, target_email]):
            print("⚠️ Email 通知未完整設定")
            self.enabled = False
        else:
            self.enabled = True
            print("✅ Email 通知系統已啟用")

    def send_trigger_email(self, today_warnings, history_warnings):
        if not self.enabled:
            print("ℹ️ Email 通知未啟用")
            return False
        try:
            msg = MIMEMultipart('related')
            total_count = len(today_warnings) + len(history_warnings)
            today_count = len(today_warnings)
            msg['Subject'] = (
                f"🌊 航行警告監控報告 - 共{total_count}筆 (今日{today_count}筆) - "
                f"{(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}(TPE)"
            )
            msg['From'] = self.mail_user
            msg['To']   = self.target_email

            html_content = self._generate_html_report(today_warnings, history_warnings)
            msg_alternative = MIMEMultipart('alternative')
            msg.attach(msg_alternative)
            msg_alternative.attach(MIMEText(html_content, 'html', 'utf-8'))

            print(f"📧 正在發送郵件至 {self.target_email}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            print("✅ 郵件發送成功")
            return True
        except Exception as e:
            print(f"❌ 郵件發送失敗: {e}")
            traceback.print_exc()
            return False

    def _source_icon(self, source):
        return {"TW_MPB": "🇹🇼", "UKMTO": "🇬🇧"}.get(source, "🇨🇳")

    def _generate_html_report(self, today_warnings, history_warnings):
        total_count = len(today_warnings) + len(history_warnings)

        html = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Microsoft JhengHei', Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 1000px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #003366; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
                h2 {{ color: #0066cc; margin-top: 30px; padding: 10px; background: #f0f8ff; border-left: 4px solid #0066cc; }}
                .summary {{ background: #e3f2fd; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .summary-item {{ display: inline-block; margin: 5px 15px 5px 0; font-weight: bold; }}
                .warning-item {{ background: #f9f9f9; padding: 15px; margin: 15px 0; border-left: 4px solid #0066cc; border-radius: 5px; }}
                .warning-item.today {{ border-left-color: #ff6b6b; background: #fff5f5; }}
                .warning-item.history {{ border-left-color: #51cf66; background: #f0fff4; }}
                .warning-title {{ font-weight: bold; color: #003366; font-size: 16px; }}
                .warning-meta {{ color: #666; font-size: 14px; margin-top: 5px; }}
                .coordinates {{ background: #e3f2fd; padding: 10px; margin-top: 10px; border-radius: 5px; font-family: 'Courier New', monospace; font-size: 13px; }}
                .coord-item {{ margin: 3px 0; }}
                .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
                .badge {{ display: inline-block; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; margin-left: 10px; }}
                .badge.today {{ background: #ff6b6b; color: white; }}
                .badge.history {{ background: #51cf66; color: white; }}
                .badge.ukmto {{ background: #6c5ce7; color: white; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌊 海事警告監控報告</h1>
                <div class="summary">
                    <div class="summary-item">📅 報告時間：{(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')} (TPE)</div><br>
                    <div class="summary-item">📊 總警告數：{total_count} 筆</div>
                    <div class="summary-item">🆕 今日新增：{len(today_warnings)} 筆</div>
                    <div class="summary-item">📚 歷史資料：{len(history_warnings)} 筆</div>
                </div>
        """

        def _render_warnings(warnings_list, badge_class, badge_label):
            result = ""
            for idx, w in enumerate(warnings_list, 1):
                source = w.get('source', '')
                icon   = self._source_icon(source)
                coords = w.get('coordinates', [])
                coord_html = ""
                if coords:
                    coord_html = '<div class="coordinates"><strong>📍 座標資訊：</strong><br>'
                    for i, (lat, lon) in enumerate(coords, 1):
                        lat_dir = 'N' if lat >= 0 else 'S'
                        lon_dir = 'E' if lon >= 0 else 'W'
                        coord_html += f'<div class="coord-item">{i}. {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}</div>'
                    coord_html += '</div>'

                # UKMTO 特有欄位
                extra_meta = ""
                if source == "UKMTO":
                    colour = w.get('colour', '')
                    colour_icon = "🔴" if colour == "Red" else "🟡"
                    extra_meta = f"⚠️ 警示等級：{colour_icon} {colour}<br>"

                kw = w.get('keywords', [])
                kw_str = ', '.join(kw) if isinstance(kw, list) else str(kw)

                result += f"""
                    <div class="warning-item {badge_class}">
                        <div class="warning-title">
                            <span>{icon}</span> {idx}. {w.get('title', 'N/A')}
                            <span class="badge {badge_class}">{badge_label}</span>
                        </div>
                        <div class="warning-meta">
                            📋 發布單位：{w.get('bureau', 'N/A')}<br>
                            📅 發布時間：{w.get('time', 'N/A')}<br>
                            {extra_meta}
                            🔑 關鍵字：{kw_str}<br>
                            🔗 <a href="{w.get('link', '#')}">查看詳情</a>
                        </div>
                        {coord_html}
                    </div>
                """
            return result

        if today_warnings:
            html += f"<h2>🆕 今日新增警告 ({len(today_warnings)} 筆)</h2>"
            html += _render_warnings(today_warnings, "today", "今日")

        if history_warnings:
            html += f"<h2>📚 歷史資料 ({len(history_warnings)} 筆)</h2>"
            html += _render_warnings(history_warnings, "history", "歷史")

        html += """
                <div class="footer">
                    <p>此為自動發送的郵件，請勿直接回覆</p>
                    <p>航行警告監控系統 v3.0 | Navigation Warning Monitor System</p>
                </div>
            </div>
        </body>
        </html>
        """
        return html


# ==================== 5. UKMTO 爬蟲 ====================
class UKMTOScraper:
    """
    爬取 UKMTO (United Kingdom Maritime Trade Operations) 航行警告
    來源: https://www.ukmto.org/recent-incidents
    篩選: 過去 N 天 (預設 30 天)
    """

    URL = "https://www.ukmto.org/recent-incidents"

    # UKMTO 頁面日期格式: "2 March 2026"
    MONTH_MAP = {
        "January": 1, "February": 2, "March": 3,    "April": 4,
        "May": 5,     "June": 6,     "July": 7,      "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }

    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, days=30):
        self.db_manager       = db_manager
        self.keyword_manager  = keyword_manager
        self.keywords         = keyword_manager.get_keywords()
        self.teams_notifier   = teams_notifier
        self.coord_extractor  = coord_extractor
        self.days             = days

        now = datetime.now(tz=timezone.utc)
        self.cutoff_date  = now - timedelta(days=days)
        self.today_start  = now.replace(hour=0, minute=0, second=0, microsecond=0)

        self.new_warnings_today   = []
        self.new_warnings_history = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []

        print(f"  🇬🇧 UKMTO 爬蟲設定:")
        print(f"     - 抓取範圍: 最近 {days} 天 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
        print(f"     - 今日定義: {self.today_start.strftime('%Y-%m-%d')} 00:00 UTC 起")

        # ── 初始化 WebDriver ──
        print("  🌐 正在啟動 Chrome WebDriver (UKMTO)...")
        self.driver = self._init_driver()
        self.wait   = WebDriverWait(self.driver, 20)
        print("  ✅ WebDriver 啟動成功 (UKMTO)")

    # ------------------------------------------------------------------
    # WebDriver 初始化（含 SSL 繞過 & 自動尋找 chromedriver）
    # ------------------------------------------------------------------
    def _init_driver(self) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors=yes")
        options.add_argument("--allow-insecure-localhost")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        driver_path = self._find_chromedriver()
        if driver_path:
            service = Service(executable_path=driver_path)
        else:
            service = Service()  # 從 PATH 尋找

        if platform.system() == 'Windows':
            service.creation_flags = subprocess.CREATE_NO_WINDOW

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        return driver

    def _find_chromedriver(self) -> str | None:
        """依序嘗試多種方式取得 chromedriver 路徑"""
        # 1. 環境變數
        env_path = os.environ.get("CHROMEDRIVER_PATH")
        if env_path and os.path.exists(env_path):
            return env_path

        # 2. 常見 Windows 路徑
        common_paths = [
            r"C:\chromedriver\chromedriver.exe",
            r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chromedriver.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "chromedriver.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Downloads", "chromedriver.exe"),
            "chromedriver.exe",
            "chromedriver",
        ]
        for p in common_paths:
            if p and os.path.exists(p):
                return p

        # 3. webdriver_manager（SSL 已繞過）
        try:
            path = ChromeDriverManager().install()
            return path
        except Exception as e:
            print(f"  ⚠️  webdriver_manager 失敗: {e}")

        return None

    # ------------------------------------------------------------------
    # 日期解析
    # ------------------------------------------------------------------
    def _parse_date(self, date_str: str) -> datetime | None:
        """將 '2 March 2026' 解析為 UTC-aware datetime"""
        parts = date_str.strip().split()
        if len(parts) != 3:
            return None
        try:
            day   = int(parts[0])
            month = self.MONTH_MAP.get(parts[1])
            year  = int(parts[2])
            if not month:
                return None
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 主要爬取邏輯
    # ------------------------------------------------------------------
    def scrape(self):
        """爬取 UKMTO 過去 N 天的航行警告"""
        print(f"\n🇬🇧 開始爬取 UKMTO 航行警告...")
        print(f"  🌐 目標網址: {self.URL}")

        try:
            self.driver.get(self.URL)

            # 等待事件列表載入
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.IncidentList_incidentList__NGsl0")
                )
            )
            print("  ✅ 頁面載入完成，開始解析...")
            time.sleep(2)

            li_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ul.IncidentList_incidentList__NGsl0 > li.IncidentList_incident__HgGtN"
            )
            print(f"  📋 共找到 {len(li_elements)} 筆事件，篩選最近 {self.days} 天...")

            for elem in li_elements:
                try:
                    self._process_incident(elem)
                except Exception as e:
                    print(f"  ⚠️ 處理事件時出錯: {e}")
                    continue

        except Exception as e:
            print(f"  ❌ UKMTO 爬取錯誤: {e}")
            traceback.print_exc()
        finally:
            try:
                self.driver.quit()
                print("  🔒 WebDriver 已關閉 (UKMTO)")
            except:
                pass

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇬🇧 UKMTO 爬取完成:")
        print(f"   🆕 今日新增: {len(self.new_warnings_today)} 筆")
        print(f"   📚 歷史資料: {len(self.new_warnings_history)} 筆")
        print(f"   📊 總計: {total_new} 筆")

        return {
            'today':   self.new_warnings_today,
            'history': self.new_warnings_history
        }

    def _process_incident(self, elem):
        """處理單一事件 <li> 元素"""

        # ── 事件 ID ──
        incident_id = elem.get_attribute("id") or "N/A"

        # ── 標題 ──
        try:
            title = elem.find_element(
                By.CSS_SELECTOR, "h3.IncidentList_title__cOmOY button"
            ).text.strip()
        except Exception:
            title = "N/A"

        # ── 警示顏色 (Red / Yellow) ──
        try:
            colour = elem.find_element(
                By.CSS_SELECTOR, "span.Pin_pin__dpf_F"
            ).get_attribute("data-colour") or "N/A"
        except Exception:
            colour = "N/A"

        # ── 日期 ──
        try:
            date_str = elem.find_element(
                By.CSS_SELECTOR, "ul.IncidentList_meta__JmhSj li span"
            ).text.strip()
            incident_date = self._parse_date(date_str)
        except Exception:
            date_str = "N/A"
            incident_date = None

        # ── 內容 ──
        try:
            details = elem.find_element(
                By.CSS_SELECTOR, "p.IncidentList_details__bwUAz"
            ).text.strip()
        except Exception:
            details = "N/A"

        # ── 日期篩選 ──
        if incident_date is None:
            print(f"  ⚠️  跳過（日期無法解析）：{title}")
            return

        if incident_date < self.cutoff_date:
            # 列表為時間倒序，超過截止日即可停止
            raise StopIteration(f"超出範圍，停止（{date_str}）")

        is_today   = incident_date >= self.today_start
        time_label = "🆕 今日" if is_today else "📚 歷史"
        colour_icon = "🔴" if colour == "Red" else "🟡"
        print(f"  {time_label} {colour_icon} [{date_str}] {title}")

        # ── 座標提取（從 details 文字）──
        coordinates = self.coord_extractor.extract_coordinates(details)
        if coordinates:
            print(f"    📍 從內容提取到 {len(coordinates)} 個座標")

        # ── 關鍵字比對（UKMTO 標題本身即為類型，直接用標題 + 內容）──
        matched_keywords = [k for k in self.keywords if k.lower() in (title + " " + details).lower()]
        # 若無任何關鍵字命中，仍保留（UKMTO 本身即為航行警告，全部收錄）
        if not matched_keywords:
            matched_keywords = ["UKMTO"]

        # ── 存入資料庫 ──
        db_data = (
            "UKMTO",          # bureau
            title,
            self.URL,         # link（UKMTO 無個別頁面連結，指向列表頁）
            date_str,
            ', '.join(matched_keywords),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            coordinates
        )

        is_new, w_id = self.db_manager.save_warning(db_data, source_type="UKMTO")

        if is_new and w_id:
            warning_data = {
                'id':          w_id,
                'bureau':      "UKMTO",
                'title':       title,
                'link':        self.URL,
                'time':        date_str,
                'keywords':    matched_keywords,
                'source':      'UKMTO',
                'colour':      colour,       # Red / Yellow（UKMTO 特有）
                'coordinates': coordinates
            }

            if is_today:
                self.new_warnings_today.append(w_id)
                self.captured_warnings_today.append(warning_data)
                print(f"    💾 新資料已存入 [今日] (ID: {w_id})")
            else:
                self.new_warnings_history.append(w_id)
                self.captured_warnings_history.append(warning_data)
                print(f"    💾 新資料已存入 [歷史] (ID: {w_id})")
        else:
            print(f"    ℹ️  資料已存在")

    # ------------------------------------------------------------------
    # 讓 scrape() 能正確捕捉 StopIteration
    # ------------------------------------------------------------------
    def scrape(self):
        print(f"\n🇬🇧 開始爬取 UKMTO 航行警告...")
        print(f"  🌐 目標網址: {self.URL}")

        try:
            self.driver.get(self.URL)
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.IncidentList_incidentList__NGsl0")
                )
            )
            print("  ✅ 頁面載入完成，開始解析...")
            time.sleep(2)

            li_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ul.IncidentList_incidentList__NGsl0 > li.IncidentList_incident__HgGtN"
            )
            print(f"  📋 共找到 {len(li_elements)} 筆事件，篩選最近 {self.days} 天...")

            for elem in li_elements:
                try:
                    self._process_incident(elem)
                except StopIteration as si:
                    print(f"  ⏭️  {si}")
                    break
                except Exception as e:
                    print(f"  ⚠️  處理事件時出錯: {e}")
                    continue

        except Exception as e:
            print(f"  ❌ UKMTO 爬取錯誤: {e}")
            traceback.print_exc()
        finally:
            try:
                self.driver.quit()
                print("  🔒 WebDriver 已關閉 (UKMTO)")
            except:
                pass

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇬🇧 UKMTO 爬取完成:")
        print(f"   🆕 今日新增: {len(self.new_warnings_today)} 筆")
        print(f"   📚 歷史資料: {len(self.new_warnings_history)} 筆")
        print(f"   📊 總計: {total_new} 筆")

        return {
            'today':   self.new_warnings_today,
            'history': self.new_warnings_history
        }


# ==================== 6. 台灣航港局爬蟲 (增強版) ====================
class TWMaritimePortBureauScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, days=3):
        self.db_manager      = db_manager
        self.keyword_manager = keyword_manager
        self.keywords        = keyword_manager.get_keywords()
        self.teams_notifier  = teams_notifier
        self.coord_extractor = coord_extractor

        self.base_url    = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
        self.days        = days
        self.cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []

        self.target_categories = {'333': '礙航公告', '334': '射擊公告'}

        print(f"  📅 台灣航港局爬蟲設定:")
        print(f"     - 抓取範圍: 最近 {days} 天 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
        print(f"     - 今日定義: {self.today_start.strftime('%Y-%m-%d')} 00:00 起")

        print("  🌐 正在啟動 Chrome WebDriver (台灣航港局)...")
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        prefs = {'profile.default_content_setting_values.notifications': 2}
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
        if not text:
            return []
        matched = []
        for k in self.keywords:
            if k.lower() in text.lower():
                matched.append(k)
        if '礙航' in text and '礙航' not in matched:
            matched.append('礙航')
        if '射擊' in text and '射擊' not in matched:
            matched.append('射擊')
        return matched

    def parse_date(self, date_string):
        try:
            date_string = date_string.strip()
            date_match = re.match(r'^(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})$', date_string)
            if date_match:
                year  = int(date_match.group(1))
                month = int(date_match.group(2))
                day   = int(date_match.group(3))
                if year < 1000:
                    year += 1911
                return datetime(year, month, day)
            return None
        except Exception:
            return None

    def is_within_date_range(self, date_string):
        if not date_string:
            return None, False
        parsed_date = self.parse_date(date_string)
        if parsed_date:
            if parsed_date < self.cutoff_date:
                return None, False
            is_today = parsed_date >= self.today_start
            return parsed_date, is_today
        return None, False

    def click_category_tab(self, category_id):
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.tabs a")))
            if category_id:
                tab_xpath = f"//div[@class='tabs']//a[@data-val='{category_id}']"
                tab = self.wait.until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
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
                    next_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.next a")))
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
                return {'has_data': False, 'notices': [], 'processed': 0}

            contents_div = table_div.find('div', class_='contents')
            if not contents_div:
                return {'has_data': False, 'notices': [], 'processed': 0}

            all_dl_list  = contents_div.find_all('dl')
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
                        continue

                    processed_count += 1
                    number = dt_list[0].get_text(strip=True)
                    date   = dt_list[1].get_text(strip=True)
                    unit   = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else '台灣航港局'

                    link_tag = dd.find('a')
                    if link_tag:
                        title = link_tag.get_text(strip=True)
                        link  = link_tag.get('href', '')
                        if link and not link.startswith('http'):
                            link = f"https://www.motcmpb.gov.tw{link}" if link.startswith('/') else f"https://www.motcmpb.gov.tw/{link}"
                    else:
                        title = dd.get_text(strip=True)
                        link  = ''

                    print(f"    [{idx}] {number} | {date} | {title[:40]}...")

                    parsed_date, is_today = self.is_within_date_range(date)
                    if parsed_date is None:
                        print(f"        ⏭️ 日期超出範圍: {date}")
                        continue

                    time_label = "🆕 今日" if is_today else "📚 歷史"
                    print(f"        {time_label} 資料: {date}")

                    matched_keywords = self.check_keywords(title)
                    if not matched_keywords:
                        print(f"        ⏭️ 無關鍵字匹配")
                        continue
                    print(f"        ✅ 關鍵字匹配: {', '.join(matched_keywords)}")

                    print(f"        📍 正在提取座標...")
                    coordinates = []
                    title_coords = self.coord_extractor.extract_coordinates(title)
                    if title_coords:
                        coordinates.extend(title_coords)
                        print(f"          ✅ 從標題提取到 {len(title_coords)} 個座標")

                    if link:
                        try:
                            self.driver.execute_script("window.open('');")
                            self.driver.switch_to.window(self.driver.window_handles[1])
                            self.driver.set_page_load_timeout(10)
                            self.driver.get(link)
                            time.sleep(2)
                            detail_soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                            content_div = (
                                detail_soup.find('div', class_='editor_Content') or
                                detail_soup.find('div', class_='content') or
                                detail_soup.find('div', id='content') or
                                detail_soup.find('article') or
                                detail_soup.find('div', id='container')
                            )
                            if content_div:
                                page_coords = self.coord_extractor.extract_coordinates(content_div.get_text())
                                for pc in page_coords:
                                    if pc not in coordinates:
                                        coordinates.append(pc)
                                if page_coords:
                                    print(f"          ✅ 從頁面提取到 {len(page_coords)} 個座標")
                            self.driver.close()
                            self.driver.switch_to.window(self.driver.window_handles[0])
                            self.driver.set_page_load_timeout(60)
                            time.sleep(1)
                        except Exception as e:
                            print(f"          ⚠️ 無法從網頁提取座標: {e}")
                            try:
                                if len(self.driver.window_handles) > 1:
                                    self.driver.close()
                                    self.driver.switch_to.window(self.driver.window_handles[0])
                                    self.driver.set_page_load_timeout(60)
                            except:
                                pass

                    if coordinates:
                        print(f"        📍 總共提取到 {len(coordinates)} 個座標")
                    else:
                        print(f"        ℹ️ 未找到座標資訊")

                    db_data = (unit, title, link, date, ', '.join(matched_keywords), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), coordinates)
                    is_new, w_id = self.db_manager.save_warning(db_data, source_type="TW_MPB")

                    if is_new and w_id:
                        warning_data = {
                            'id': w_id, 'bureau': unit, 'title': title, 'link': link,
                            'time': date, 'keywords': matched_keywords,
                            'source': 'TW_MPB', 'category': category_name, 'coordinates': coordinates
                        }
                        if is_today:
                            self.new_warnings_today.append(w_id)
                            self.captured_warnings_today.append(warning_data)
                            print(f"        💾 新資料已存入 [今日] (ID: {w_id})")
                        else:
                            self.new_warnings_history.append(w_id)
                            self.captured_warnings_history.append(warning_data)
                            print(f"        💾 新資料已存入 [歷史] (ID: {w_id})")
                    else:
                        print(f"        ℹ️ 資料已存在")

                except Exception as e:
                    print(f"    ⚠️ 處理項目 {idx} 時出錯: {e}")
                    traceback.print_exc()
                    continue

            print(f"    📊 處理 {processed_count} 筆")
            return {'has_data': processed_count > 0, 'notices': [], 'processed': processed_count}

        except Exception as e:
            print(f"  ❌ 請求失敗: {e}")
            traceback.print_exc()
            return {'has_data': False, 'notices': [], 'processed': 0}

    def scrape_all_pages(self, max_pages=5):
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

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇹🇼 台灣航港局爬取完成:")
        print(f"   🆕 今日新增: {len(self.new_warnings_today)} 筆")
        print(f"   📚 歷史資料: {len(self.new_warnings_history)} 筆")
        print(f"   📊 總計: {total_new} 筆")
        return {'today': self.new_warnings_today, 'history': self.new_warnings_history}


# ==================== 7. 中國海事局爬蟲 (增強版) ====================
class CNMSANavigationWarningsScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, headless=True, days=3):
        self.db_manager      = db_manager
        self.keyword_manager = keyword_manager
        self.keywords        = keyword_manager.get_keywords()
        self.teams_notifier  = teams_notifier
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

        self.days        = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []

        print(f"  📅 中國海事局爬蟲設定:")
        print(f"     - 抓取範圍: 最近 {days} 天 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
        print(f"     - 今日定義: {self.today_start.strftime('%Y-%m-%d')} 00:00 起")

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
        """抓取單一海事局警告（增強版，區分今日/歷史）"""
        print(f"  🔍 抓取: {bureau_name}")

        max_retries = 3
        for retry in range(max_retries):
            try:
                if retry > 0:
                    print(f"    🔄 重試第 {retry} 次...")
                    try:
                        bureau_element = self.driver.find_element(
                            By.XPATH,
                            f"//div[@class='nav_lv2_text' and contains(text(), '{bureau_name}')]"
                        )
                    except:
                        print(f"    ⚠️ 無法重新獲取元素: {bureau_name}")
                        break

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

                            is_today = False
                            if publish_time:
                                p_date = self.parse_date(publish_time)
                                if p_date:
                                    if p_date < self.cutoff_date:
                                        print(f"      ⏭️ 日期過舊: {publish_time}")
                                        processed_count += 1
                                        continue
                                    is_today   = p_date >= self.today_start
                                    time_label = "🆕 今日" if is_today else "📚 歷史"
                                    print(f"      {time_label} 資料: {publish_time}")
                                else:
                                    print(f"      ⚠️ 無法解析日期: {publish_time}")
                                    processed_count += 1
                                    continue
                            else:
                                print(f"      ⚠️ 無日期資訊")
                                processed_count += 1
                                continue

                            # ── 座標提取 ──
                            print(f"    📍 正在提取座標: {title[:40]}...")
                            coordinates = []
                            title_coords = self.coord_extractor.extract_coordinates(title)
                            if title_coords:
                                coordinates.extend(title_coords)
                                print(f"      ✅ 從標題提取到 {len(title_coords)} 個座標")

                            if link and not link.startswith('javascript'):
                                try:
                                    self.driver.execute_script("window.open('');")
                                    self.driver.switch_to.window(self.driver.window_handles[-1])
                                    self.driver.set_page_load_timeout(10)
                                    try:
                                        self.driver.get(link)
                                        time.sleep(1)
                                        page_html   = self.driver.page_source
                                        page_coords = self.coord_extractor.extract_from_html(page_html)
                                        if page_coords:
                                            for pc in page_coords:
                                                if pc not in coordinates:
                                                    coordinates.append(pc)
                                            print(f"      ✅ 從頁面提取到 {len(page_coords)} 個座標")
                                    except Exception as e:
                                        print(f"      ⚠️ 頁面載入超時或失敗: {e}")
                                    finally:
                                        try:
                                            self.driver.close()
                                            self.driver.switch_to.window(self.driver.window_handles[0])
                                            self.driver.set_page_load_timeout(120)
                                        except:
                                            pass
                                except Exception as e:
                                    print(f"      ⚠️ 無法從網頁提取座標: {e}")

                            if coordinates:
                                print(f"      📍 總共提取到 {len(coordinates)} 個座標")
                            else:
                                print(f"      ⚠️ 未找到座標資訊")

                            db_data = (
                                bureau_name, title, link, publish_time,
                                ', '.join(matched),
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                coordinates
                            )
                            is_new, w_id = self.db_manager.save_warning(db_data, source_type="CN_MSA")

                            if is_new and w_id:
                                warning_data = {
                                    'id': w_id, 'bureau': bureau_name, 'title': title,
                                    'link': link, 'time': publish_time, 'keywords': matched,
                                    'source': 'CN_MSA', 'coordinates': coordinates
                                }
                                if is_today:
                                    self.new_warnings_today.append(w_id)
                                    self.captured_warnings_today.append(warning_data)
                                    print(f"      ✅ 新警告 [今日]: {title[:40]}...")
                                else:
                                    self.new_warnings_history.append(w_id)
                                    self.captured_warnings_history.append(warning_data)
                                    print(f"      ✅ 新警告 [歷史]: {title[:40]}...")
                            else:
                                print(f"      ⏭️ 已存在")

                        except Exception as e:
                            print(f"    ⚠️ 處理項目 {processed_count + 1} 時出錯: {e}")

                        processed_count += 1

                    except Exception as e:
                        print(f"    ⚠️ 獲取項目列表時出錯: {e}")
                        break

                print(f"    ✅ {bureau_name} 處理完成，共處理 {processed_count} 個項目")
                break  # 成功則跳出重試迴圈

            except Exception as e:
                print(f"  ⚠️ 抓取 {bureau_name} 錯誤 (嘗試 {retry+1}/{max_retries}): {e}")
                if retry == max_retries - 1:
                    print(f"  ❌ {bureau_name} 抓取失敗，已達最大重試次數")
                else:
                    time.sleep(3)

    def scrape_all_bureaus(self):
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
                print("  🔒 WebDriver 已關閉 (中國海事局)")
            except:
                pass

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇨🇳 中國海事局爬取完成:")
        print(f"   🆕 今日新增: {len(self.new_warnings_today)} 筆")
        print(f"   📚 歷史資料: {len(self.new_warnings_history)} 筆")
        print(f"   📊 總計: {total_new} 筆")
        return {'today': self.new_warnings_today, 'history': self.new_warnings_history}


# ==================== 8. 環境變數讀取 ====================
print("📋 正在讀取環境變數...")

TEAMS_WEBHOOK    = os.getenv("TEAMS_WEBHOOK_URL", "")
MAIL_USER        = os.getenv("MAIL_USER", "")
MAIL_PASSWORD    = os.getenv("MAIL_PASSWORD", "")
TARGET_EMAIL     = os.getenv("TARGET_EMAIL", "")
MAIL_SMTP_SERVER = os.getenv("MAIL_SMTP_SERVER", "smtp.gmail.com")
MAIL_SMTP_PORT   = int(os.getenv("MAIL_SMTP_PORT", "587"))

DB_FILE_PATH     = os.getenv("DB_FILE_PATH", "navigation_warnings.db")
BACKUP_DIR       = os.getenv("BACKUP_DIR", "backups")
MAX_BACKUP_FILES = int(os.getenv("MAX_BACKUP_FILES", "7"))

SCRAPE_INTERVAL  = int(os.getenv("SCRAPE_INTERVAL", "3600"))
MAX_RETRIES      = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT  = int(os.getenv("REQUEST_TIMEOUT", "30"))

KEYWORDS_CONFIG  = os.getenv("KEYWORDS_CONFIG", "keywords_config.json")
CHROME_HEADLESS  = os.getenv("CHROME_HEADLESS", "true").lower() == "true"

ENABLE_EMAIL_NOTIFICATIONS = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "true").lower() == "true"
ENABLE_TEAMS_NOTIFICATIONS = os.getenv("ENABLE_TEAMS_NOTIFICATIONS", "true").lower() == "true"

ENABLE_CN_MSA = os.getenv("ENABLE_CN_MSA", "true").lower() == "true"
ENABLE_TW_MPB = os.getenv("ENABLE_TW_MPB", "true").lower() == "true"
ENABLE_UKMTO  = os.getenv("ENABLE_UKMTO",  "true").lower() == "true"   # ← 新增

SCRAPE_DAYS       = int(os.getenv("SCRAPE_DAYS",       "3"))
UKMTO_SCRAPE_DAYS = int(os.getenv("UKMTO_SCRAPE_DAYS", "30"))           # ← 新增，UKMTO 預設 30 天

print("\n" + "="*70)
print("⚙️  系統設定檢查")
print("="*70)
print(f"📧 Email 通知: {'✅ 啟用' if ENABLE_EMAIL_NOTIFICATIONS and MAIL_USER else '❌ 停用'}")
print(f"📢 Teams 通知: {'✅ 啟用' if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK else '❌ 停用'}")
print(f"💾 資料庫: {DB_FILE_PATH}")
print(f"📅 抓取範圍: CN/TW 最近 {SCRAPE_DAYS} 天 | UKMTO 最近 {UKMTO_SCRAPE_DAYS} 天")
print(f"🔍 資料來源: CN_MSA={'✅' if ENABLE_CN_MSA else '❌'} | TW_MPB={'✅' if ENABLE_TW_MPB else '❌'} | UKMTO={'✅' if ENABLE_UKMTO else '❌'}")
print("="*70 + "\n")


# ==================== 9. 主程式進入點 ====================
if __name__ == "__main__":
    try:
        print("\n" + "="*70)
        print("🌊 海事警告監控系統啟動 v3.0")
        print("="*70)

        # ── 初始化資料庫 ──
        print("\n📦 初始化資料庫...")
        db_manager = DatabaseManager(db_name=DB_FILE_PATH)
        print(f"  ✅ 資料庫初始化成功: {DB_FILE_PATH}")

        # ── 初始化關鍵字管理器 ──
        print("🔑 初始化關鍵字管理器...")
        keyword_manager = KeywordManager(config_file=KEYWORDS_CONFIG)

        # ── 初始化座標提取器 ──
        print("🗺️  初始化座標提取器...")
        coord_extractor = CoordinateExtractor()

        # ── 初始化 Teams 通知器 ──
        teams_notifier = None
        if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK:
            print("📢 初始化 Teams 通知器...")
            teams_notifier = UnifiedTeamsNotifier(TEAMS_WEBHOOK)

        # ── 初始化 Email 通知器 ──
        email_notifier = None
        if ENABLE_EMAIL_NOTIFICATIONS and all([MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL]):
            print("📧 初始化 Email 通知器...")
            email_notifier = GmailRelayNotifier(MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL)

        # ── 初始化爬蟲 ──
        cn_scraper   = None
        tw_scraper   = None
        ukmto_scraper = None

        if ENABLE_CN_MSA:
            print("🇨🇳 初始化中國海事局爬蟲...")
            cn_scraper = CNMSANavigationWarningsScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                headless=CHROME_HEADLESS,
                days=SCRAPE_DAYS
            )

        if ENABLE_TW_MPB:
            print("🇹🇼 初始化台灣航港局爬蟲...")
            tw_scraper = TWMaritimePortBureauScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                days=SCRAPE_DAYS
            )

        if ENABLE_UKMTO:
            print("🇬🇧 初始化 UKMTO 爬蟲...")
            ukmto_scraper = UKMTOScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                days=UKMTO_SCRAPE_DAYS
            )

        print("\n" + "="*70)
        print("✅ 所有模組初始化完成")
        print("="*70)

        # ========== 開始爬取 ==========
        print("\n🚀 開始爬取海事警告...")

        all_warnings_today   = []
        all_warnings_history = []
        all_captured_today   = []
        all_captured_history = []

        # 爬取中國海事局
        if cn_scraper:
            print("\n🇨🇳 爬取中國海事局...")
            cn_result = cn_scraper.scrape_all_bureaus()
            all_warnings_today.extend(cn_result['today'])
            all_warnings_history.extend(cn_result['history'])
            all_captured_today.extend(cn_scraper.captured_warnings_today)
            all_captured_history.extend(cn_scraper.captured_warnings_history)

        # 爬取台灣航港局
        if tw_scraper:
            print("\n🇹🇼 爬取台灣航港局...")
            tw_result = tw_scraper.scrape_all_pages()
            all_warnings_today.extend(tw_result['today'])
            all_warnings_history.extend(tw_result['history'])
            all_captured_today.extend(tw_scraper.captured_warnings_today)
            all_captured_history.extend(tw_scraper.captured_warnings_history)

        # 爬取 UKMTO
        if ukmto_scraper:
            print("\n🇬🇧 爬取 UKMTO...")
            ukmto_result = ukmto_scraper.scrape()
            all_warnings_today.extend(ukmto_result['today'])
            all_warnings_history.extend(ukmto_result['history'])
            all_captured_today.extend(ukmto_scraper.captured_warnings_today)
            all_captured_history.extend(ukmto_scraper.captured_warnings_history)

        # ========== 發送通知 ==========
        total_warnings = len(all_warnings_today) + len(all_warnings_history)

        if total_warnings > 0:
            print(f"\n📢 發現 {total_warnings} 個警告 (今日 {len(all_warnings_today)} 筆，歷史 {len(all_warnings_history)} 筆)")

            if teams_notifier and ENABLE_TEAMS_NOTIFICATIONS:

                def _to_teams_tuple(w):
                    """將 warning_data dict 轉為 Teams 通知所需的 tuple 格式"""
                    return (
                        w.get('id'),
                        w.get('bureau'),
                        w.get('title'),
                        w.get('link'),
                        w.get('time'),
                        ', '.join(w.get('keywords', [])) if isinstance(w.get('keywords'), list) else w.get('keywords', ''),
                        '',
                        json.dumps(w.get('coordinates', []))
                    )

                # 依來源分組發送（今日）
                for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                    group = [w for w in all_captured_today if w.get('source') == src]
                    if group:
                        print(f"\n📤 發送 {src} 通知 [今日新增]...")
                        teams_notifier.send_batch_notification(
                            [_to_teams_tuple(w) for w in group], src, is_today=True
                        )

                # 依來源分組發送（歷史）
                for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                    group = [w for w in all_captured_history if w.get('source') == src]
                    if group:
                        print(f"\n📤 發送 {src} 通知 [歷史資料]...")
                        teams_notifier.send_batch_notification(
                            [_to_teams_tuple(w) for w in group], src, is_today=False
                        )

            # Email 通知
            if email_notifier and ENABLE_EMAIL_NOTIFICATIONS:
                print("\n📧 發送 Email 通知...")
                email_notifier.send_trigger_email(all_captured_today, all_captured_history)

        else:
            print("\n✅ 沒有新的警告")

        # ========== 執行摘要 ==========
        print("\n" + "="*70)
        print("📊 執行摘要")
        print("="*70)

        for src, icon in [("CN_MSA", "🇨🇳 中國海事局"), ("TW_MPB", "🇹🇼 台灣航港局"), ("UKMTO", "🇬🇧 UKMTO")]:
            t_count = len([w for w in all_captured_today   if w.get('source') == src])
            h_count = len([w for w in all_captured_history if w.get('source') == src])
            t_coords = sum(len(w.get('coordinates', [])) for w in all_captured_today   if w.get('source') == src)
            h_coords = sum(len(w.get('coordinates', [])) for w in all_captured_history if w.get('source') == src)
            print(f"\n  {icon}:")
            print(f"     🆕 今日新增: {t_count} 筆 ({t_coords} 個座標點)")
            print(f"     📚 歷史資料: {h_count} 筆 ({h_coords} 個座標點)")

        total_coords = sum(len(w.get('coordinates', [])) for w in all_captured_today + all_captured_history)
        print(f"\n  📈 總計: {total_warnings} 筆警告")
        print(f"  📍 總座標點數: {total_coords}")

        print("\n" + "="*70)
        db_manager.print_statistics()

        print("\n" + "="*70)
        print("🎉 系統執行完成 v3.0")
        print("="*70)

    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
    except Exception as e:
        print(f"\n❌ 執行失敗: {e}")
        traceback.print_exc()

