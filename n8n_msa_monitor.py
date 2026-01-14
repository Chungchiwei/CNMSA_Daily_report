#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一海事警告監控系統 (中國海事局 + 台灣航港局)
支援經緯度提取、地圖繪製、Teams 通知、Email 報告
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

# ==================== 地圖繪製相關套件 ====================
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    MAPPING_AVAILABLE = True
    print("✅ 地圖繪製模組載入成功")
except ImportError as e:
    MAPPING_AVAILABLE = False
    print(f"⚠️ 地圖繪製模組未安裝: {e}")

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
    def __init__(self):
        # ========== 正則表達式模式 (按優先順序) ==========
        
        # 1. 度-分.小數格式 (最常見)
        # 例如: 35-23.50N 119-35.92E, 18-17.37N 109-22.17E
        self.pattern_dm_decimal = re.compile(
            r'(\d{1,3})[°\-\s]*(\d{1,2}\.?\d*)[′\'\-\s]*([NSns])\s*[,，\s]*'
            r'(\d{1,3})[°\-\s]*(\d{1,2}\.?\d*)[′\'\-\s]*([EWew])',
            re.IGNORECASE
        )
        
        # 2. 度分秒格式
        # 例如: 25°30'15"N 121°20'30"E
        self.pattern_dms = re.compile(
            r'(\d{1,3})[°\s]*(\d{1,2})[′\'\s]*(\d{1,2}\.?\d*)[″"\s]*([NSns])\s*[,，\s]*'
            r'(\d{1,3})[°\s]*(\d{1,2})[′\'\s]*(\d{1,2}\.?\d*)[″"\s]*([EWew])',
            re.IGNORECASE
        )
        
        # 3. 純度分格式 (無秒)
        # 例如: 25°30'N 121°20'E
        self.pattern_dm = re.compile(
            r'(\d{1,3})[°\s]*(\d{1,2})[′\'\s]*([NSns])\s*[,，\s]*'
            r'(\d{1,3})[°\s]*(\d{1,2})[′\'\s]*([EWew])',
            re.IGNORECASE
        )
        
        # 4. 十進制度數格式
        # 例如: 25.5N 121.3E, 25.5°N 121.3°E
        self.pattern_decimal = re.compile(
            r'(\d{1,3}\.?\d*)[°\s]*([NSns])\s*[,，\s]*'
            r'(\d{1,3}\.?\d*)[°\s]*([EWew])',
            re.IGNORECASE
        )
        
        # 5. 中文格式
        # 例如: 北緯25度30分 東經121度20分
        self.pattern_chinese = re.compile(
            r'[北南]緯\s*(\d{1,3})\s*度\s*(\d{1,2}\.?\d*)\s*分\s*'
            r'[東西]經\s*(\d{1,3})\s*度\s*(\d{1,2}\.?\d*)\s*分',
            re.IGNORECASE
        )
        
        print("  🗺️ 座標提取器初始化完成")
    
    def _convert_to_decimal(self, degrees, minutes=0, seconds=0, direction='N'):
        """轉換為十進制度數"""
        try:
            degrees = float(degrees)
            minutes = float(minutes) if minutes else 0
            seconds = float(seconds) if seconds else 0
            
            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
            
            # 根據方向調整正負號
            if direction.upper() in ['S', 'W']:
                decimal = -decimal
            
            return round(decimal, 6)
        except Exception as e:
            print(f"    ⚠️ 座標轉換錯誤: {e}")
            return None
    
    def _validate_coordinate(self, lat, lon):
        """驗證座標是否在合理範圍內 (亞太海域)"""
        try:
            lat = float(lat)
            lon = float(lon)
            
            # 緯度範圍: -60° 到 60° (涵蓋南北半球主要海域)
            # 經度範圍: 60° 到 180° (亞太地區)
            if -60 <= lat <= 60 and 60 <= lon <= 180:
                return True
            
            # 西經轉換 (如果有的話)
            if -180 <= lon < 0:
                lon = 360 + lon
                if 60 <= lon <= 180:
                    return True
            
            return False
        except:
            return False
    
    def extract_coordinates(self, text):
        """從文字中提取所有座標 (增強版)"""
        if not text:
            return []
        
        coordinates = []
        
        # 預處理文字：統一格式
        text = text.replace('，', ',').replace('。', '.')
        
        # ========== 1. 度-分.小數格式 (優先) ==========
        matches = self.pattern_dm_decimal.findall(text)
        for match in matches:
            try:
                lat_deg, lat_min, lat_dir, lon_deg, lon_min, lon_dir = match
                
                # 轉換為十進制
                lat = self._convert_to_decimal(lat_deg, lat_min, 0, lat_dir)
                lon = self._convert_to_decimal(lon_deg, lon_min, 0, lon_dir)
                
                if lat is not None and lon is not None:
                    if self._validate_coordinate(lat, lon):
                        coord = (lat, lon)
                        if coord not in coordinates:
                            coordinates.append(coord)
                            print(f"    ✅ 提取座標 (度-分.小數): {lat:.4f}°, {lon:.4f}°")
            except Exception as e:
                print(f"    ⚠️ 解析座標失敗 (度-分.小數): {match} - {e}")
                continue
        
        # ========== 2. 度分秒格式 ==========
        matches = self.pattern_dms.findall(text)
        for match in matches:
            try:
                lat_deg, lat_min, lat_sec, lat_dir, lon_deg, lon_min, lon_sec, lon_dir = match
                
                lat = self._convert_to_decimal(lat_deg, lat_min, lat_sec, lat_dir)
                lon = self._convert_to_decimal(lon_deg, lon_min, lon_sec, lon_dir)
                
                if lat is not None and lon is not None:
                    if self._validate_coordinate(lat, lon):
                        coord = (lat, lon)
                        if coord not in coordinates:
                            coordinates.append(coord)
                            print(f"    ✅ 提取座標 (度分秒): {lat:.4f}°, {lon:.4f}°")
            except Exception as e:
                print(f"    ⚠️ 解析座標失敗 (度分秒): {match} - {e}")
                continue
        
        # ========== 3. 純度分格式 ==========
        matches = self.pattern_dm.findall(text)
        for match in matches:
            try:
                lat_deg, lat_min, lat_dir, lon_deg, lon_min, lon_dir = match
                
                lat = self._convert_to_decimal(lat_deg, lat_min, 0, lat_dir)
                lon = self._convert_to_decimal(lon_deg, lon_min, 0, lon_dir)
                
                if lat is not None and lon is not None:
                    if self._validate_coordinate(lat, lon):
                        coord = (lat, lon)
                        if coord not in coordinates:
                            coordinates.append(coord)
                            print(f"    ✅ 提取座標 (度分): {lat:.4f}°, {lon:.4f}°")
            except Exception as e:
                print(f"    ⚠️ 解析座標失敗 (度分): {match} - {e}")
                continue
        
        # ========== 4. 十進制度數格式 ==========
        matches = self.pattern_decimal.findall(text)
        for match in matches:
            try:
                lat, lat_dir, lon, lon_dir = match
                
                lat = self._convert_to_decimal(lat, 0, 0, lat_dir)
                lon = self._convert_to_decimal(lon, 0, 0, lon_dir)
                
                if lat is not None and lon is not None:
                    if self._validate_coordinate(lat, lon):
                        coord = (lat, lon)
                        if coord not in coordinates:
                            coordinates.append(coord)
                            print(f"    ✅ 提取座標 (十進制): {lat:.4f}°, {lon:.4f}°")
            except Exception as e:
                print(f"    ⚠️ 解析座標失敗 (十進制): {match} - {e}")
                continue
        
        # ========== 5. 中文格式 ==========
        matches = self.pattern_chinese.findall(text)
        for match in matches:
            try:
                lat_deg, lat_min, lon_deg, lon_min = match
                
                # 中文格式預設北緯東經
                lat = self._convert_to_decimal(lat_deg, lat_min, 0, 'N')
                lon = self._convert_to_decimal(lon_deg, lon_min, 0, 'E')
                
                if lat is not None and lon is not None:
                    if self._validate_coordinate(lat, lon):
                        coord = (lat, lon)
                        if coord not in coordinates:
                            coordinates.append(coord)
                            print(f"    ✅ 提取座標 (中文): {lat:.4f}°, {lon:.4f}°")
            except Exception as e:
                print(f"    ⚠️ 解析座標失敗 (中文): {match} - {e}")
                continue
        
        return coordinates
    
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


# ==================== 2. 海圖繪製器 ====================
class MaritimeMapPlotter:
    """繪製海事警告區域地圖"""
    
    def __init__(self):
        self.output_dir = "maps"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def plot_warnings(self, warnings_data, output_filename="maritime_warnings.png"):
        """繪製多個警告的座標地圖"""
        if not MAPPING_AVAILABLE:
            print("❌ 地圖繪製功能不可用")
            return None
        
        if not warnings_data:
            print("⚠️ 無座標資料可繪製")
            return None
        
        try:
            # 收集所有座標
            all_coords = []
            for warning in warnings_data:
                all_coords.extend(warning.get('coordinates', []))
            
            if not all_coords:
                print("⚠️ 無有效座標可繪製")
                return None
            
            # 計算地圖範圍
            lats = [c[0] for c in all_coords]
            lons = [c[1] for c in all_coords]
            
            lat_min, lat_max = min(lats) - 2, max(lats) + 2
            lon_min, lon_max = min(lons) - 2, max(lons) + 2
            
            # 建立地圖
            fig = plt.figure(figsize=(16, 12))
            ax = plt.axes(projection=ccrs.PlateCarree())
            
            # 設定地圖範圍
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
            
            # 添加地圖特徵
            ax.add_feature(cfeature.LAND, facecolor='lightgray', edgecolor='black', linewidth=0.5)
            ax.add_feature(cfeature.OCEAN, facecolor='lightblue')
            ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
            ax.add_feature(cfeature.BORDERS, linestyle=':', linewidth=0.5)
            ax.gridlines(draw_labels=True, linewidth=0.5, color='gray', alpha=0.5, linestyle='--')
            
            # 顏色列表 (區分來源)
            cn_colors = ['red', 'darkred', 'crimson', 'orangered']
            tw_colors = ['blue', 'darkblue', 'navy', 'royalblue']
            
            # 繪製每個警告的座標
            cn_idx = 0
            tw_idx = 0
            
            for warning in warnings_data:
                coords = warning.get('coordinates', [])
                if not coords:
                    continue
                
                source = warning.get('source', 'CN_MSA')
                bureau = warning.get('bureau', 'Unknown')
                title = warning.get('title', '')[:30]
                
                # 根據來源選擇顏色
                if source == 'TW_MPB':
                    color = tw_colors[tw_idx % len(tw_colors)]
                    marker = 's'  # 方形
                    tw_idx += 1
                    source_label = f"🇹🇼 {bureau}"
                else:
                    color = cn_colors[cn_idx % len(cn_colors)]
                    marker = 'o'  # 圓形
                    cn_idx += 1
                    source_label = f"🇨🇳 {bureau}"
                
                # 繪製點
                for idx, (lat, lon) in enumerate(coords):
                    ax.plot(lon, lat, marker=marker, color=color, markersize=12, 
                           transform=ccrs.PlateCarree(), 
                           label=source_label if idx == 0 else "")
                    
                    # 添加座標標籤
                    ax.text(lon + 0.15, lat + 0.15, f"({lat:.2f}, {lon:.2f})", 
                           fontsize=9, transform=ccrs.PlateCarree(), 
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7))
                
                # 如果有多個點，繪製連線(表示區域)
                if len(coords) > 1:
                    lons_line = [c[1] for c in coords] + [coords[0][1]]
                    lats_line = [c[0] for c in coords] + [coords[0][0]]
                    ax.plot(lons_line, lats_line, color=color, linewidth=2, 
                           linestyle='--', alpha=0.6, transform=ccrs.PlateCarree())
                    
                    # 填充區域
                    polygon = Polygon([(c[1], c[0]) for c in coords], 
                                    facecolor=color, alpha=0.2, 
                                    transform=ccrs.PlateCarree())
                    ax.add_patch(polygon)
            
            # 標題
            plt.title(f"Maritime Navigation Warnings Map\n"
                     f"({len(warnings_data)} warnings, {len(all_coords)} coordinates)\n"
                     f"🇨🇳 China MSA | 🇹🇼 Taiwan MPB", 
                     fontsize=18, fontweight='bold', pad=20)
            
            # 圖例
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                by_label = dict(zip(labels, handles))
                ax.legend(by_label.values(), by_label.keys(), 
                         loc='upper right', fontsize=10, framealpha=0.9)
            
            # 儲存圖片
            output_path = os.path.join(self.output_dir, output_filename)
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            print(f"✅ 地圖已儲存: {output_path}")
            return output_path
        
        except Exception as e:
            print(f"❌ 地圖繪製失敗: {e}")
            traceback.print_exc()
            return None


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
        """發送批量警告通知 (含座標資訊) - 修正 SSL 錯誤"""
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
            
            # ========== 關鍵修正：加入 verify=False 和 timeout ==========
            print(f"  📤 正在發送 Teams 通知到: {self.webhook_url[:50]}...")
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30,
                verify=False  # ✅ 關閉 SSL 憑證驗證
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
    
    def send_trigger_email(self, json_data, html_content, map_path=None):
        """發送觸發郵件 (含地圖附件)"""
        if not self.enabled:
            print("ℹ️ Email 通知未啟用")
            return False
        
        try:
            msg = MIMEMultipart('related')
            msg['Subject'] = f"🌊 海事警告監控報告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            msg['From'] = self.mail_user
            msg['To'] = self.target_email
            
            # HTML 內容
            msg_alternative = MIMEMultipart('alternative')
            msg.attach(msg_alternative)
            
            # 如果有地圖，在 HTML 中嵌入
            if map_path and os.path.exists(map_path):
                html_with_map = html_content.replace(
                    '</body>',
                    f'''
                    <div style="text-align:center; margin:30px 0;">
                        <h3 style="color:#003366;">🗺️ 警告區域地圖</h3>
                        <img src="cid:map_image" style="max-width:100%; border:2px solid #ddd; border-radius:8px;">
                    </div>
                    </body>
                    '''
                )
            else:
                html_with_map = html_content
            
            msg_alternative.attach(MIMEText(html_with_map, 'html', 'utf-8'))
            
            # 附加地圖圖片
            if map_path and os.path.exists(map_path):
                try:
                    with open(map_path, 'rb') as f:
                        img = MIMEImage(f.read())
                        img.add_header('Content-ID', '<map_image>')
                        img.add_header('Content-Disposition', 'inline', filename='maritime_warnings_map.png')
                        msg.attach(img)
                    print("  ✅ 地圖已附加到 Email")
                except Exception as e:
                    print(f"  ⚠️ 無法附加地圖: {e}")
            
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


# ==================== 5. 台灣航港局爬蟲 (使用 Selenium，含座標提取) ====================
# ==================== 5. 台灣航港局爬蟲 (Selenium 版本，修正動態載入) ====================
class TWMaritimePortBureauScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, days=3):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        self.coord_extractor = coord_extractor
        
        self.base_url = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
        
        self.days = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.new_warnings = []
        self.captured_warnings_data = []
        
        # 定義要抓取的分類
        self.target_categories = {
            '333': '礙航公告',
            '334': '射擊公告'
        }
        
        print(f"  📅 台灣航港局爬蟲設定: 抓取最近 {days} 天資料")
        
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
        
        # 允許載入圖片以確保完整渲染
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
            # 等待標籤載入
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.tabs a"))
            )
            
            # 找到對應的標籤
            if category_id:
                # 使用 data-val 屬性找到標籤
                tab_xpath = f"//div[@class='tabs']//a[@data-val='{category_id}']"
                tab = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
            else:
                # 點擊「全部」標籤
                tab_xpath = "//div[@class='tabs']//a[@class='active']"
                tab = self.driver.find_element(By.XPATH, tab_xpath)
            
            # 滾動到元素位置
            self.driver.execute_script("arguments[0].scrollIntoView(true);", tab)
            time.sleep(0.5)
            
            # 點擊標籤
            self.driver.execute_script("arguments[0].click();", tab)
            print(f"    ✅ 已點擊分類標籤")
            
            # 等待內容更新
            time.sleep(3)
            
            return True
            
        except Exception as e:
            print(f"    ⚠️ 點擊分類標籤失敗: {e}")
            return False
    
    def get_notices_selenium(self, page=1, base_category_id=None):
        """使用 Selenium 爬取指定頁面"""
        try:
            category_name = self.target_categories.get(base_category_id, '全部') if base_category_id else '全部'
            print(f"  正在請求台灣航港局 [{category_name}] 第 {page} 頁...")
            
            # 第一次載入或切換分類
            if page == 1:
                # 載入主頁面
                print(f"    🌐 載入主頁面...")
                self.driver.get(self.base_url)
                time.sleep(3)
                
                # 點擊分類標籤
                if base_category_id:
                    if not self.click_category_tab(base_category_id):
                        return {'has_data': False, 'notices': [], 'processed': 0}
            else:
                # 翻頁
                try:
                    # 找到「下一頁」按鈕
                    next_button = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "li.next a"))
                    )
                    
                    # 滾動到按鈕位置
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                    time.sleep(0.5)
                    
                    # 點擊下一頁
                    self.driver.execute_script("arguments[0].click();", next_button)
                    print(f"    ✅ 已點擊下一頁")
                    
                    time.sleep(3)
                    
                except Exception as e:
                    print(f"    ⚠️ 無法翻頁: {e}")
                    return {'has_data': False, 'notices': [], 'processed': 0}
            
            # 等待內容載入
            try:
                # 等待 table div 出現
                self.wait.until(
                    EC.presence_of_element_located((By.ID, "table"))
                )
                
                # 等待 dl 元素出現
                self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#table dl"))
                )
                
                print(f"    ✅ 頁面內容載入完成")
                
            except Exception as e:
                print(f"    ⚠️ 等待內容載入超時: {e}")
                
                # Debug: 截圖
                try:
                    screenshot_path = f"tw_mpb_debug_{category_name}_p{page}.png"
                    self.driver.save_screenshot(screenshot_path)
                    print(f"    📸 已儲存截圖: {screenshot_path}")
                except:
                    pass
                
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            # 使用 BeautifulSoup 解析
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # 找到 table div
            table_div = soup.find('div', id='table')
            
            if not table_div:
                print(f"    ⚠️ 找不到 table div")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            # 找到 contents div
            contents_div = table_div.find('div', class_='contents')
            
            if not contents_div:
                print(f"    ⚠️ 找不到 contents div")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            print(f"    ✅ 找到 contents div")
            
            # 找到所有 dl 元素
            all_dl_list = contents_div.find_all('dl')
            
            # 過濾掉標題列 (class="con-title")
            data_dl_list = []
            for dl in all_dl_list:
                dl_classes = dl.get('class', [])
                if 'con-title' not in dl_classes:
                    data_dl_list.append(dl)
            
            print(f"    📋 找到 {len(data_dl_list)} 個資料列")
            
            if len(data_dl_list) == 0:
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            processed_count = 0
            
            # 遍歷每個 dl
            for idx, dl in enumerate(data_dl_list, 1):
                try:
                    dt_list = dl.find_all('dt')
                    dd = dl.find('dd')
                    
                    if len(dt_list) < 2 or not dd:
                        print(f"    ⚠️ 第 {idx} 列結構不完整")
                        continue
                    
                    processed_count += 1
                    
                    # 提取資料
                    number = dt_list[0].get_text(strip=True)
                    date = dt_list[1].get_text(strip=True)
                    unit = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else '台灣航港局'
                    
                    link_tag = dd.find('a')
                    if link_tag:
                        title = link_tag.get_text(strip=True)
                        link = link_tag.get('href', '')
                        
                        # 修正相對路徑
                        if link and not link.startswith('http'):
                            if link.startswith('/'):
                                link = f"https://www.motcmpb.gov.tw{link}"
                            else:
                                link = f"https://www.motcmpb.gov.tw/{link}"
                    else:
                        title = dd.get_text(strip=True)
                        link = ''
                    
                    print(f"    [{idx}] {number} | {date} | {title[:40]}...")
                    
                    # 檢查日期範圍
                    if not self.is_within_date_range(date):
                        continue
                    
                    # 檢查關鍵字
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
                    
                    # 2. 從連結頁面提取
                    if link:
                        try:
                            print(f"          🌐 正在訪問詳細頁面...")
                            
                            # 開新分頁訪問詳細頁面
                            self.driver.execute_script("window.open('');")
                            self.driver.switch_to.window(self.driver.window_handles[1])
                            
                            self.driver.get(link)
                            time.sleep(2)
                            
                            detail_soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                            
                            # 嘗試多種方式找到內容區域
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
                            
                            # 關閉分頁，返回列表頁
                            self.driver.close()
                            self.driver.switch_to.window(self.driver.window_handles[0])
                            time.sleep(1)
                            
                        except Exception as e:
                            print(f"          ⚠️ 無法從網頁提取座標: {e}")
                            # 確保返回列表頁
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
            # 關閉 WebDriver
            try:
                self.driver.quit()
                print("  🔒 WebDriver 已關閉 (台灣航港局)")
            except:
                pass
        
        print(f"\n🇹🇼 台灣航港局爬取完成，新增 {len(self.new_warnings)} 筆警告")
        return self.new_warnings


# ==================== 6. 中國海事局爬蟲 (含座標提取) ====================
class CNMSANavigationWarningsScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, coord_extractor, headless=True):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        self.coord_extractor = coord_extractor
        
        print("🇨🇳 初始化中國海事局爬蟲...")
        
        # WebDriver 設定
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
        """抓取單一海事局警告 (含座標提取)"""
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
                            if p_date and p_date < self.three_days_ago:
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
                        
                        # 從連結頁面提取
                        if link and not link.startswith('javascript'):
                            try:
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", item)
                                time.sleep(0.5)
                                self.driver.execute_script("arguments[0].click();", item)
                                time.sleep(2)
                                
                                try:
                                    page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                                    page_coords = self.coord_extractor.extract_coordinates(page_text)
                                    
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


# ==================== 7. 統一監控系統 ====================
class UnifiedMaritimeWarningSystem:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', 
                 mail_user=None, mail_pass=None, target_email=None):
        print("🚀 初始化統一海事警告監控系統...")
        
        # 初始化核心組件
        self.keyword_manager = KeywordManager()
        self.db_manager = DatabaseManager()
        self.teams_notifier = UnifiedTeamsNotifier(webhook_url) if webhook_url else None
        self.email_notifier = GmailRelayNotifier(mail_user, mail_pass, target_email)
        self.coord_extractor = CoordinateExtractor()
        self.map_plotter = MaritimeMapPlotter() if MAPPING_AVAILABLE else None
        
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        
        # 初始化各爬蟲
        self.cn_scraper = CNMSANavigationWarningsScraper(
            self.db_manager, self.keyword_manager, self.teams_notifier, self.coord_extractor
        )
        self.tw_scraper = TWMaritimePortBureauScraper(
            self.db_manager, self.keyword_manager, self.teams_notifier, self.coord_extractor
        )
        
        self.all_new_warnings = []
        self.all_captured_data = []
        
        print("✅ 統一監控系統初始化完成\n")
    
    def run_all_scrapers(self):
        """執行所有爬蟲"""
        start_time = datetime.now()
        map_path = None
        
        print(f"{'='*60}")
        print(f"🌊 開始執行多源海事警告監控")
        print(f"{'='*60}")
        
        try:
            # 1. 執行中國海事局爬蟲
            print("\n" + "="*60)
            cn_warnings = self.cn_scraper.scrape_all_bureaus()
            self.all_new_warnings.extend(cn_warnings)
            self.all_captured_data.extend(self.cn_scraper.captured_warnings_data)
            
            # 2. 執行台灣航港局爬蟲
            print("\n" + "="*60)
            tw_warnings = self.tw_scraper.scrape_all_pages()
            self.all_new_warnings.extend(tw_warnings)
            self.all_captured_data.extend(self.tw_scraper.captured_warnings_data)
            
            # 3. 繪製地圖
            if self.all_captured_data and self.map_plotter:
                print("\n" + "="*60)
                print("🗺️ 正在繪製海圖...")
                warnings_for_map = [
                    {
                        'title': w['title'],
                        'coordinates': w.get('coordinates', []),
                        'bureau': w['bureau'],
                        'source': w.get('source', 'CN_MSA')
                    }
                    for w in self.all_captured_data
                    if w.get('coordinates')
                ]
                
                if warnings_for_map:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    map_filename = f"maritime_warnings_{timestamp}.png"
                    map_path = self.map_plotter.plot_warnings(warnings_for_map, map_filename)
            
            # 4. 發送通知
            if self.enable_teams and self.all_captured_data:
                self.send_notifications()
            
            # 5. 生成報告
            duration = (datetime.now() - start_time).total_seconds()
            self.generate_final_report(duration, map_path)
            
        except Exception as e:
            print(f"❌ 執行過程發生錯誤: {e}")
            traceback.print_exc()
    
    def send_notifications(self):
        """發送通知"""
        if self.send_mode == 'batch':
            cn_warnings = [w for w in self.all_captured_data if w.get('source') == 'CN_MSA']
            tw_warnings = [w for w in self.all_captured_data if w.get('source') == 'TW_MPB']
            
            if cn_warnings:
                cn_data = []
                for w in cn_warnings:
                    cn_data.append((
                        w['id'], w['bureau'], w['title'], w['link'], 
                        w['time'], ', '.join(w['keywords']), 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        w.get('coordinates', [])
                    ))
                
                if self.teams_notifier.send_batch_notification(cn_data, "CN_MSA"):
                    for w in cn_warnings:
                        self.db_manager.mark_as_notified(w['id'])
            
            if tw_warnings:
                tw_data = []
                for w in tw_warnings:
                    tw_data.append((
                        w['id'], w['bureau'], w['title'], w['link'], 
                        w['time'], ', '.join(w['keywords']), 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        w.get('coordinates', [])
                    ))
                
                if self.teams_notifier.send_batch_notification(tw_data, "TW_MPB"):
                    for w in tw_warnings:
                        self.db_manager.mark_as_notified(w['id'])
    
    def generate_final_report(self, duration, map_path=None):
        """生成最終報告"""
        print(f"\n{'='*60}")
        print(f"📊 執行結果摘要")
        print(f"{'='*60}")
        print(f"⏱️ 總耗時: {duration:.2f} 秒")
        
        cn_count = len([w for w in self.all_captured_data if w.get('source') == 'CN_MSA'])
        tw_count = len([w for w in self.all_captured_data if w.get('source') == 'TW_MPB'])
        total_coords = sum(len(w.get('coordinates', [])) for w in self.all_captured_data)
        
        print(f"🇨🇳 中國海事局新警告: {cn_count} 筆")
        print(f"🇹🇼 台灣航港局新警告: {tw_count} 筆")
        print(f"📈 總計新警告: {len(self.all_captured_data)} 筆")
        print(f"📍 提取座標點: {total_coords} 個")
        if map_path:
            print(f"🗺️ 地圖: {map_path}")
        print(f"{'='*60}")
        
        # 生成報告
        json_data, html_data = self._generate_unified_report(duration)
        self.email_notifier.send_trigger_email(json_data, html_data, map_path)
        
        if self.all_captured_data:
            self.db_manager.export_to_excel()
            print("✅ 報告生成完成")
        else:
            print("ℹ️ 本次無新警告")
    
    def _generate_unified_report(self, duration):
        """生成統一報告"""
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', sans-serif;"
        count = len(self.all_captured_data)
        status_color = "#2E7D32" if count == 0 else "#D9534F"
        
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')
        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')
        
        cn_count = len([w for w in self.all_captured_data if w.get('source') == 'CN_MSA'])
        tw_count = len([w for w in self.all_captured_data if w.get('source') == 'TW_MPB'])
        total_coords = sum(len(w.get('coordinates', [])) for w in self.all_captured_data)
        
        html = f"""
        <html><body style="{font_style} color:#333; line-height:1.5;">
            <div style="background:#003366; color:white; padding:20px; border-radius:6px 6px 0 0;">
                <h2 style="margin: 0; font-size: 25px; font-weight: 700;"> 
                🌊 航行警告監控系統(CN & TW) 
                </h2>
                <div style="margin-top: 8px; font-size: 12px; color: #a3cbe8;">
                📅 Last Update: {now_str_LT} (TPE) | {now_str_UTC} (UTC)
                </div>
            </div>
            <div style="background:#f8f9fa; border:1px solid #ddd; padding:15px; margin-bottom:20px;">
                <strong style="color:{status_color};">📊 監控報告摘要</strong><br>
                🇨🇳 中國海事局: {cn_count} 個新警告<br>
                🇹🇼 台灣航港局: {tw_count} 個新警告<br>
                <strong>總計: {count} 個新警告</strong><br>
                📍 提取座標點: {total_coords} 個
            </div>
        """
        
        if count > 0:
            html += f"""<table style="width:100%; border-collapse:collapse; font-size:14px; border:1px solid #ddd;">
                <tr style="background:#f0f4f8; text-align:left;">
                    <th style="padding:10px; border-bottom:2px solid #ccc;">來源</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈單位</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">警告標題</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈時間</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">座標</th>
                </tr>"""
            
            for i, item in enumerate(self.all_captured_data):
                bg = "#fff" if i % 2 == 0 else "#f9f9f9"
                source_flag = "🇨🇳" if item.get('source') == 'CN_MSA' else "🇹🇼"
                source_name = "中國海事局" if item.get('source') == 'CN_MSA' else "台灣航港局"
                
                kw_html = "".join([
                    f"<span style='background:#fff3cd; padding:2px 5px; margin-right:5px; border-radius:3px; font-size:12px;'>{k}</span>" 
                    for k in item['keywords']
                ])
                # 座標顯示
                coords = item.get('coordinates', [])
                coord_html = "無座標"
                if coords:
                    coord_html = "<br>".join([f"({c[0]:.4f}°, {c[1]:.4f}°)" for c in coords[:3]])
                    if len(coords) > 3:
                        coord_html += f"<br><small style='color:#666;'>...還有 {len(coords)-3} 個</small>"
                
                html += f"""<tr style="background:{bg};">
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{source_flag} {source_name}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{item['bureau']}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee;">
                        <a href="{item['link']}" style="color:#0056b3; text-decoration:none; font-weight:bold;">{item['title']}</a><br>
                        <div style="margin-top:5px;">{kw_html}</div>
                    </td>
                    <td style="padding:10px; border-bottom:1px solid #eee; color:#666;">{item['time']}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee; font-size:12px; color:#666;">{coord_html}</td>
                </tr>"""
            html += "</table>"
        else:
            html += "<p style='text-align:center; color:#666; padding:20px;'>本次執行未發現新的航行警告</p>"
        
        html += f"""
            <div style="margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 20px; font-size: 15px; color: #9ca3af; text-align: center; {font_style}">
                <p style="margin: 0;">Wan Hai Lines Ltd. | Marine Technology Division</p>
                <p style="margin: 0;color: blue;">Present by Fleet Risk Department</p>
                <p style="margin: 0;">Multi-Source Maritime Warning System | Automated Monitoring with Coordinate Extraction</p>
            </div>
        </body>
        </html>
        """
        
        json_data = {
            "execution_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "duration": round(duration, 2),
            "total_warnings": count,
            "cn_msa_warnings": cn_count,
            "tw_mpb_warnings": tw_count,
            "total_coordinates": total_coords,
            "new_warnings": self.all_captured_data
        }
        
        return json_data, html


# ==================== 8. 主程式進入點 ====================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🌊 統一海事警告監控系統 (CN MSA + TW MPB)")
    print("   支援經緯度提取、地圖繪製、Teams 通知、Email 報告")
    print("="*60 + "\n")
    
    # 從環境變數讀取設定
    TEAMS_WEBHOOK = os.getenv('TEAMS_WEBHOOK_URL')
    MAIL_USER = os.getenv('MAIL_USER')
    MAIL_PASS = os.getenv('MAIL_PASSWORD')
    TARGET_EMAIL = os.getenv('TARGET_EMAIL')
    
    # 檢查設定
    config_status = []
    if TEAMS_WEBHOOK:
        config_status.append("✅ Teams Webhook")
    else:
        config_status.append("⚠️ Teams Webhook 未設定")
    
    if MAIL_USER and MAIL_PASS:
        config_status.append("✅ Email 帳號")
    else:
        config_status.append("⚠️ Email 帳號未設定")
    
    if TARGET_EMAIL:
        config_status.append("✅ 收件人")
    else:
        config_status.append("⚠️ 收件人未設定")
    
    print("📋 設定檢查:")
    for status in config_status:
        print(f"   {status}")
    print()
    
    # 初始化統一監控系統
    try:
        system = UnifiedMaritimeWarningSystem(
            webhook_url=TEAMS_WEBHOOK,
            enable_teams=bool(TEAMS_WEBHOOK),
            send_mode='batch',
            mail_user=MAIL_USER,
            mail_pass=MAIL_PASS,
            target_email=TARGET_EMAIL
        )
        
        # 執行監控
        system.run_all_scrapers()
        
        print("\n" + "="*60)
        print("🎉 系統執行完成！")
        print("="*60)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者中斷執行")
    except Exception as e:
        print(f"\n\n❌ 系統執行失敗: {e}")
        traceback.print_exc()
        
        # 嘗試發送錯誤通知
        try:
            error_notifier = GmailRelayNotifier(MAIL_USER, MAIL_PASS, TARGET_EMAIL)
            error_notifier.send_error_notification(str(e), traceback.format_exc())
        except:
            pass
    
    print("\n🚀 祝您有美好的一天！\n")
      

