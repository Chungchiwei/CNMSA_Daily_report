#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一海事警告監控系統 (中國海事局 + 台灣航港局 + UKMTO)
支援經緯度提取、Teams 通知、Email 報告
版本: 3.3 - UKMTO CSS Selector 改為 partial match，防止 Next.js hash 變動失效
"""

import argparse
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
from pathlib import Path
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

from cn_sources.registry import CNSourceRegistry
from services.risk_assessment import RiskAssessmentService
from services import summarizer
from services import source_health_alert
from services.ssl_config import resolve_ssl_verify
from templates import email_report as email_report_tpl
from notifications.teams_notifier import TeamsNotifier

# 專案根目錄（不受執行時工作目錄影響，claude.md 十二之要求）
PROJECT_ROOT = Path(__file__).resolve().parent

# ==================== 1. 全域初始化 ====================
# 安全性原則：預設一律驗證 HTTPS 憑證，不得全域停用 SSL 驗證。
# 若公司網路使用自訂 CA（例如企業 Proxy 憑證攔截），可透過 CA_BUNDLE_PATH
# 環境變數指定自訂 CA bundle 路徑，requests 呼叫會改用該路徑做驗證，
# 而不是整體停用驗證。SSL_VERIFY 的實際判斷邏輯統一於 services/ssl_config.py，
# 避免各模組各自維護一份可能不同步的規則。
load_dotenv()

SSL_VERIFY = resolve_ssl_verify()
if SSL_VERIFY is not True:
    print(f"🔐 已設定自訂 CA bundle: {SSL_VERIFY}")

warnings.filterwarnings('ignore', category=DeprecationWarning)
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


# ==================== 2. 座標提取器 (增強版) ====================
class CoordinateExtractor:
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
            except Exception:
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
            except Exception:
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
            except Exception:
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
                return self.extract_coordinates(content_div.get_text())
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


# ==================== 3. (已移除) 舊版 Teams 通知系統 ====================
# UnifiedTeamsNotifier 已由 notifications/teams_notifier.py 的 TeamsNotifier 取代
# （風險分色卡片、URL 驗證、Markdown 逸出、429/5xx 重試、dry-run 保護）。
# 舊類別不再被主流程呼叫，為避免重複的 Teams 發送邏輯已於 v4 移除。


# ==================== 4. Email 通知系統 ====================
class GmailRelayNotifier:
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

    def send_trigger_email(self, today_warnings, history_warnings, health_reports=None,
                            source_anomaly=False, subject_prefix=""):
        if not self.enabled:
            print("ℹ️ Email 通知未啟用")
            return False
        try:
            msg = MIMEMultipart('related')

            subject = subject_prefix + email_report_tpl.build_subject(today_warnings, history_warnings)
            msg['Subject'] = subject
            msg['From'] = self.mail_user
            msg['To']   = self.target_email

            msg_alt = MIMEMultipart('alternative')
            msg.attach(msg_alt)

            # 純文字 alternative 必須先於 HTML 附加（email 標準：依偏好順序遞增附加）
            plain_text = email_report_tpl.build_plain_text_report(
                today_warnings, history_warnings, source_anomaly=source_anomaly
            )
            msg_alt.attach(MIMEText(plain_text, 'plain', 'utf-8'))

            html_report = email_report_tpl.build_html_report(
                today_warnings, history_warnings,
                health_reports=health_reports, source_anomaly=source_anomaly,
            )
            msg_alt.attach(MIMEText(html_report, 'html', 'utf-8'))

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

    def send_system_anomaly_email(self, anomaly) -> bool:
        """
        來源健康異常通知：與一般航警通知（send_trigger_email）完全分開的獨立信件，
        主旨固定為「【系統異常】...」，不得與「今日無新增」混淆（claude.md 第二階段第七節）。
        """
        if not self.enabled:
            print("ℹ️ Email 通知未啟用，無法發送系統異常通知")
            return False
        try:
            msg = MIMEMultipart('related')
            msg['Subject'] = email_report_tpl.build_system_anomaly_subject(anomaly)
            msg['From'] = self.mail_user
            msg['To'] = self.target_email

            msg_alt = MIMEMultipart('alternative')
            msg.attach(msg_alt)
            msg_alt.attach(MIMEText(email_report_tpl.build_system_anomaly_plain_text(anomaly), 'plain', 'utf-8'))
            msg_alt.attach(MIMEText(email_report_tpl.build_system_anomaly_html(anomaly), 'html', 'utf-8'))

            print(f"📧 正在發送系統異常通知至 {self.target_email}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            print("✅ 系統異常通知發送成功")
            return True
        except Exception as e:
            print(f"❌ 系統異常通知發送失敗: {e}")
            traceback.print_exc()
            return False

    # _source_icon / _generate_html_report 已移除：舊版 inline HTML report 產生器，
    # 已由 templates/email_report.py 的 build_html_report()/build_plain_text_report() 取代，
    # send_trigger_email() 不再呼叫此處的任何方法（見上方 import email_report_tpl）。


# ==================== 5. UKMTO 爬蟲 (v3.3 - Partial Class Match) ====================
class UKMTOScraper:
    URL = "https://www.ukmto.org/recent-incidents"
    MONTH_MAP = {
        "January": 1, "February": 2, "March": 3,    "April": 4,
        "May": 5,     "June": 6,     "July": 7,      "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }

    # ✅ v3.3 改動：所有 selector 改為 partial class match，不依賴 Next.js hash
    # 只保留 class 前綴，hash 部分完全移除
    SEL_INCIDENT_LIST = "ul[class*='IncidentList_incidentList']"
    SEL_INCIDENT_ITEM = "ul[class*='IncidentList_incidentList'] > li[class*='IncidentList_incident']"
    SEL_TITLE_BTN     = "h3[class*='IncidentList_title'] button"
    SEL_PIN_SPAN      = "span[class*='Pin_pin']"
    SEL_META_SPAN     = "ul[class*='IncidentList_meta'] li span"
    SEL_DETAILS_P     = "p[class*='IncidentList_details']"

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

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []
        self._next_data_coords: dict   = {}

        print(f"  🇬🇧 UKMTO 爬蟲設定 (v3.3):")
        print(f"     - 抓取範圍: 最近 {days} 天 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
        print(f"     - 今日定義: {self.today_start.strftime('%Y-%m-%d')} 00:00 UTC 起")
        print(f"     - CSS 策略: Partial class match (防 hash 失效)")

        print("  🌐 正在啟動 Chrome WebDriver (UKMTO)...")
        self.driver = self._init_driver()
        self.wait   = WebDriverWait(self.driver, 20)
        print("  ✅ WebDriver 啟動成功 (UKMTO)")

    def _init_driver(self) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        driver_path = self._find_chromedriver()
        service = Service(executable_path=driver_path) if driver_path else Service()
        if platform.system() == 'Windows':
            service.creation_flags = subprocess.CREATE_NO_WINDOW

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        return driver

    def _find_chromedriver(self) -> str | None:
        env_path = os.environ.get("CHROMEDRIVER_PATH")
        if env_path and os.path.exists(env_path):
            return env_path
        common_paths = [
            r"C:\chromedriver\chromedriver.exe",
            r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chromedriver.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "chromedriver.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Downloads", "chromedriver.exe"),
            "chromedriver.exe", "chromedriver",
        ]
        for p in common_paths:
            if p and os.path.exists(p):
                return p
        try:
            return ChromeDriverManager().install()
        except Exception as e:
            print(f"  ⚠️  webdriver_manager 失敗: {e}")
        return None

    def _verify_selectors(self):
        """
        ✅ v3.3 新增：執行前驗證所有關鍵 selector 是否有效
        若任何 selector 找不到元素，印出警告方便快速定位
        """
        print("\n  🔬 Selector 驗證中...")
        test_cases = [
            (self.SEL_INCIDENT_LIST, "事件列表容器"),
            (self.SEL_INCIDENT_ITEM, "事件項目"),
            (self.SEL_TITLE_BTN,     "標題按鈕"),
            (self.SEL_PIN_SPAN,      "Pin 顏色標記"),
            (self.SEL_META_SPAN,     "日期 span"),
            (self.SEL_DETAILS_P,     "詳情段落"),
        ]
        all_ok = True
        for selector, name in test_cases:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                status = f"✅ 找到 {len(elements)} 個" if elements else "❌ 找不到任何元素"
                if not elements:
                    all_ok = False
            except Exception as e:
                status = f"❌ 錯誤: {e}"
                all_ok = False
            print(f"     [{name}] {selector[:60]} → {status}")

        if not all_ok:
            print("\n  ⚠️  部分 Selector 失效！正在印出頁面 class 清單以供診斷...")
            self._debug_print_classes()
        else:
            print("  ✅ 所有 Selector 驗證通過")
        return all_ok

    def _debug_print_classes(self):
        """
        ✅ v3.3 新增：當 selector 失效時，自動印出頁面所有 class 名稱
        方便快速找到新的 class 前綴
        """
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            all_classes = set()
            for tag in soup.find_all(True):
                for cls in tag.get('class', []):
                    if 'IncidentList' in cls or 'Pin_pin' in cls or 'incident' in cls.lower():
                        all_classes.add(cls)
            print(f"  🔎 頁面中找到的相關 class 名稱:")
            for cls in sorted(all_classes):
                print(f"     - {cls}")
        except Exception as e:
            print(f"  ⚠️  Debug class 列印失敗: {e}")

    def _extract_coords_from_next_data(self) -> dict:
        coord_map = {}
        try:
            script_el = self.driver.find_element(By.ID, "__NEXT_DATA__")
            raw       = script_el.get_attribute("innerHTML")
            data      = json.loads(raw)
            print("  ✅ 成功讀取 __NEXT_DATA__")
            page_props = data.get("props", {}).get("pageProps", {})
            candidates = [
                (page_props.get("incidents", []),                 "id",  "latitude",  "longitude"),
                (page_props.get("incidents", []),                 "id",  "lat",       "lng"),
                (page_props.get("incidents", []),                 "_id", "latitude",  "longitude"),
                (page_props.get("data", {}).get("incidents", []), "id",  "latitude",  "longitude"),
                (page_props.get("data", {}).get("incidents", []), "id",  "lat",       "lng"),
                (page_props.get("initialData", []),               "id",  "latitude",  "longitude"),
                (page_props.get("initialData", []),               "id",  "lat",       "lng"),
            ]
            for incidents, id_key, lat_key, lon_key in candidates:
                if not incidents:
                    continue
                for inc in incidents:
                    try:
                        inc_id = str(inc.get(id_key, ""))
                        lat    = inc.get(lat_key)
                        lon    = inc.get(lon_key)
                        if lat is None or lon is None:
                            pos = inc.get("position") or inc.get("location") or inc.get("coordinates") or {}
                            if isinstance(pos, dict):
                                lat = pos.get("lat") or pos.get("latitude")
                                lon = pos.get("lng") or pos.get("lon") or pos.get("longitude")
                            elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                                lat, lon = pos[0], pos[1]
                        if lat is not None and lon is not None:
                            lat_f = float(lat)
                            lon_f = float(lon)
                            if -90 <= lat_f <= 90 and -180 <= lon_f <= 180:
                                coord_map[inc_id] = (lat_f, lon_f)
                    except (ValueError, TypeError):
                        continue
                if coord_map:
                    print(f"  📡 __NEXT_DATA__ 共解析到 {len(coord_map)} 筆座標")
                    return coord_map
            if not coord_map:
                print("  ⚠️  標準路徑未找到座標，嘗試遞迴搜尋...")
                coord_map = self._deep_search_coords(data)
                if coord_map:
                    print(f"  📡 遞迴搜尋共找到 {len(coord_map)} 筆座標")
        except Exception as e:
            print(f"  ⚠️  __NEXT_DATA__ 解析失敗: {e}")
        return coord_map

    def _deep_search_coords(self, obj, depth=0, result=None) -> dict:
        if result is None:
            result = {}
        if depth > 6:
            return result
        if isinstance(obj, dict):
            lat = obj.get("latitude") or obj.get("lat")
            lon = obj.get("longitude") or obj.get("lng") or obj.get("lon")
            if lat is not None and lon is not None:
                try:
                    lat_f, lon_f = float(lat), float(lon)
                    if -90 <= lat_f <= 90 and -180 <= lon_f <= 180:
                        inc_id = str(obj.get("id") or obj.get("_id") or len(result))
                        result[inc_id] = (lat_f, lon_f)
                except (ValueError, TypeError):
                    pass
            for v in obj.values():
                self._deep_search_coords(v, depth + 1, result)
        elif isinstance(obj, list):
            for item in obj:
                self._deep_search_coords(item, depth + 1, result)
        return result

    def _fetch_coords_from_next_api(self) -> dict:
        coord_map = {}
        try:
            script_el = self.driver.find_element(By.ID, "__NEXT_DATA__")
            raw       = json.loads(script_el.get_attribute("innerHTML"))
            build_id  = raw.get("buildId", "")
            if not build_id:
                return coord_map
            api_url = f"https://www.ukmto.org/_next/data/{build_id}/recent-incidents.json"
            print(f"  🔄 嘗試 _next/data API: {api_url}")
            resp = requests.get(api_url, timeout=15, verify=SSL_VERIFY, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                api_data   = resp.json()
                page_props = api_data.get("pageProps", {})
                incidents  = (
                    page_props.get("incidents") or
                    page_props.get("data", {}).get("incidents") or []
                )
                for inc in incidents:
                    try:
                        inc_id = str(inc.get("id") or inc.get("_id", ""))
                        lat    = inc.get("latitude") or inc.get("lat")
                        lon    = inc.get("longitude") or inc.get("lng") or inc.get("lon")
                        if lat is not None and lon is not None:
                            coord_map[inc_id] = (float(lat), float(lon))
                    except (ValueError, TypeError):
                        continue
                if coord_map:
                    print(f"  ✅ _next/data API 取得 {len(coord_map)} 筆座標")
        except Exception as e:
            print(f"  ⚠️  _next/data API 失敗: {e}")
        return coord_map

    def _parse_date(self, date_str: str) -> datetime | None:
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

    def scrape(self):
        print(f"\n🇬🇧 開始爬取 UKMTO 航行警告 (v3.3)...")
        try:
            self.driver.get(self.URL)

            # ✅ v3.3：等待改用 partial class match selector
            self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, self.SEL_INCIDENT_LIST)
            ))
            print("  ✅ 頁面載入完成")
            time.sleep(2)

            # ✅ v3.3：執行 selector 驗證
            self._verify_selectors()

            print("\n  📡 Step 1: 從 __NEXT_DATA__ 提取座標...")
            self._next_data_coords = self._extract_coords_from_next_data()

            if not self._next_data_coords:
                print("  🔄 Step 2: 嘗試 _next/data API...")
                self._next_data_coords = self._fetch_coords_from_next_api()

            if self._next_data_coords:
                print(f"  ✅ 座標預載完成，共 {len(self._next_data_coords)} 筆")
            else:
                print("  ⚠️  無法預載座標，將改用文字解析 fallback")

            print("\n  📋 Step 3: 開始解析事件列表...")

            # ✅ v3.3：改用 partial class match 抓取事件列表
            li_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                self.SEL_INCIDENT_ITEM
            )
            print(f"  共找到 {len(li_elements)} 筆事件，篩選最近 {self.days} 天...")

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
            except Exception:
                pass

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇬🇧 UKMTO 爬取完成: 🆕 今日={len(self.new_warnings_today)} | 📚 歷史={len(self.new_warnings_history)} | 總計={total_new}")
        return {'today': self.new_warnings_today, 'history': self.new_warnings_history}

    def _process_incident(self, elem):
        incident_id = elem.get_attribute("id") or ""

        # ✅ v3.3：全部改用 partial class match selector
        try:
            title = elem.find_element(
                By.CSS_SELECTOR, self.SEL_TITLE_BTN
            ).text.strip()
        except Exception:
            title = "N/A"

        try:
            colour = elem.find_element(
                By.CSS_SELECTOR, self.SEL_PIN_SPAN
            ).get_attribute("data-colour") or "N/A"
        except Exception:
            colour = "N/A"

        try:
            date_str      = elem.find_element(
                By.CSS_SELECTOR, self.SEL_META_SPAN
            ).text.strip()
            incident_date = self._parse_date(date_str)
        except Exception:
            date_str      = "N/A"
            incident_date = None

        try:
            details = elem.find_element(
                By.CSS_SELECTOR, self.SEL_DETAILS_P
            ).text.strip()
        except Exception:
            details = "N/A"

        if incident_date is None:
            print(f"  ⚠️  跳過（日期無法解析）：{title}")
            return
        if incident_date < self.cutoff_date:
            raise StopIteration(f"超出範圍，停止（{date_str}）")

        is_today    = incident_date >= self.today_start
        time_label  = "🆕 今日" if is_today else "📚 歷史"
        colour_icon = "🔴" if colour == "Red" else "🟡"
        print(f"  {time_label} {colour_icon} [{date_str}] {title}")

        coordinates  = []
        coord_source = "none"

        if incident_id and incident_id in self._next_data_coords:
            coordinates  = [self._next_data_coords[incident_id]]
            coord_source = "next_data"
        if not coordinates and self._next_data_coords:
            clean_id = incident_id.lstrip('#').strip()
            for key, coord in self._next_data_coords.items():
                if clean_id and (clean_id in key or key in clean_id):
                    coordinates  = [coord]
                    coord_source = "next_data"
                    break
        if not coordinates:
            text_coords = self.coord_extractor.extract_coordinates(details)
            if text_coords:
                coordinates  = text_coords
                coord_source = "text"

        matched_keywords = [k for k in self.keywords if k.lower() in (title + " " + details).lower()]
        if not matched_keywords:
            matched_keywords = ["UKMTO"]

        db_data = (
            "UKMTO", title, self.URL, date_str,
            ', '.join(matched_keywords),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            coordinates
        )
        is_new, w_id = self.db_manager.save_warning(db_data, source_type="UKMTO")

        if is_new and w_id:
            warning_data = {
                'id': w_id, 'bureau': "UKMTO", 'title': title,
                'link': self.URL, 'time': date_str, 'keywords': matched_keywords,
                'source': 'UKMTO', 'colour': colour,
                'coordinates': coordinates, 'coord_source': coord_source, 'details': details,
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


# ==================== 6. 台灣航港局爬蟲 (不變) ====================
class TWMaritimePortBureauScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, days=3):
        self.db_manager      = db_manager
        self.keyword_manager = keyword_manager
        self.keywords        = keyword_manager.get_keywords()
        self.teams_notifier  = teams_notifier
        self.coord_extractor = coord_extractor
        self.base_url        = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
        self.days            = days
        self.cutoff_date     = (
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=days)
        )
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []
        self.target_categories         = {'333': '礙航公告', '334': '射擊公告'}

        print(f"  📅 台灣航港局爬蟲設定: 最近 {days} 天 | 今日: {self.today_start.strftime('%Y-%m-%d')}")
        print("  🌐 正在啟動 Chrome WebDriver (台灣航港局)...")

        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        options.add_experimental_option('prefs', {'profile.default_content_setting_values.notifications': 2})
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
        matched = [k for k in self.keywords if k.lower() in text.lower()]
        for kw in ['礙航', '射擊']:
            if kw in text and kw not in matched:
                matched.append(kw)
        return matched

    def parse_date(self, date_string):
        try:
            m = re.match(r'^(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})$', date_string.strip())
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if y < 1000:
                    y += 1911
                return datetime(y, mo, d)
        except Exception:
            pass
        return None

    def is_within_date_range(self, date_string):
        if not date_string:
            return None, False
        pd = self.parse_date(date_string)
        if pd:
            if pd < self.cutoff_date:
                return None, False
            return pd, pd >= self.today_start
        return None, False

    def click_category_tab(self, category_id):
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.tabs a")))
            tab_xpath = (
                f"//div[@class='tabs']//a[@data-val='{category_id}']"
                if category_id
                else "//div[@class='tabs']//a[@class='active']"
            )
            tab = self.wait.until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
            self.driver.execute_script("arguments[0].scrollIntoView(true);", tab)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", tab)
            time.sleep(3)
            return True
        except Exception as e:
            print(f"    ⚠️ 點擊分類標籤失敗: {e}")
            return False

    def get_notices_selenium(self, page=1, base_category_id=None):
        try:
            category_name = (
                self.target_categories.get(base_category_id, '全部')
                if base_category_id else '全部'
            )
            print(f"  正在請求台灣航港局 [{category_name}] 第 {page} 頁...")

            if page == 1:
                self.driver.get(self.base_url)
                time.sleep(3)
                if base_category_id and not self.click_category_tab(base_category_id):
                    return {'has_data': False, 'notices': [], 'processed': 0}
            else:
                try:
                    nb = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.next a")))
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", nb)
                    time.sleep(0.5)
                    self.driver.execute_script("arguments[0].click();", nb)
                    time.sleep(3)
                except Exception as e:
                    print(f"    ⚠️ 無法翻頁: {e}")
                    return {'has_data': False, 'notices': [], 'processed': 0}

            try:
                self.wait.until(EC.presence_of_element_located((By.ID, "table")))
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#table dl")))
            except Exception as e:
                print(f"    ⚠️ 等待內容載入超時: {e}")
                return {'has_data': False, 'notices': [], 'processed': 0}

            soup         = BeautifulSoup(self.driver.page_source, 'html.parser')
            table_div    = soup.find('div', id='table')
            if not table_div:
                return {'has_data': False, 'notices': [], 'processed': 0}
            contents_div = table_div.find('div', class_='contents')
            if not contents_div:
                return {'has_data': False, 'notices': [], 'processed': 0}
            data_dl_list = [
                dl for dl in contents_div.find_all('dl')
                if 'con-title' not in dl.get('class', [])
            ]
            print(f"    📋 找到 {len(data_dl_list)} 個資料列")
            if not data_dl_list:
                return {'has_data': False, 'notices': [], 'processed': 0}

            processed_count = 0
            for idx, dl in enumerate(data_dl_list, 1):
                try:
                    dt_list = dl.find_all('dt')
                    dd      = dl.find('dd')
                    if len(dt_list) < 2 or not dd:
                        continue
                    processed_count += 1
                    date = dt_list[1].get_text(strip=True)
                    unit = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else '台灣航港局'
                    link_tag = dd.find('a')
                    if link_tag:
                        title = link_tag.get_text(strip=True)
                        link  = link_tag.get('href', '')
                        if link and not link.startswith('http'):
                            link = (
                                f"https://www.motcmpb.gov.tw{link}"
                                if link.startswith('/')
                                else f"https://www.motcmpb.gov.tw/{link}"
                            )
                    else:
                        title = dd.get_text(strip=True)
                        link  = ''

                    parsed_date, is_today = self.is_within_date_range(date)
                    if parsed_date is None:
                        continue

                    matched_keywords = self.check_keywords(title)
                    if not matched_keywords:
                        continue

                    coordinates  = []
                    title_coords = self.coord_extractor.extract_coordinates(title)
                    if title_coords:
                        coordinates.extend(title_coords)

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
                                for pc in self.coord_extractor.extract_coordinates(content_div.get_text()):
                                    if pc not in coordinates:
                                        coordinates.append(pc)
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
                            except Exception:
                                pass

                    db_data = (
                        unit, title, link, date,
                        ', '.join(matched_keywords),
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        coordinates
                    )
                    is_new, w_id = self.db_manager.save_warning(db_data, source_type="TW_MPB")

                    if is_new and w_id:
                        warning_data = {
                            'id': w_id, 'bureau': unit, 'title': title,
                            'link': link, 'time': date, 'keywords': matched_keywords,
                            'source': 'TW_MPB', 'category': category_name,
                            'coordinates': coordinates, 'coord_source': 'text'
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
            except Exception:
                pass

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇹🇼 台灣航港局爬取完成: 🆕 今日={len(self.new_warnings_today)} | 📚 歷史={len(self.new_warnings_history)} | 總計={total_new}")
        return {'today': self.new_warnings_today, 'history': self.new_warnings_history}


# ==================== 7. (已移除) 舊版單一中央入口中國海事局爬蟲 ====================
# CNMSANavigationWarningsScraper 已由 cn_sources/ 多來源 registry 取代，
# 舊類別從未在主流程中被實際呼叫，為避免重複爬取與混淆已於 v4 移除。



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
ENABLE_CN_MSA              = os.getenv("ENABLE_CN_MSA",  "true").lower() == "true"
ENABLE_TW_MPB              = os.getenv("ENABLE_TW_MPB",  "true").lower() == "true"
ENABLE_UKMTO               = os.getenv("ENABLE_UKMTO",   "true").lower() == "true"
SCRAPE_DAYS                = int(os.getenv("SCRAPE_DAYS",       "7"))
UKMTO_SCRAPE_DAYS          = int(os.getenv("UKMTO_SCRAPE_DAYS", "30"))

print("\n" + "=" * 70)
print("⚙️  系統設定檢查 v3.3")
print("=" * 70)
print(f"📧 Email 通知: {'✅ 啟用' if ENABLE_EMAIL_NOTIFICATIONS and MAIL_USER else '❌ 停用'}")
print(f"📢 Teams 通知: {'✅ 啟用' if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK else '❌ 停用'}")
print(f"💾 資料庫: {DB_FILE_PATH}")
print(f"📅 抓取範圍: CN/TW 最近 {SCRAPE_DAYS} 天 | UKMTO 最近 {UKMTO_SCRAPE_DAYS} 天")
print(
    f"🔍 資料來源: "
    f"CN_MSA={'✅' if ENABLE_CN_MSA else '❌'} | "
    f"TW_MPB={'✅' if ENABLE_TW_MPB else '❌'} | "
    f"UKMTO={'✅' if ENABLE_UKMTO else '❌'}"
)
print("=" * 70 + "\n")


def parse_cli_args():
    parser = argparse.ArgumentParser(description="海事警告監控與自動通知系統")
    parser.add_argument("--source", choices=["cn", "tw", "ukmto"], default=None,
                         help="只執行指定來源（預設：依 .env 設定執行全部已啟用來源）")
    parser.add_argument("--dry-run", action="store_true",
                         help="只爬取並列印結果，不寫入資料庫、不發送任何通知")
    parser.add_argument("--save-debug", action="store_true",
                         help="解析失敗或列表為空時，保存 HTML 快照到 debug 目錄（已於 .gitignore 排除）")
    parser.add_argument("--backfill-days", type=int, default=None,
                         help="覆寫抓取天數範圍（預設沿用 SCRAPE_DAYS/UKMTO_SCRAPE_DAYS）")
    parser.add_argument("--send-test-email", action="store_true",
                         help="使用目前資料庫中的資料產生一封測試 Email 並直接寄出（不重新爬取）")
    parser.add_argument("--no-notify", action="store_true",
                         help="執行爬取與資料庫寫入，但不發送 Email/Teams 通知")
    parser.add_argument("--preview-email", action="store_true",
                         help="產生 Email 預覽 HTML 檔案到本機（不寄信），供上線前檢查排版")
    return parser.parse_args()


# ==================== 9. 主程式進入點 ====================
if __name__ == "__main__":
    args = parse_cli_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        print("\n" + "=" * 70)
        print(f"🌊 海事警告監控系統啟動 v4.0 (run_id={run_id})")
        if args.dry_run:
            print("   🧪 DRY-RUN 模式：不會寫入資料庫、不會發送通知")
        print("=" * 70)

        print("\n📦 初始化資料庫...")
        db_manager = DatabaseManager(db_name=str(PROJECT_ROOT / DB_FILE_PATH))
        print(f"  ✅ 資料庫初始化成功: {DB_FILE_PATH}")

        print("🔑 初始化關鍵字管理器...")
        keyword_manager = KeywordManager(config_file=str(PROJECT_ROOT / KEYWORDS_CONFIG))

        print("🗺️  初始化座標提取器...")
        coord_extractor = CoordinateExtractor()

        print("📐 初始化風險評分服務...")
        risk_service = RiskAssessmentService(keywords_config_path=str(PROJECT_ROOT / KEYWORDS_CONFIG))

        teams_notifier = None
        if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK:
            print("📢 初始化 Teams 通知器...")
            teams_notifier = TeamsNotifier(TEAMS_WEBHOOK)

        email_notifier = None
        if ENABLE_EMAIL_NOTIFICATIONS and all([MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL]):
            print("📧 初始化 Email 通知器...")
            email_notifier = GmailRelayNotifier(MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL)

        run_cn    = ENABLE_CN_MSA    and (args.source is None or args.source == "cn")
        run_tw    = ENABLE_TW_MPB    and (args.source is None or args.source == "tw")
        run_ukmto = ENABLE_UKMTO     and (args.source is None or args.source == "ukmto")
        scrape_days = args.backfill_days if args.backfill_days else SCRAPE_DAYS
        ukmto_days  = args.backfill_days if args.backfill_days else UKMTO_SCRAPE_DAYS

        print("\n" + "=" * 70)
        print("✅ 所有模組初始化完成")
        print("=" * 70)

        all_captured_today   = []
        all_captured_history = []
        cn_health_reports     = []
        cn_registry_result    = None

        # ── --send-test-email：不爬取，直接用最近資料庫資料寄一封測試信 ──
        if args.send_test_email:
            print("\n📧 --send-test-email：讀取資料庫近期資料產生測試信...")
            recent_df = db_manager.get_all_warnings(limit=10)
            test_items = []
            for _, row in recent_df.iterrows():
                test_items.append({
                    "title": row.get("title"), "bureau": row.get("maritime_bureau"),
                    "time": row.get("publish_time"), "link": row.get("link"),
                    "source": row.get("source_type"), "risk_level": row.get("risk_level") or "MEDIUM",
                    "keywords": (row.get("keywords_matched") or "").split(", ") if row.get("keywords_matched") else [],
                    "coordinates": [],
                })
            if email_notifier:
                email_notifier.send_trigger_email(test_items, [], subject_prefix="[測試信] ")
            else:
                print("  ⚠️ Email 通知未設定完整，無法發送測試信")
            sys.exit(0)

        # ── 中國海事局：多來源 registry（claude.md 三、五） ──
        if run_cn:
            print("\n🇨🇳 執行中國海事局多來源抓取...")
            cn_registry = CNSourceRegistry(
                config_path=str(PROJECT_ROOT / os.getenv("CN_MSA_SOURCES_CONFIG", "config/maritime_sources.json")),
                keyword_manager=keyword_manager,
                coordinate_extractor=coord_extractor.extract_coordinates,
                risk_service=risk_service,
                headless=CHROME_HEADLESS,
                save_debug=args.save_debug,
                debug_dir=str(PROJECT_ROOT / "debug"),
                days=scrape_days,
            )
            cn_registry_result = cn_registry.run()
            cn_health_reports = cn_registry_result.health_reports

            print("\n  📊 中國海事局各來源健康狀態:")
            for report in cn_health_reports:
                row = report.to_row()
                print(f"     {row['來源']}: {row['狀態']} | 列表={row['列表筆數']} | 詳情成功={row['詳情成功']} | 耗時={row['耗時(秒)']}s")
                if row['錯誤'] != '-':
                    print(f"        ⚠️ {row['錯誤']}")

            for bucket_name, bucket in (("today", cn_registry_result.today), ("history", cn_registry_result.history)):
                for item in bucket:
                    item["summary_zh_tw"] = summarizer.rule_based_summary(item.get("title", ""), item.get("cleaned_content", ""))
                    item["recommended_action"] = summarizer.recommended_action(item.get("risk_level", "INFO"))
                    item.setdefault("status", "ACTIVE")
                    item["source"] = "CN_MSA"

                    if args.dry_run:
                        is_new, is_changed, warning_id = True, False, None
                    else:
                        is_new, is_changed, warning_id = db_manager.upsert_rich_warning(item, source_type="CN_MSA")
                    item["id"] = warning_id

                    if is_new or is_changed:
                        if bucket_name == "today":
                            all_captured_today.append(item)
                        else:
                            all_captured_history.append(item)

            print(f"  🇨🇳 中國海事局完成：今日 {len(cn_registry_result.today)} 筆掃描 / 歷史 {len(cn_registry_result.history)} 筆掃描 "
                  f"（新增或變更 {len([w for w in all_captured_today + all_captured_history if w.get('source') == 'CN_MSA'])} 筆）")

        # ── 台灣航港局／UKMTO：沿用既有爬蟲邏輯（本次未變更，避免既有功能退化） ──
        tw_scraper    = None
        ukmto_scraper = None

        if run_tw:
            print("\n🇹🇼 初始化台灣航港局爬蟲...")
            tw_scraper = TWMaritimePortBureauScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                days=scrape_days
            )
            tw_result = tw_scraper.scrape_all_pages()
            for w in tw_scraper.captured_warnings_today:
                all_captured_today.append(w)
            for w in tw_scraper.captured_warnings_history:
                all_captured_history.append(w)

        if run_ukmto:
            print("\n🇬🇧 初始化 UKMTO 爬蟲...")
            ukmto_scraper = UKMTOScraper(
                db_manager=db_manager,
                keyword_manager=keyword_manager,
                teams_notifier=teams_notifier,
                coord_extractor=coord_extractor,
                days=ukmto_days
            )
            ukmto_result = ukmto_scraper.scrape()
            for w in ukmto_scraper.captured_warnings_today:
                all_captured_today.append(w)
            for w in ukmto_scraper.captured_warnings_history:
                all_captured_history.append(w)

        # ── 統一補上風險評分（TW/UKMTO 尚未在來源層計算，這裡統一補齊，claude.md 六） ──
        for w in all_captured_today + all_captured_history:
            if w.get("risk_level") is None or w.get("relevance_score") is None:
                src_type = w.get("source", "TW_MPB")
                src_keywords = keyword_manager.get_keywords_by_source(src_type)
                kw_text = ", ".join(w.get("keywords", [])) if isinstance(w.get("keywords"), list) else str(w.get("keywords", ""))
                assessment = risk_service.assess(
                    title=w.get("title", ""), content=w.get("details", "") or kw_text,
                    source_keywords=src_keywords, has_coordinates=bool(w.get("coordinates")),
                )
                w.update(assessment.to_dict())
            if not w.get("summary_zh_tw"):
                w["summary_zh_tw"] = summarizer.rule_based_summary(w.get("title", ""), w.get("details", ""))
            if not w.get("recommended_action"):
                w["recommended_action"] = summarizer.recommended_action(w.get("risk_level", "INFO"))

        total_warnings = len(all_captured_today) + len(all_captured_history)
        cn_anomaly = source_health_alert.detect_anomaly(cn_health_reports) if cn_health_reports else None
        cn_source_anomaly = cn_anomaly is not None

        # ── --preview-email：只產生本機 HTML 檔，不寄信 ──
        if args.preview_email:
            preview_dir = PROJECT_ROOT / "reports"
            preview_dir.mkdir(exist_ok=True)
            preview_path = preview_dir / f"email_preview_{run_id}.html"
            html_content = email_report_tpl.build_html_report(
                all_captured_today, all_captured_history,
                health_reports=cn_health_reports, source_anomaly=cn_source_anomaly and not all_captured_today,
            )
            preview_path.write_text(html_content, encoding="utf-8")
            print(f"\n🖼️  Email 預覽已產生: {preview_path}")

        # ── 發送通知 ──
        if args.dry_run:
            print(f"\n🧪 DRY-RUN：偵測到 {total_warnings} 筆新增/變更警告，略過資料庫寫入確認與所有通知")
            if cn_anomaly:
                print(f"   ⚠️ 亦偵測到來源異常（DRY-RUN 不發送）：{cn_anomaly.reason}")
        elif args.no_notify:
            print(f"\n🔕 --no-notify：偵測到 {total_warnings} 筆新增/變更警告，已寫入資料庫但略過通知")
        else:
            # 1) 系統異常通知：與一般航警通知分開，即使沒有新警告也要發送（claude.md 第二階段第七節）
            if cn_anomaly:
                print(f"\n🚨 偵測到中國海事局來源異常：{cn_anomaly.reason}")
                if email_notifier and ENABLE_EMAIL_NOTIFICATIONS:
                    anomaly_ok = email_notifier.send_system_anomaly_email(cn_anomaly)
                    db_manager.record_notification_attempt(
                        0, 'EMAIL_SYSTEM_ALERT', TARGET_EMAIL, 'SUCCESS' if anomaly_ok else 'FAILED'
                    )
                if teams_notifier and ENABLE_TEAMS_NOTIFICATIONS:
                    detail_lines = [
                        f"{s['source_name']}: {s['status']} | 最新公告: {s.get('newest_publish_date') or '-'} | {s.get('error_summary') or '-'}"
                        for s in cn_anomaly.failed_sources
                    ]
                    anomaly_result = teams_notifier.send_system_anomaly(cn_anomaly.reason, detail_lines, dry_run=False)
                    db_manager.record_notification_attempt(
                        0, 'TEAMS_SYSTEM_ALERT', 'webhook', 'SUCCESS' if anomaly_result.success else 'FAILED'
                    )

            # 2) 一般警告通知：只在有新增/變更時發送
            if total_warnings > 0:
                print(
                    f"\n📢 發現 {total_warnings} 個新增/變更警告 "
                    f"(今日 {len(all_captured_today)} 筆，歷史 {len(all_captured_history)} 筆)"
                )

                if teams_notifier and ENABLE_TEAMS_NOTIFICATIONS:
                    for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                        group = [w for w in all_captured_today if w.get('source') == src]
                        if group:
                            print(f"\n📤 發送 {src} 通知 [今日新增]...")
                            result = teams_notifier.send_batch(group, src, is_today=True, dry_run=False)
                            for w in group:
                                if w.get('id'):
                                    db_manager.record_notification_attempt(
                                        w['id'], 'TEAMS', 'webhook', 'SUCCESS' if result.success else 'FAILED',
                                        error=result.error or None,
                                    )

                    for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                        group = [w for w in all_captured_history if w.get('source') == src]
                        if group:
                            print(f"\n📤 發送 {src} 通知 [歷史資料]...")
                            result = teams_notifier.send_batch(group, src, is_today=False, dry_run=False)
                            for w in group:
                                if w.get('id'):
                                    db_manager.record_notification_attempt(
                                        w['id'], 'TEAMS', 'webhook', 'SUCCESS' if result.success else 'FAILED',
                                        error=result.error or None,
                                    )

                if email_notifier and ENABLE_EMAIL_NOTIFICATIONS:
                    print("\n📧 發送 Email 通知...")
                    ok = email_notifier.send_trigger_email(
                        all_captured_today, all_captured_history,
                        health_reports=cn_health_reports,
                        source_anomaly=cn_source_anomaly and not all_captured_today,
                    )
                    for w in all_captured_today + all_captured_history:
                        if w.get('id'):
                            db_manager.record_notification_attempt(
                                w['id'], 'EMAIL', TARGET_EMAIL, 'SUCCESS' if ok else 'FAILED'
                            )
            elif not cn_anomaly:
                print("\n✅ 沒有新的警告")

        # ── 執行摘要 ──
        print("\n" + "=" * 70)
        print("📊 執行摘要")
        print("=" * 70)
        print(f"{'來源':<12}{'狀態':<18}{'列表筆數':<10}{'詳情成功':<10}{'新增/變更':<12}{'耗時(秒)':<10}")
        for report in cn_health_reports:
            row = report.to_row()
            changed_count = len([w for w in all_captured_today + all_captured_history if w.get('source') == 'CN_MSA' and w.get('bureau') == report.source_name.replace('中國海事局（中央入口）', row['來源'])])
            print(f"{row['來源']:<12}{row['狀態']:<18}{row['列表筆數']:<10}{row.get('詳情成功', row['列表筆數']):<10}{'-':<12}{row['耗時(秒)']:<10}")

        for src, icon in [("TW_MPB", "🇹🇼 台灣航港局"), ("UKMTO", "🇬🇧 UKMTO")]:
            t_count = len([w for w in all_captured_today if w.get('source') == src])
            h_count = len([w for w in all_captured_history if w.get('source') == src])
            print(f"{icon:<20}{'HEALTHY' if (t_count or h_count) else '-':<18}{'-':<10}{'-':<10}{t_count + h_count:<12}{'-':<10}")

        print(f"\n  📈 總計新增/變更: {total_warnings} 筆")
        if cn_source_anomaly:
            print("  ⚠️ 中國海事局所有來源本次執行均未取得資料，請檢查健康狀態表與 debug 輸出（不等於「今日無新增」）")

        if not args.dry_run:
            print("\n" + "=" * 70)
            db_manager.print_statistics()

        print("\n" + "=" * 70)
        print(f"🎉 系統執行完成 (run_id={run_id})")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
    except Exception as e:
        print(f"\n❌ 執行失敗: {e}")
        traceback.print_exc()

