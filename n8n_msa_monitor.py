#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一海事警告監控系統 (中國海事局 + 台灣航港局 + UKMTO)
支援經緯度提取、Teams 通知、Email 報告
版本: 3.5 - CN_MSA 改為純 requests，移除 Selenium 依賴
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
# ✅ 只保留 UKMTO / TW_MPB 仍需要的 Selenium import
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
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['WDM_SSL_VERIFY'] = '0'
load_dotenv()
warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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


# ==================== 2. 座標提取器 (v3.5 修正版) ====================
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
                lat     = float(groups[0])
                lat_dir = groups[1].upper()
                lon     = float(groups[2])
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
        if not (-90 <= lat <= 90):
            return False
        if not (-180 <= lon <= 180):
            return False
        # ✅ 修正：中國沿海 lon 最小約 108°，原本 60° 下限過嚴
        # 涵蓋範圍：東亞 + 中東 + 非洲東岸（UKMTO 活動區域）
        if not (-60 <= lat <= 60 and 30 <= lon <= 180):
            return False
        return True

    def extract_from_html(self, html_content):
        """
        ✅ 修正：原本 find('div', {'class': 'text', 'id': 'ch_p'}) 是 AND 條件
        改為依序嘗試 class='text' 或 id='ch_p'
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            content_div = (
                soup.find('div', class_='text') or
                soup.find('div', id='ch_p')     or
                soup.find('div', class_='TRS_Editor') or
                soup.find('div', class_='content') or
                soup.find('article')
            )
            if content_div:
                return self.extract_coordinates(content_div.get_text())
            return self.extract_coordinates(soup.get_text())
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


# ==================== 3. 統一 Teams 通知系統 ====================
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
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card_content
                }
            ]
        }

    def send_batch_notification(self, warnings_list, source_type="CN_MSA", is_today=True):
        if not self.webhook_url or not warnings_list:
            return False
        try:
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
            cfg         = source_config.get(source_type, source_config["CN_MSA"])
            source_icon = cfg["icon"]
            source_name = cfg["name"]
            home_url    = cfg["home_url"]
            base_domain = cfg["base_domain"]
            time_badge  = "🆕 今日新增" if is_today else "📚 歷史資料 (近30天)"
            title_color = "Attention" if is_today else "Good"

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
                            first    = coord_list[0]
                            lat, lon = first[0], first[1]
                            lat_dir  = 'N' if lat >= 0 else 'S'
                            lon_dir  = 'E' if lon >= 0 else 'W'
                            coord_summary = f"📍 {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}"
                            if len(coord_list) > 1:
                                coord_summary += f" (+{len(coord_list)-1})"
                    except Exception:
                        coord_summary = "座標格式錯誤"

                item_elements = [
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
                ]

                details_text = w[6] if isinstance(w, (list, tuple)) and len(w) > 6 else ""
                if details_text and source_type == "UKMTO":
                    item_elements.append({
                        "type": "TextBlock",
                        "text": details_text,
                        "wrap": True,
                        "size": "Small",
                        "color": "Default",
                        "spacing": "Small"
                    })

                item_elements.append({
                    "type": "TextBlock",
                    "text": f"📅 {pub_time} | {coord_summary}",
                    "size": "Small",
                    "isSubtle": True
                })

                body_elements.extend(item_elements)

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
            payload    = self._create_adaptive_card(card_title, body_elements, actions)

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
                print(f"❌ {source_name} Teams 通知失敗: HTTP {response.status_code} | {response.text[:200]}")
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

    def send_trigger_email(self, today_warnings, history_warnings):
        if not self.enabled:
            print("ℹ️ Email 通知未啟用")
            return False
        try:
            msg = MIMEMultipart('related')
            total_count = len(today_warnings) + len(history_warnings)
            time_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')

            msg['Subject'] = f"🌊 航行警告監控報告 - 共{total_count}筆 (今日{len(today_warnings)}筆) - {time_str}(TPE)"
            msg['From'] = self.mail_user
            msg['To']   = self.target_email

            msg_alt = MIMEMultipart('alternative')
            msg.attach(msg_alt)
            msg_alt.attach(
                MIMEText(
                    self._generate_html_report(today_warnings, history_warnings),
                    'html',
                    'utf-8'
                )
            )
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
        tpe_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')

        cn_today   = sum(1 for w in today_warnings if w.get('source') == 'CN_MSA')
        tw_today   = sum(1 for w in today_warnings if w.get('source') == 'TW_MPB')
        uk_today   = sum(1 for w in today_warnings if w.get('source') == 'UKMTO')
        cn_history = sum(1 for w in history_warnings if w.get('source') == 'CN_MSA')
        tw_history = sum(1 for w in history_warnings if w.get('source') == 'TW_MPB')
        uk_history = sum(1 for w in history_warnings if w.get('source') == 'UKMTO')

        cn_total = cn_today + cn_history
        tw_total = tw_today + tw_history
        uk_total = uk_today + uk_history

        cn_coords    = sum(len(w.get('coordinates', [])) for w in today_warnings + history_warnings if w.get('source') == 'CN_MSA')
        tw_coords    = sum(len(w.get('coordinates', [])) for w in today_warnings + history_warnings if w.get('source') == 'TW_MPB')
        uk_coords    = sum(len(w.get('coordinates', [])) for w in today_warnings + history_warnings if w.get('source') == 'UKMTO')
        total_coords = cn_coords + tw_coords + uk_coords

        sources_summary = []
        if uk_today: sources_summary.append(f"🇬🇧 UKMTO {uk_today} 筆")
        if cn_today: sources_summary.append(f"🇨🇳 中國海事局 {cn_today} 筆")
        if tw_today: sources_summary.append(f"🇹🇼 台灣航港局 {tw_today} 筆")
        sources_text = " | ".join(sources_summary) if sources_summary else "無新增"

        SOURCE_ORDER = {'UKMTO': 0, 'CN_MSA': 1, 'TW_MPB': 2}

        def _sort_warnings(warnings_list):
            return sorted(warnings_list, key=lambda w: SOURCE_ORDER.get(w.get('source', ''), 99))

        def _render_warnings(warnings_list, is_today):
            result = ""
            for idx, w in enumerate(warnings_list, 1):
                source = w.get('source', '')
                icon   = self._source_icon(source)
                coords = w.get('coordinates', [])
                title  = w.get('title', 'N/A')
                bureau = w.get('bureau', 'N/A')
                time   = w.get('time', 'N/A')
                link   = w.get('link', '#')
                kw     = w.get('keywords', [])
                kw_str = ', '.join(kw) if isinstance(kw, list) else str(kw)

                coord_rows = ""
                if coords:
                    coord_source = w.get('coord_source', 'text')
                    source_label_map = {
                        'next_data': '📡 來源：系統精確座標',
                        'text':      '📝 來源：內文解析',
                        'fallback':  '🔄 來源：備用解析',
                    }
                    source_label = source_label_map.get(coord_source, '📍 座標資訊')
                    coord_rows += f"""
                    <table width="100%" cellpadding="8" cellspacing="0" bgcolor="#F0F7FF">
                      <tr><td>
                        <font face="Arial, sans-serif" size="2" color="#0056B3"><b>{source_label}</b></font><br>
                    """
                    for i, pt in enumerate(coords, 1):
                        lat, lon = pt[0], pt[1]
                        lat_dir  = 'N' if lat >= 0 else 'S'
                        lon_dir  = 'E' if lon >= 0 else 'W'
                        maps_url = f"http://maps.google.com/maps?q={lat:.6f},{lon:.6f}"
                        coord_rows += (
                            f'      <font face="Courier New, monospace" size="2" color="#333333">'
                            f'📍 {i}. {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir} '
                            f'&nbsp;&nbsp;<a href="{maps_url}" target="_blank"><font color="#0056B3">🗺️地圖</font></a></font><br>\n'
                        )
                    coord_rows += "      </td></tr></table><br>"

                level_text = ""
                details_block = ""
                if source == "UKMTO":
                    colour = w.get('colour', '')
                    colour_icon = "🔴" if colour == "Red" else "🟡"
                    level_text = f'&nbsp;&nbsp;<font face="Arial" size="2" color="#D32F2F"><b>{colour_icon} 等級: {colour}</b></font>'
                    if w.get('details'):
                        details_block = f"""
                        <table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFF9E6">
                          <tr><td>
                            <font face="Arial, sans-serif" size="2" color="#4D4D4D">
                              <b>📄 通告內容：</b><br>{w['details']}
                            </font>
                          </td></tr>
                        </table><br>"""

                if is_today:
                    card_border = "#D32F2F"
                    header_bg   = "#D32F2F"
                    badge_html  = '<font face="Arial" size="1" color="#FFD54F"><b>★ NEW</b></font>'
                else:
                    card_border = "#B0BEC5"
                    header_bg   = "#607D8B"
                    badge_html  = ""

                result += f"""
                <table width="100%" cellpadding="0" cellspacing="0"><tr><td height="12"></td></tr></table>
                <table width="100%" cellpadding="1" cellspacing="0" bgcolor="{card_border}">
                  <tr><td>
                    <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#FFFFFF">
                      <tr>
                        <td bgcolor="{header_bg}">
                          <table width="100%" cellpadding="10" cellspacing="0"><tr>
                            <td><font face="Arial, sans-serif" size="3" color="#FFFFFF"><b>{idx}. {icon} {title}</b></font></td>
                            <td align="right" width="60">{badge_html}</td>
                          </tr></table>
                        </td>
                      </tr>
                      <tr><td>
                        <table width="100%" cellpadding="12" cellspacing="0"><tr><td>
                          <table width="100%" cellpadding="4" cellspacing="0"><tr>
                            <td width="33%"><font face="Arial" size="2" color="#4A148C">📋 <b>局處:</b> {bureau}</font></td>
                            <td width="33%"><font face="Arial" size="2" color="#0D47A1">📅 <b>時間:</b> {time}</font></td>
                            <td width="33%"><font face="Arial" size="2" color="#1B5E20">🔑 <b>標籤:</b> {kw_str}</font>{level_text}</td>
                          </tr></table>
                          <hr size="1" color="#EEEEEE">
                          {details_block}
                          {coord_rows}
                          <table cellpadding="8" cellspacing="0" bgcolor="#E3F2FD"><tr><td>
                            <a href="{link}" target="_blank">
                              <font face="Arial, sans-serif" size="2" color="#1976D2"><b>🔗 前往原始網站查看詳情 →</b></font>
                            </a>
                          </td></tr></table>
                        </td></tr></table>
                      </td></tr>
                    </table>
                  </td></tr>
                </table>"""
            return result

        def _badge(value, color):
            return f'<font face="Arial" size="3" color="{color}"><b>{value}</b></font>'

        def _pct(value):
            return f'{round(value / max(total_count, 1) * 100)}%'

        html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>航行警告監控報告</title>
</head>
<body bgcolor="#F4F6F8" style="margin: 0; padding: 0;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#F4F6F8">
  <tr><td align="center">
    <table width="680" cellpadding="0" cellspacing="0" bgcolor="#FFFFFF" align="center">
      <tr>
        <td bgcolor="#0A1628">
          <table width="100%" cellpadding="24" cellspacing="0"><tr><td>
            <font face="Arial, sans-serif" size="5" color="#FFFFFF"><b>🌊 WHL_Maritech_FRM 海事警告監控</b></font><br><br>
            <font face="Arial, sans-serif" size="2" color="#8FA3B8">📅 報告時間：{tpe_now} (TPE) &nbsp;|&nbsp; 系統版本 v3.2</font>
          </td></tr></table>
        </td>
      </tr>"""

        if today_warnings:
            html += f"""
      <tr>
        <td bgcolor="#D32F2F">
          <table width="100%" cellpadding="16" cellspacing="0"><tr><td>
            <font face="Arial, sans-serif" size="4" color="#FFFFFF"><b>🚨 今日發現 {len(today_warnings)} 筆新增航行警告</b></font><br><br>
            <font face="Arial, sans-serif" size="2" color="#FFCDD2">{sources_text}</font>
          </td></tr></table>
        </td>
      </tr>"""
        else:
            html += """
      <tr>
        <td bgcolor="#2E7D32">
          <table width="100%" cellpadding="16" cellspacing="0"><tr><td align="center">
            <font face="Arial, sans-serif" size="3" color="#FFFFFF"><b>✅ 今日無新增航行警告</b></font>
          </td></tr></table>
        </td>
      </tr>"""

        html += """
      <tr><td>
        <table width="100%" cellpadding="20" cellspacing="0"><tr><td>"""

        if today_warnings:
            html += f"""
          <table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFEBEE"><tr><td>
            <font face="Arial, sans-serif" size="4" color="#B71C1C"><b>🚨 今日新增 ({len(today_warnings)} 筆)</b></font>
          </td></tr></table>
          {_render_warnings(_sort_warnings(today_warnings), is_today=True)}
          <br><br><hr size="1" color="#E0E0E0"><br>"""

        if history_warnings:
            html += f"""
          <table width="100%" cellpadding="10" cellspacing="0" bgcolor="#E8F5E9"><tr><td>
            <font face="Arial, sans-serif" size="4" color="#1B5E20"><b>📚 過往歷史資料 ({len(history_warnings)} 筆)</b></font>
          </td></tr></table>
          {_render_warnings(_sort_warnings(history_warnings), is_today=False)}"""

        html += f"""
          <br><hr size="1" color="#E0E0E0"><br>
          <font face="Arial, sans-serif" size="4" color="#333333"><b>📊 警告來源統計</b></font><br><br>
          <table width="100%" cellpadding="10" cellspacing="1" bgcolor="#CFD8DC">
            <tr bgcolor="#263238">
              <td width="28%"><font face="Arial" size="2" color="#FFFFFF"><b>來源</b></font></td>
              <td width="14%" align="center"><font face="Arial" size="2" color="#FFFFFF"><b>🆕 今日</b></font></td>
              <td width="14%" align="center"><font face="Arial" size="2" color="#FFFFFF"><b>📚 歷史</b></font></td>
              <td width="14%" align="center"><font face="Arial" size="2" color="#FFFFFF"><b>📊 小計</b></font></td>
              <td width="14%" align="center"><font face="Arial" size="2" color="#FFFFFF"><b>📍 座標數</b></font></td>
              <td width="16%" align="center"><font face="Arial" size="2" color="#FFFFFF"><b>佔比</b></font></td>
            </tr>
            <tr bgcolor="#FFFFFF">
              <td><font face="Arial" size="3">🇬🇧</font> <font face="Arial" size="2" color="#333333"><b>UKMTO</b></font></td>
              <td align="center">{_badge(uk_today, '#D32F2F' if uk_today else '#9E9E9E')}</td>
              <td align="center">{_badge(uk_history, '#2E7D32' if uk_history else '#9E9E9E')}</td>
              <td align="center">{_badge(uk_total, '#1565C0' if uk_total else '#9E9E9E')}</td>
              <td align="center">{_badge(uk_coords, '#F57F17' if uk_coords else '#9E9E9E')}</td>
              <td align="center"><font face="Arial" size="2" color="#333333">{_pct(uk_total)}</font></td>
            </tr>
            <tr bgcolor="#FAFAFA">
              <td><font face="Arial" size="3">🇨🇳</font> <font face="Arial" size="2" color="#333333"><b>中國海事局</b></font></td>
              <td align="center">{_badge(cn_today, '#D32F2F' if cn_today else '#9E9E9E')}</td>
              <td align="center">{_badge(cn_history, '#2E7D32' if cn_history else '#9E9E9E')}</td>
              <td align="center">{_badge(cn_total, '#1565C0' if cn_total else '#9E9E9E')}</td>
              <td align="center">{_badge(cn_coords, '#F57F17' if cn_coords else '#9E9E9E')}</td>
              <td align="center"><font face="Arial" size="2" color="#333333">{_pct(cn_total)}</font></td>
            </tr>
            <tr bgcolor="#FFFFFF">
              <td><font face="Arial" size="3">🇹🇼</font> <font face="Arial" size="2" color="#333333"><b>台灣航港局</b></font></td>
              <td align="center">{_badge(tw_today, '#D32F2F' if tw_today else '#9E9E9E')}</td>
              <td align="center">{_badge(tw_history, '#2E7D32' if tw_history else '#9E9E9E')}</td>
              <td align="center">{_badge(tw_total, '#1565C0' if tw_total else '#9E9E9E')}</td>
              <td align="center">{_badge(tw_coords, '#F57F17' if tw_coords else '#9E9E9E')}</td>
              <td align="center"><font face="Arial" size="2" color="#333333">{_pct(tw_total)}</font></td>
            </tr>
            <tr bgcolor="#ECEFF1">
              <td><font face="Arial" size="2" color="#333333"><b>📈 合計</b></font></td>
              <td align="center">{_badge(len(today_warnings), '#D32F2F' if today_warnings else '#9E9E9E')}</td>
              <td align="center">{_badge(len(history_warnings), '#2E7D32' if history_warnings else '#9E9E9E')}</td>
              <td align="center">{_badge(total_count, '#1565C0' if total_count else '#9E9E9E')}</td>
              <td align="center">{_badge(total_coords, '#F57F17' if total_coords else '#9E9E9E')}</td>
              <td align="center"><font face="Arial" size="2" color="#333333"><b>100%</b></font></td>
            </tr>
          </table>
        </td></tr></table>
      </td></tr>
      <tr>
        <td bgcolor="#E9ECEF">
          <table width="100%" cellpadding="16" cellspacing="0"><tr><td align="center">
            <font face="Arial, sans-serif" size="2" color="#6C757D">
              ⚠️ 此為自動發送的郵件，請勿直接回覆<br><br>
              航行警告監控系統 v3.2 &nbsp;|&nbsp; Navigation Warning Monitor System
            </font>
          </td></tr></table>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
        return html


# ==================== 5. UKMTO 爬蟲 (v3.2 - 不變) ====================
class UKMTOScraper:
    URL = "https://www.ukmto.org/recent-incidents"
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

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []
        self._next_data_coords: dict   = {}

        print(f"  🇬🇧 UKMTO 爬蟲設定:")
        print(f"     - 抓取範圍: 最近 {days} 天 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
        print(f"     - 今日定義: {self.today_start.strftime('%Y-%m-%d')} 00:00 UTC 起")

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
            resp = requests.get(api_url, timeout=15, verify=False, headers={"User-Agent": "Mozilla/5.0"})
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
        print(f"\n🇬🇧 開始爬取 UKMTO 航行警告...")
        try:
            self.driver.get(self.URL)
            self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "ul.IncidentList_incidentList__NGsl0")
            ))
            print("  ✅ 頁面載入完成")
            time.sleep(2)

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
            li_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ul.IncidentList_incidentList__NGsl0 > li.IncidentList_incident__HgGtN"
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
        try:
            title = elem.find_element(By.CSS_SELECTOR, "h3.IncidentList_title__cOmOY button").text.strip()
        except Exception:
            title = "N/A"
        try:
            colour = elem.find_element(By.CSS_SELECTOR, "span.Pin_pin__dpf_F").get_attribute("data-colour") or "N/A"
        except Exception:
            colour = "N/A"
        try:
            date_str      = elem.find_element(By.CSS_SELECTOR, "ul.IncidentList_meta__JmhSj li span").text.strip()
            incident_date = self._parse_date(date_str)
        except Exception:
            date_str      = "N/A"
            incident_date = None
        try:
            details = elem.find_element(By.CSS_SELECTOR, "p.IncidentList_details__bwUAz").text.strip()
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


# ==================== 7. 中國海事局爬蟲 (v3.5 - 純 requests 版) ====================
class CNMSANavigationWarningsScraper:

    BASE_URL  = "https://www.msa.gov.cn"
    INDEX_URL = "https://www.msa.gov.cn/html/cnmsa/hxaq/aqxx/index.html"

    HARDCODED_BUREAUS = [
        ("上海海事局",       "/94df14ce1110415da44e67593e76619f/index.jhtml"),
        ("天津海事局",       "/bdba5fad6e5d48679f970fcf8efb8636/index.jhtml"),
        ("辽宁海事局",       "/c8896863b1014c438705536a03eb46ff/index.jhtml"),
        ("河北海事局",       "/93b73989d22045f9bc3270a6eba35180/index.jhtml"),
        ("山东海事局",       "/36ea3354c8f84953aba082d6d989c750/index.jhtml"),
        ("浙江海事局",       "/8e10ea74eb9e4c9690f8f891968add80/index.jhtml"),
        ("福建海事局",       "/7b08405760384570a0fb44e9204c4b1d/index.jhtml"),
        ("广东海事局",       "/1e478d409e854918bf12478b8a19f4a8/index.jhtml"),
        ("广西海事局",       "/86de2fffff2c47f98359fd1f20d6508f/index.jhtml"),
        ("海南海事局",       "/d3340711057b494b8fa09eedc4c5ead9/index.jhtml"),
        ("长江海事局",       "/9340423406cc4507b2fb8af2492d2a3d/index.jhtml"),
        ("江苏海事局",       "/b5b0f3c7630d4967b1e6b06208575d15/index.jhtml"),
        ("深圳海事局",       "/325fdc0892b44313a63ee5c165be98ec/index.jhtml"),
        ("连云港海事局",     "/fa4501f3dbe44f70bc726f27132d4e04/index.jhtml"),
        ("江苏省地方海事局", "/d14ed012960b4064971270459a4a0d4d/index.jhtml"),
        ("江西省地方海事局", "/html/cnmsa/hxaq/aqxx/hxjg/jxsdfhsju/index.html"),
    ]

    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor,
                 headless=True, days=7):
        self.db_manager      = db_manager
        self.keyword_manager = keyword_manager
        self.keywords        = keyword_manager.get_keywords()
        self.teams_notifier  = teams_notifier
        self.coord_extractor = coord_extractor

        self.days        = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.new_warnings_today        = []
        self.new_warnings_history      = []
        self.captured_warnings_today   = []
        self.captured_warnings_history = []

        self.session = self._build_session()

        print("🇨🇳 初始化中國海事局爬蟲 v3.5 (純 requests)...")
        print(f"  🌐 {self.INDEX_URL}")
        print(f"  📅 最近 {days} 天 | 截止: {self.cutoff_date.strftime('%Y-%m-%d')} | 今日: {self.today_start.strftime('%Y-%m-%d')}")

    def _build_session(self):
        s = requests.Session()
        s.headers.update({
            'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection':      'keep-alive',
            'Referer':         'https://www.msa.gov.cn/',
        })
        s.verify = False
        return s

    def _get_soup(self, url, timeout=20):
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code == 200:
                    resp.encoding = resp.apparent_encoding or 'utf-8'
                    soup  = BeautifulSoup(resp.text, 'html.parser')
                    title = soup.title.string if soup.title else ''
                    if any(k in title.upper() for k in ['ACCESS DENIED', 'FORBIDDEN']):
                        print(f"    ⛔ 被封鎖 (attempt {attempt+1})")
                        time.sleep(3)
                        continue
                    return soup
                print(f"    ⚠️ HTTP {resp.status_code} (attempt {attempt+1}): {url[:60]}")
                time.sleep(2)
            except requests.exceptions.Timeout:
                print(f"    ⚠️ Timeout (attempt {attempt+1}): {url[:60]}")
                time.sleep(3)
            except Exception as e:
                print(f"    ⚠️ 請求失敗 (attempt {attempt+1}): {type(e).__name__}")
                time.sleep(2)
        return None

    def check_keywords(self, text):
        if not text:
            return []
        return [k for k in self.keywords if k.lower() in text.lower()]

    def parse_date(self, date_str):
        if not date_str:
            return None
        date_str = date_str.strip()
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日']:
            try:
                return datetime.strptime(date_str, fmt)
            except Exception:
                continue
        m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', date_str)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except Exception:
                pass
        return None

    def _fetch_bureau_list(self):
        print("  📡 動態抓取各局連結...")
        soup = self._get_soup(self.INDEX_URL)
        if not soup:
            print("  ⚠️ 首頁抓取失敗，使用硬編碼清單")
            return self._build_hardcoded_list()

        bureaus = []
        for li in soup.find_all('li', class_='nav_lv2_list'):
            a_tag    = li.find('a', href=True)
            name_div = li.find('div', class_='nav_lv2_text')
            if not a_tag or not name_div:
                continue
            name = name_div.get_text(strip=True)
            href = a_tag.get('href', '')
            if not name or not href:
                continue
            full_url = href if href.startswith('http') else f"{self.BASE_URL}{href}"
            bureaus.append((name, full_url))

        if bureaus:
            print(f"  ✅ 動態抓取成功，共 {len(bureaus)} 個局")
            return bureaus

        print("  ⚠️ 動態解析到 0 個局，使用硬編碼清單")
        return self._build_hardcoded_list()

    def _build_hardcoded_list(self):
        result = [
            (name, path if path.startswith('http') else f"{self.BASE_URL}{path}")
            for name, path in self.HARDCODED_BUREAUS
        ]
        print(f"  📋 硬編碼清單：{len(result)} 個局")
        return result

    def _extract_items(self, soup):
        items = []
        seen  = set()

        # ── 方法 A：table > tr > td（已實測確認）──
        for tr in soup.find_all('tr'):
            tds   = tr.find_all('td')
            if len(tds) < 2:
                continue
            a_tag = tds[0].find('a', href=True)
            if not a_tag:
                continue
            title = (a_tag.get('title') or a_tag.get_text(strip=True) or '').strip()
            title = re.sub(r'\s*\d{4}[-/]\d{2}[-/]\d{2}\s*$', '', title).strip()
            if not title or len(title) < 4:
                continue
            href = a_tag.get('href', '')
            if not href or href.startswith(('javascript:', '#')):
                continue
            if href.startswith('/'):
                href = f"{self.BASE_URL}{href}"
            elif not href.startswith('http'):
                href = f"{self.BASE_URL}/{href}"
            publish_time = tds[1].get_text(strip=True)
            if href in seen:
                continue
            seen.add(href)
            items.append({'title': title, 'link': href, 'publish_time': publish_time})

        if items:
            return items

        # ── 方法 B：ul > li（備用）──
        for li in soup.find_all('li'):
            a_tag = li.find('a', href=True)
            if not a_tag:
                continue
            title = (a_tag.get('title') or a_tag.get_text(strip=True) or '').strip()
            title = re.sub(r'\s*\d{4}[-/]\d{2}[-/]\d{2}\s*$', '', title).strip()
            if not title or len(title) < 4:
                continue
            href = a_tag.get('href', '')
            if not href or href.startswith(('javascript:', '#')):
                continue
            if href.startswith('/'):
                href = f"{self.BASE_URL}{href}"
            elif not href.startswith('http'):
                href = f"{self.BASE_URL}/{href}"
            publish_time = ''
            for tag in li.find_all(['span', 'em', 'i', 'div']):
                txt = tag.get_text(strip=True)
                if re.match(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', txt):
                    publish_time = txt[:10]
                    break
            if not publish_time:
                m = re.search(r'(\d{4}-\d{2}-\d{2})', li.get_text())
                if m:
                    publish_time = m.group(1)
            if href in seen:
                continue
            seen.add(href)
            items.append({'title': title, 'link': href, 'publish_time': publish_time})

        return items

    # ✅ 修正：縮排正確，屬於 class 內部
    def _fetch_detail_coords(self, link):
        """用 requests 抓詳情頁，提取座標"""
        if not link or link.startswith('javascript'):
            return []
        soup = self._get_soup(link, timeout=15)
        if not soup:
            return []
        try:
            if hasattr(self.coord_extractor, 'extract_from_html'):
                coords = self.coord_extractor.extract_from_html(str(soup))
            else:
                content = (
                    soup.find('div', class_='text')       or
                    soup.find('div', id='ch_p')            or
                    soup.find('div', class_='TRS_Editor')  or
                    soup.find('div', class_='content')     or
                    soup.find('article')                   or
                    soup
                )
                coords = self.coord_extractor.extract_coordinates(content.get_text())
        except Exception as e:
            print(f"      ⚠️ 座標提取失敗: {type(e).__name__}: {e}")
            coords = []
        if coords:
            print(f"      📍 詳情頁取得 {len(coords)} 個座標")
        return coords

    def _process_items(self, items, bureau_name):
        matched_count = 0
        skipped_date  = 0
        skipped_kw    = 0

        for item in items:
            title        = item['title'].strip()
            link         = item['link']
            publish_time = item['publish_time'].strip()

            if not title:
                continue
            if not publish_time:
                skipped_date += 1
                continue
            p_date = self.parse_date(publish_time)
            if not p_date or p_date < self.cutoff_date:
                skipped_date += 1
                continue

            is_today   = p_date >= self.today_start
            time_label = "🆕 今日" if is_today else "📚 歷史"

            matched = self.check_keywords(title)
            if not matched:
                skipped_kw += 1
                continue

            matched_count += 1
            print(f"      {time_label} ✅ [{publish_time}] {title[:55]} | {matched[:3]}")

            coordinates = self.coord_extractor.extract_coordinates(title)
            for dc in self._fetch_detail_coords(link):
                if dc not in coordinates:
                    coordinates.append(dc)

            db_data = (
                bureau_name, title, link, publish_time,
                ', '.join(matched),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                coordinates
            )
            is_new, w_id = self.db_manager.save_warning(db_data, source_type="CN_MSA")

            if is_new and w_id:
                warning_data = {
                    'id':           w_id,
                    'bureau':       bureau_name,
                    'title':        title,
                    'link':         link,
                    'time':         publish_time,
                    'keywords':     matched,
                    'source':       'CN_MSA',
                    'coordinates':  coordinates,
                    'coord_source': 'text',
                }
                if is_today:
                    self.new_warnings_today.append(w_id)
                    self.captured_warnings_today.append(warning_data)
                    print(f"      💾 [今日] DB ID: {w_id}")
                else:
                    self.new_warnings_history.append(w_id)
                    self.captured_warnings_history.append(warning_data)
                    print(f"      💾 [歷史] DB ID: {w_id}")
            else:
                print(f"      ⏭️  已存在")

        print(
            f"    📊 {bureau_name} | "
            f"命中={matched_count} | 日期過濾={skipped_date} | 關鍵字未命中={skipped_kw}"
        )

    def scrape_all_bureaus(self):
        print(f"\n🇨🇳 開始爬取中國海事局航行警告 (v3.5)...")
        print(f"  🌐 {self.INDEX_URL}")
        try:
            bureau_list = self._fetch_bureau_list()
            print(f"\n  📍 共 {len(bureau_list)} 個海事局待爬取")

            for bureau_name, bureau_url in bureau_list:
                print(f"\n  🔍 {bureau_name}")
                print(f"     {bureau_url}")

                soup = self._get_soup(bureau_url)
                if not soup:
                    print(f"    ❌ 頁面抓取失敗，跳過")
                    continue

                items = self._extract_items(soup)
                print(f"    📋 解析到 {len(items)} 個項目", end="")

                if not items:
                    print()
                    for a in soup.find_all('a', href=True)[:5]:
                        print(f"    🔗 {a.get('href','')} | {a.get_text(strip=True)[:40]}")
                    time.sleep(1)
                    continue

                dates = [i['publish_time'] for i in items if i['publish_time']]
                print(f" | 日期: {min(dates)} ~ {max(dates)}" if dates else "")

                self._process_items(items, bureau_name)
                time.sleep(1)

        except Exception as e:
            print(f"❌ 中國海事局爬取錯誤: {e}")
            traceback.print_exc()

        total_new = len(self.new_warnings_today) + len(self.new_warnings_history)
        print(f"\n🇨🇳 中國海事局爬取完成:")
        print(f"   🆕 今日新增: {len(self.new_warnings_today)} 筆")
        print(f"   📚 歷史資料: {len(self.new_warnings_history)} 筆")
        print(f"   📊 總計新增: {total_new} 筆")
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
ENABLE_CN_MSA              = os.getenv("ENABLE_CN_MSA",  "true").lower() == "true"
ENABLE_TW_MPB              = os.getenv("ENABLE_TW_MPB",  "true").lower() == "true"
ENABLE_UKMTO               = os.getenv("ENABLE_UKMTO",   "true").lower() == "true"
SCRAPE_DAYS                = int(os.getenv("SCRAPE_DAYS",       "7"))   # v3.2: 預設改為 7 天
UKMTO_SCRAPE_DAYS          = int(os.getenv("UKMTO_SCRAPE_DAYS", "30"))

print("\n" + "=" * 70)
print("⚙️  系統設定檢查 v3.2")
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


# ==================== 9. 主程式進入點 ====================
if __name__ == "__main__":
    try:
        print("\n" + "=" * 70)
        print("🌊 海事警告監控系統啟動 v3.2")
        print("=" * 70)

        print("\n📦 初始化資料庫...")
        db_manager = DatabaseManager(db_name=DB_FILE_PATH)
        print(f"  ✅ 資料庫初始化成功: {DB_FILE_PATH}")

        print("🔑 初始化關鍵字管理器...")
        keyword_manager = KeywordManager(config_file=KEYWORDS_CONFIG)

        print("🗺️  初始化座標提取器...")
        coord_extractor = CoordinateExtractor()

        teams_notifier = None
        if ENABLE_TEAMS_NOTIFICATIONS and TEAMS_WEBHOOK:
            print("📢 初始化 Teams 通知器...")
            teams_notifier = UnifiedTeamsNotifier(TEAMS_WEBHOOK)

        email_notifier = None
        if ENABLE_EMAIL_NOTIFICATIONS and all([MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL]):
            print("📧 初始化 Email 通知器...")
            email_notifier = GmailRelayNotifier(MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL)

        cn_scraper    = None
        tw_scraper    = None
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

        print("\n" + "=" * 70)
        print("✅ 所有模組初始化完成")
        print("=" * 70)

        # ── 開始爬取 ──
        print("\n🚀 開始爬取海事警告...")

        all_warnings_today   = []
        all_warnings_history = []
        all_captured_today   = []
        all_captured_history = []

        if cn_scraper:
            cn_result = cn_scraper.scrape_all_bureaus()
            all_warnings_today.extend(cn_result['today'])
            all_warnings_history.extend(cn_result['history'])
            all_captured_today.extend(cn_scraper.captured_warnings_today)
            all_captured_history.extend(cn_scraper.captured_warnings_history)

        if tw_scraper:
            tw_result = tw_scraper.scrape_all_pages()
            all_warnings_today.extend(tw_result['today'])
            all_warnings_history.extend(tw_result['history'])
            all_captured_today.extend(tw_scraper.captured_warnings_today)
            all_captured_history.extend(tw_scraper.captured_warnings_history)

        if ukmto_scraper:
            ukmto_result = ukmto_scraper.scrape()
            all_warnings_today.extend(ukmto_result['today'])
            all_warnings_history.extend(ukmto_result['history'])
            all_captured_today.extend(ukmto_scraper.captured_warnings_today)
            all_captured_history.extend(ukmto_scraper.captured_warnings_history)

        # ── 發送通知 ──
        total_warnings = len(all_warnings_today) + len(all_warnings_history)

        if total_warnings > 0:
            print(
                f"\n📢 發現 {total_warnings} 個警告 "
                f"(今日 {len(all_warnings_today)} 筆，歷史 {len(all_warnings_history)} 筆)"
            )

            if teams_notifier and ENABLE_TEAMS_NOTIFICATIONS:

                def _to_teams_tuple(w):
                    return (
                        w.get('id'),
                        w.get('bureau'),
                        w.get('title'),
                        w.get('link'),
                        w.get('time'),
                        ', '.join(w.get('keywords', [])) if isinstance(w.get('keywords'), list) else w.get('keywords', ''),
                        w.get('details', ''),
                        json.dumps(w.get('coordinates', []))
                    )

                for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                    group = [w for w in all_captured_today if w.get('source') == src]
                    if group:
                        print(f"\n📤 發送 {src} 通知 [今日新增]...")
                        teams_notifier.send_batch_notification(
                            [_to_teams_tuple(w) for w in group], src, is_today=True
                        )

                for src in ["CN_MSA", "TW_MPB", "UKMTO"]:
                    group = [w for w in all_captured_history if w.get('source') == src]
                    if group:
                        print(f"\n📤 發送 {src} 通知 [歷史資料]...")
                        teams_notifier.send_batch_notification(
                            [_to_teams_tuple(w) for w in group], src, is_today=False
                        )

            if email_notifier and ENABLE_EMAIL_NOTIFICATIONS:
                print("\n📧 發送 Email 通知...")
                email_notifier.send_trigger_email(all_captured_today, all_captured_history)

        else:
            print("\n✅ 沒有新的警告")

        # ── 執行摘要 ──
        print("\n" + "=" * 70)
        print("📊 執行摘要")
        print("=" * 70)

        for src, icon in [
            ("CN_MSA", "🇨🇳 中國海事局"),
            ("TW_MPB", "🇹🇼 台灣航港局"),
            ("UKMTO",  "🇬🇧 UKMTO")
        ]:
            t_count  = len([w for w in all_captured_today   if w.get('source') == src])
            h_count  = len([w for w in all_captured_history if w.get('source') == src])
            t_coords = sum(len(w.get('coordinates', [])) for w in all_captured_today   if w.get('source') == src)
            h_coords = sum(len(w.get('coordinates', [])) for w in all_captured_history if w.get('source') == src)

            if src == "UKMTO":
                all_ukmto = [w for w in all_captured_today + all_captured_history if w.get('source') == 'UKMTO']
                nd_count  = len([w for w in all_ukmto if w.get('coord_source') == 'next_data'])
                tx_count  = len([w for w in all_ukmto if w.get('coord_source') == 'text'])
                no_count  = len([w for w in all_ukmto if w.get('coord_source') == 'none'])
                print(f"\n  {icon}:")
                print(f"     🆕 今日新增: {t_count} 筆 ({t_coords} 個座標點)")
                print(f"     📚 歷史資料: {h_count} 筆 ({h_coords} 個座標點)")
                print(f"     📡 座標來源: __NEXT_DATA__={nd_count} | 文字解析={tx_count} | 無座標={no_count}")
            else:
                print(f"\n  {icon}:")
                print(f"     🆕 今日新增: {t_count} 筆 ({t_coords} 個座標點)")
                print(f"     📚 歷史資料: {h_count} 筆 ({h_coords} 個座標點)")

        total_coords = sum(
            len(w.get('coordinates', []))
            for w in all_captured_today + all_captured_history
        )
        print(f"\n  📈 總計: {total_warnings} 筆警告")
        print(f"  📍 總座標點數: {total_coords}")

        print("\n" + "=" * 70)
        db_manager.print_statistics()

        print("\n" + "=" * 70)
        print("🎉 系統執行完成 v3.2")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
    except Exception as e:
        print(f"\n❌ 執行失敗: {e}")
        traceback.print_exc()

