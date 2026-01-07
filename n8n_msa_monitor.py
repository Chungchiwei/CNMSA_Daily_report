#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MSA 航行警告監控系統 - SQLite 版本（含座標提取）
版本: 2.0
更新日期: 2026-01-07
功能: 中國海事局 + 台灣航港局雙源監控
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from database_manager import DatabaseManager
from keyword_manager import KeywordManager

# ==================== 套件檢查與載入 ====================
try:
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    BS4_AVAILABLE = True
    print("✅ BeautifulSoup4 載入成功")
except ImportError:
    BS4_AVAILABLE = False
    print("⚠️ BeautifulSoup4 未安裝，台灣航港局功能將被停用")
    print("   請執行: pip install beautifulsoup4")
    
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    from matplotlib.patches import Polygon
    MAPPING_AVAILABLE = True
    print("✅ 地圖繪製模組載入成功")
except ImportError as e:
    MAPPING_AVAILABLE = False
    print(f"⚠️ 地圖繪製模組未安裝: {e}")

# ==================== 環境設定 ====================
os.environ['WDM_SSL_VERIFY'] = '0'
os.environ['WDM_LOG_LEVEL'] = '0'
load_dotenv()

warnings.filterwarnings('ignore')
logging.getLogger('selenium').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('WDM').setLevel(logging.ERROR)

# Windows 錯誤訊息過濾
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


# ==================== 經緯度提取器 (增強版) ====================
class CoordinateExtractor:
    """提取文本中的經緯度座標（支援多種格式）"""
    
    def __init__(self):
        # 標準格式正則表達式
        self.patterns = [
            # 格式1: 18-17.37N 109-22.17E (度-分.小數)
            r'(\d{1,3})-(\d{1,2}\.?\d*)\s*([NSns北南])\s+(\d{1,3})-(\d{1,2}\.?\d*)\s*([EWew東西])',
            
            # 格式2: 25°30'N 121°20'E
            r'(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([NSns北南])\s+(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([EWew東西])',
            
            # 格式3: 25°30.5'N, 121°20.8'E (帶逗號)
            r'(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([NSns北南])\s*[,，]\s*(\d{1,3})[°度]\s*(\d{1,2}\.?\d*)[\'′分]?\s*([EWew東西])',
        ]
        
        # 台灣航港局中文格式
        self.tw_pattern = r'([北南]緯)\s*(\d{1,3})\s*[度\s]\s*(\d{1,2}(?:\.\d+)?)\s*[分\s]?.*?([東西]經)\s*(\d{1,3})\s*[度\s]\s*(\d{1,2}(?:\.\d+)?)\s*[分\s]?'

    def extract_coordinates(self, text):
        """從文本中提取所有經緯度座標"""
        if not text:
            return []
        
        coordinates = []
        
        # 預處理文本
        clean_text = text.replace('、', ' ').replace('，', ' ').replace('。', ' ')
        clean_text = re.sub(r'\s+', ' ', clean_text)
        
        # 1. 匹配台灣中文格式
        tw_matches = re.finditer(self.tw_pattern, clean_text)
        for match in tw_matches:
            try:
                coord = self._parse_tw_match(match)
                if coord and self._validate_coordinate(coord):
                    coordinates.append(coord)
            except Exception as e:
                continue

        # 2. 匹配標準格式
        for pattern in self.patterns:
            matches = re.finditer(pattern, clean_text, re.IGNORECASE)
            for match in matches:
                try:
                    coord = self._parse_match(match)
                    if coord and self._validate_coordinate(coord):
                        coordinates.append(coord)
                except Exception as e:
                    continue
        
        # 去重（保留精度到小數點後3位）
        unique_coords = []
        for coord in coordinates:
            is_duplicate = False
            for existing in unique_coords:
                if abs(coord[0] - existing[0]) < 0.001 and abs(coord[1] - existing[1]) < 0.001:
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_coords.append(coord)
        
        return unique_coords
    
    def _parse_tw_match(self, match):
        """解析台灣中文格式"""
        try:
            groups = match.groups()
            if len(groups) < 6:
                return None
            
            lat_dir = groups[0]  # '北緯' or '南緯'
            lat_deg = float(groups[1])
            lat_min = float(groups[2])
            
            lon_dir = groups[3]  # '東經' or '西經'
            lon_deg = float(groups[4])
            lon_min = float(groups[5])
            
            lat = lat_deg + lat_min / 60
            lon = lon_deg + lon_min / 60
            
            if '南' in lat_dir:
                lat = -lat
            if '西' in lon_dir:
                lon = -lon
                
            return (lat, lon)
        except:
            return None

    def _parse_match(self, match):
        """解析標準格式"""
        try:
            groups = match.groups()
            if len(groups) < 6:
                return None
            
            lat_deg = float(groups[0])
            lat_min = float(groups[1]) if groups[1] else 0
            lat_dir = groups[2].upper()
            
            lon_deg = float(groups[3])
            lon_min = float(groups[4]) if groups[4] else 0
            lon_dir = groups[5].upper()
            
            lat = lat_deg + lat_min / 60
            lon = lon_deg + lon_min / 60
            
            if lat_dir in ['S', 's', '南']:
                lat = -lat
            if lon_dir in ['W', 'w', '西']:
                lon = -lon
            
            return (lat, lon)
        except:
            return None
    
    def _validate_coordinate(self, coord):
        """驗證座標合理性"""
        if not coord or len(coord) != 2:
            return False
        
        lat, lon = coord
        
        # 基本範圍檢查
        if not (-90 <= lat <= 90):
            return False
        if not (-180 <= lon <= 180):
            return False
        
        # 排除明顯錯誤的座標（如 0,0）
        if abs(lat) < 0.01 and abs(lon) < 0.01:
            return False
        
        return True


# ==================== 台灣航港局爬蟲 (優化版) ====================
class TWMaritimeNoticesCrawler:
    """台灣交通部航港局航行警告爬蟲"""
    
    def __init__(self, days=3):
        if not BS4_AVAILABLE:
            raise ImportError("BeautifulSoup4 未安裝")
        
        self.base_url = "https://www.motcmpb.gov.tw/Information/Notice"
        self.params = {
            'SiteId': '1',
            'NodeId': '483'
        }
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.motcmpb.gov.tw/'
        }
        self.days = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def parse_date(self, date_string):
        """解析日期字串（支援民國年和西元年）"""
        if not date_string:
            return None
        
        try:
            date_string = date_string.strip()
            
            # 民國年格式: 113/12/31, 113-12-31, 113年12月31日
            roc_patterns = [
                r'(\d{2,3})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{1,2})',
                r'(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?'
            ]
            
            for pattern in roc_patterns:
                match = re.search(pattern, date_string)
                if match:
                    year = int(match.group(1)) + 1911
                    month = int(match.group(2))
                    day = int(match.group(3))
                    return datetime(year, month, day)
            
            # 西元年格式
            date_formats = [
                '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',
                '%Y年%m月%d日', '%Y-%m-%d %H:%M:%S'
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
            
            return None
        except Exception as e:
            return None
    
    def is_within_date_range(self, date_string):
        """檢查日期是否在範圍內"""
        if not date_string:
            return True
        
        parsed_date = self.parse_date(date_string)
        if parsed_date:
            return parsed_date >= self.cutoff_date
        return True
    
    def get_notices(self, page=1):
        """爬取指定頁面的航行警告（帶重試機制）"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                params = self.params.copy()
                if page > 1:
                    params['page'] = page
                
                print(f"  📄 正在請求第 {page} 頁 (嘗試 {attempt + 1}/{max_retries})...")
                
                response = self.session.get(
                    self.base_url,
                    params=params,
                    timeout=30,
                    verify=False
                )
                response.raise_for_status()
                response.encoding = 'utf-8'
                
                soup = BeautifulSoup(response.text, 'html.parser')
                notices = []
                
                # 尋找內容區塊
                contents_div = soup.find('div', class_='contents')
                if not contents_div:
                    contents_div = soup.find('div', id='container')
                
                if not contents_div:
                    print("    ⚠️ 找不到內容區塊")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    return []
                
                # 解析資料列
                dl_list = contents_div.find_all('dl')
                
                if len(dl_list) <= 1:
                    print("    ⚠️ 沒有找到資料列")
                    return []
                
                # 跳過標題列
                for dl in dl_list[1:]:
                    try:
                        dt_list = dl.find_all('dt')
                        dd = dl.find('dd')
                        
                        if len(dt_list) < 2 or not dd:
                            continue
                        
                        number = dt_list[0].get_text(strip=True)
                        date = dt_list[1].get_text(strip=True)
                        unit = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else '航港局'
                        
                        # 日期過濾
                        if not self.is_within_date_range(date):
                            continue
                        
                        # 提取標題和連結
                        link_tag = dd.find('a')
                        if link_tag:
                            title = link_tag.get_text(strip=True)
                            link = link_tag.get('href', '')
                            
                            if link and not link.startswith('http'):
                                link = f"https://www.motcmpb.gov.tw{link}"
                        else:
                            title = dd.get_text(strip=True)
                            link = ''
                        
                        notice = {
                            'number': number,
                            'date': date,
                            'title': title,
                            'unit': unit,
                            'link': link
                        }
                        
                        notices.append(notice)
                        print(f"    ✅ 找到: {number} - {title[:30]}...")
                        
                    except Exception as e:
                        continue
                
                return notices
                
            except requests.exceptions.RequestException as e:
                print(f"    ⚠️ 請求失敗 (嘗試 {attempt + 1}/{max_retries}): {str(e)[:100]}")
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                return []
            except Exception as e:
                print(f"    ❌ 解析失敗: {str(e)[:100]}")
                return []
        
        return []
    
    def crawl_recent_notices(self, max_pages=5):
        """爬取最近N天的航行警告"""
        all_notices = []
        should_continue = True
        
        for page in range(1, max_pages + 1):
            if not should_continue:
                break
            
            notices = self.get_notices(page)
            
            if not notices:
                print(f"    ⚠️ 第 {page} 頁沒有資料，停止爬取")
                break
            
            # 檢查最舊日期
            dates = [self.parse_date(n.get('date', '')) for n in notices]
            valid_dates = [d for d in dates if d is not None]
            
            if valid_dates:
                oldest_date = min(valid_dates)
                if oldest_date < self.cutoff_date:
                    print(f"    ⚠️ 已到達日期範圍外 ({oldest_date.strftime('%Y-%m-%d')})")
                    should_continue = False
            
            all_notices.extend(notices)
            
            if page < max_pages and should_continue:
                time.sleep(2)
        
        return all_notices


# ==================== 海圖繪製器 (優化版) ====================
class MaritimeMapPlotter:
    """繪製海事警告區域地圖"""
    
    def __init__(self):
        self.output_dir = "maps"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        self.chinese_font = self._setup_chinese_font()
    
    def _setup_chinese_font(self):
        """設定中文字型（增強版）"""
        try:
            import matplotlib.font_manager as fm
            
            # 字型路徑清單（依優先順序）
            font_paths = [
                # Windows
                'C:/Windows/Fonts/msyh.ttc',      # 微軟正黑體
                'C:/Windows/Fonts/msjh.ttc',      # 微軟正黑體
                'C:/Windows/Fonts/kaiu.ttf',      # 標楷體
                'C:/Windows/Fonts/mingliu.ttc',   # 細明體
                # Linux
                '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
                '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
                '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                '/usr/share/fonts/truetype/arphic/uming.ttc',
                # macOS
                '/System/Library/Fonts/PingFang.ttc',
                '/Library/Fonts/Microsoft/Microsoft YaHei.ttf',
                '/System/Library/Fonts/STHeiti Light.ttc',
            ]
            
            for font_path in font_paths:
                if os.path.exists(font_path):
                    try:
                        font_prop = fm.FontProperties(fname=font_path)
                        # 測試字型是否可用
                        test_fig = plt.figure()
                        test_fig.text(0.5, 0.5, '測試', fontproperties=font_prop)
                        plt.close(test_fig)
                        
                        print(f"  ✅ 使用字型: {os.path.basename(font_path)}")
                        return font_prop
                    except Exception as e:
                        continue
            
            # 嘗試使用系統字型
            print("  ⚠️ 嘗試使用系統預設中文字型...")
            for font_name in ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'Noto Sans CJK']:
                try:
                    font_prop = fm.FontProperties(family=font_name)
                    print(f"  ✅ 使用系統字型: {font_name}")
                    return font_prop
                except:
                    continue
            
            print("  ⚠️ 未找到中文字型，地圖標籤可能顯示為方塊")
            return None
            
        except Exception as e:
            print(f"  ⚠️ 字型設定失敗: {e}")
            return None
    
    def plot_warnings(self, warnings_data, output_filename="maritime_warnings.png"):
        """繪製警告座標地圖"""
        if not MAPPING_AVAILABLE:
            print("❌ 地圖繪製功能不可用")
            return None
        
        if not warnings_data:
            return None
        
        try:
            # 收集所有座標
            all_coords = []
            for warning in warnings_data:
                coords = warning.get('coordinates', [])
                if coords:
                    all_coords.extend(coords)
            
            if not all_coords:
                print("  ⚠️ 無有效座標可繪製")
                return None
            
            lats = [c[0] for c in all_coords]
            lons = [c[1] for c in all_coords]
            
            # 計算地圖範圍（增加10%邊距）
            lat_range = max(lats) - min(lats)
            lon_range = max(lons) - min(lons)
            
            lat_margin = max(lat_range * 0.1, 1)
            lon_margin = max(lon_range * 0.1, 1)
            
            lat_min = min(lats) - lat_margin
            lat_max = max(lats) + lat_margin
            lon_min = min(lons) - lon_margin
            lon_max = max(lons) + lon_margin
            
            # 建立地圖
            fig = plt.figure(figsize=(18, 14), dpi=150)
            ax = plt.axes(projection=ccrs.PlateCarree())
            
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
            
            # 添加地圖特徵
            ax.add_feature(cfeature.LAND, facecolor='#f5f5dc', edgecolor='#8b7355', linewidth=1)
            ax.add_feature(cfeature.OCEAN, facecolor='#e0f2ff')
            ax.add_feature(cfeature.COASTLINE, linewidth=1.5, edgecolor='#2c5f7a')
            ax.add_feature(cfeature.BORDERS, linestyle=':', linewidth=1, edgecolor='#666666')
            
            # 添加網格
            gl = ax.gridlines(draw_labels=True, linewidth=0.8, color='gray', 
                             alpha=0.5, linestyle='--')
            gl.top_labels = False
            gl.right_labels = False
            gl.xlabel_style = {'size': 10}
            gl.ylabel_style = {'size': 10}
            
            # 顏色列表
            colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', 
                     '#9b59b6', '#e67e22', '#1abc9c', '#34495e',
                     '#c0392b', '#2980b9', '#27ae60', '#d35400']
            
            # 繪製警告區域
            plotted_bureaus = set()
            
            for idx, warning in enumerate(warnings_data):
                coords = warning.get('coordinates', [])
                if not coords:
                    continue
                
                color = colors[idx % len(colors)]
                bureau = warning.get('bureau', 'Unknown')
                
                # 繪製座標點
                for coord_idx, (lat, lon) in enumerate(coords):
                    label = bureau if bureau not in plotted_bureaus else ""
                    if label:
                        plotted_bureaus.add(bureau)
                    
                    ax.plot(lon, lat, marker='o', color=color, markersize=14, 
                           markeredgecolor='white', markeredgewidth=2.5,
                           transform=ccrs.PlateCarree(), label=label, zorder=5)
                    
                    # 座標標籤
                    label_text = f"{lat:.3f}°N\n{lon:.3f}°E"
                    text_props = dict(
                        fontsize=9,
                        transform=ccrs.PlateCarree(),
                        bbox=dict(boxstyle='round,pad=0.4', facecolor='white', 
                                 edgecolor=color, linewidth=1.5, alpha=0.9),
                        zorder=6
                    )
                    
                    if self.chinese_font:
                        text_props['fontproperties'] = self.chinese_font
                    
                    ax.text(lon + 0.15, lat + 0.15, label_text, **text_props)
                
                # 連線與填充
                if len(coords) > 1:
                    lons_line = [c[1] for c in coords] + [coords[0][1]]
                    lats_line = [c[0] for c in coords] + [coords[0][0]]
                    
                    ax.plot(lons_line, lats_line, color=color, linewidth=3, 
                           linestyle='--', alpha=0.8, transform=ccrs.PlateCarree(), zorder=4)
                    
                    ax.fill(lons_line, lats_line, color=color, alpha=0.2, 
                           transform=ccrs.PlateCarree(), zorder=3)
            
            # 標題
            title_text = f"海事航行警告區域圖\nMaritime Navigation Warnings Map\n({len(warnings_data)} 個警告, {len(all_coords)} 個座標點)"
            title_props = {'fontsize': 20, 'fontweight': 'bold', 'pad': 30}
            
            if self.chinese_font:
                title_props['fontproperties'] = self.chinese_font
            
            plt.title(title_text, **title_props)
            
            # 圖例
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                by_label = dict(zip(labels, handles))
                legend_props = {
                    'loc': 'upper right',
                    'fontsize': 12,
                    'framealpha': 0.95,
                    'edgecolor': 'black',
                    'fancybox': True,
                    'shadow': True
                }
                
                if self.chinese_font:
                    legend_props['prop'] = self.chinese_font
                
                legend = ax.legend(by_label.values(), by_label.keys(), **legend_props)
                legend.set_zorder(10)
            
            # 資訊文字
            info_text = (
                f"製圖時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"資料來源: 中國海事局 (MSA) + 台灣航港局 (MOTC)\n"
                f"座標系統: WGS84"
            )
            
            text_props = {
                'transform': ax.transAxes,
                'fontsize': 10,
                'verticalalignment': 'bottom',
                'bbox': dict(boxstyle='round', facecolor='white', alpha=0.9, edgecolor='gray')
            }
            
            if self.chinese_font:
                text_props['fontproperties'] = self.chinese_font
            
            plt.text(0.02, 0.02, info_text, **text_props)
            
            # 儲存
            output_path = os.path.join(self.output_dir, output_filename)
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor='white', edgecolor='none')
            plt.close()
            
            print(f"✅ 地圖已儲存: {output_path}")
            return output_path
        
        except Exception as e:
            print(f"❌ 地圖繪製失敗: {e}")
            traceback.print_exc()
            return None


# ==================== Teams 通知類別 ====================
class TeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    def _fix_url(self, url):
        """修正 URL 格式"""
        if not url: 
            return "#"
        
        url = url.strip()
        
        if url.startswith(('http://', 'https://')): 
            return url
        
        if url.startswith(('javascript:', '#')): 
            return "#"
        
        if url.startswith('/'):
            if 'motcmpb' in url or '/Information' in url:
                return f"https://www.motcmpb.gov.tw{url}"
            else:
                return f"https://www.msa.gov.cn{url}"
        
        return url
    
    def _create_adaptive_card(self, title, body_elements, actions=None):
        """建立 Adaptive Card"""
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

    def send_warning_notification(self, warning_data):
        """發送單個警告通知"""
        if not self.webhook_url: 
            return False
        
        try:
            warning_id, bureau, title, link, pub_time, keywords, scrape_time, coordinates = warning_data
            fixed_link = self._fix_url(link)
            
            coord_text = "無座標資訊"
            if coordinates:
                try:
                    coord_list = json.loads(coordinates) if isinstance(coordinates, str) else coordinates
                    if coord_list:
                        coord_text = "\n".join([f"• ({c[0]:.4f}°, {c[1]:.4f}°)" for c in coord_list[:5]])
                        if len(coord_list) > 5:
                            coord_text += f"\n• ...還有 {len(coord_list)-5} 個座標"
                except:
                    coord_text = "座標格式錯誤"
            
            body = [
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "🏢 發布單位:", "value": bureau},
                        {"title": "📋 標題:", "value": title},
                        {"title": "📅 發布時間:", "value": pub_time},
                        {"title": "🔍 關鍵字:", "value": keywords},
                        {"title": "📍 座標:", "value": coord_text}
                    ]
                },
                {
                    "type": "TextBlock",
                    "text": f"🔗 {fixed_link}",
                    "wrap": True,
                    "size": "Small"
                }
            ]
            
            actions = [
                {
                    "type": "Action.OpenUrl",
                    "title": "🌐 開啟公告",
                    "url": fixed_link
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
                }
            ]
            
            actions = []
            
            for idx, w in enumerate(warnings_list[:8], 1):
                _, bureau, title, link, pub_time, _, _, coordinates = w
                fixed_link = self._fix_url(link)
                
                coord_summary = "無座標"
                if coordinates:
                    try:
                        coord_list = json.loads(coordinates) if isinstance(coordinates, str) else coordinates
                        if coord_list:
                            coord_summary = f"{len(coord_list)} 個座標點"
                    except:
                        pass
                
                body_elements.extend([
                    {
                        "type": "TextBlock",
                        "text": f"**{idx}. {bureau}**",
                        "weight": "Bolder",
                        "spacing": "Medium"
                    },
                    {
                        "type": "TextBlock",
                        "text": title[:100],
                        "wrap": True
                    },
                    {
                        "type": "TextBlock",
                        "text": f"📅 {pub_time} | 📍 {coord_summary}",
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
                return False
                
        except Exception as e:
            print(f"❌ Teams 批量發送失敗: {e}")
            traceback.print_exc()
            return False


# ==================== Gmail 發信類別 ====================
class GmailRelayNotifier:
    def __init__(self, user, password, target_email):
        self.user = user
        self.password = password
        self.target = target_email

    def send_trigger_email(self, report_data: dict, report_html: str, map_path: str = None) -> bool:
        if not self.user or not self.password or not self.target: 
            print("⚠️ Email 設定不完整")
            return False
        
        msg = MIMEMultipart('related')
        msg['From'] = self.user
        msg['To'] = self.target
        msg['Subject'] = "GITHUB_TRIGGER_CN_MSA_REPORT"
        
        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)
        
        text_part = MIMEText(json.dumps(report_data, ensure_ascii=False, indent=2), 'plain', 'utf-8')
        msg_alternative.attach(text_part)
        
        html_part = MIMEText(report_html, 'html', 'utf-8')
        msg_alternative.attach(html_part)
        
        if map_path and os.path.exists(map_path):
            try:
                with open(map_path, 'rb') as f:
                    img_data = f.read()
                    img = MIMEImage(img_data)
                    img.add_header('Content-ID', '<map_image>')
                    img.add_header('Content-Disposition', 'inline', filename='maritime_warnings_map.png')
                    msg.attach(img)
                print("  ✅ 地圖已嵌入 Email")
            except Exception as e:
                print(f"  ⚠️ 無法嵌入地圖: {e}")

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


# ==================== 主爬蟲類別 ====================
class MSANavigationWarningsScraper:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', headless=True, 
                 mail_user=None, mail_pass=None, target_email=None, enable_tw=True):
        print("🚀 初始化海事局爬蟲...")
        
        self.keyword_manager = KeywordManager()
        self.keywords = self.keyword_manager.get_keywords()
        print(f"📋 載入 {len(self.keywords)} 個監控關鍵字")
        
        self.db_manager = DatabaseManager()
        self.coord_extractor = CoordinateExtractor()
        self.map_plotter = MaritimeMapPlotter() if MAPPING_AVAILABLE else None
        
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        self.teams_notifier = TeamsNotifier(webhook_url) if self.enable_teams else None
        self.email_notifier = GmailRelayNotifier(mail_user, mail_pass, target_email)
        
        self.enable_tw = enable_tw and BS4_AVAILABLE
        if self.enable_tw:
            try:
                self.tw_crawler = TWMaritimeNoticesCrawler(days=3)
                print("✅ 台灣航港局爬蟲已啟用")
            except ImportError:
                print("⚠️ 台灣航港局爬蟲啟用失敗：缺少 BeautifulSoup4")
                self.enable_tw = False
        
        if self.enable_teams:
            print(f"✅ Teams 通知已啟用 (模式: {send_mode})")
        else:
            print("⚠️ Teams 通知未啟用")
        
        print("🌐 正在啟動 Chrome WebDriver...")
        
        options = webdriver.ChromeOptions()
        
        if headless:
            options.add_argument('--headless=new')
        
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        prefs = {
            'profile.managed_default_content_settings.images': 2,
            'profile.default_content_setting_values.notifications': 2,
        }
        options.add_experimental_option('prefs', prefs)
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        service = Service(ChromeDriverManager().install())
        if platform.system() == 'Windows':
            service.creation_flags = subprocess.CREATE_NO_WINDOW
        
        try:
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(120)
            self.wait = WebDriverWait(self.driver, 20)
            print("  ✅ WebDriver 啟動成功")
        except Exception as e:
            print(f"❌ WebDriver 初始化失敗: {e}")
            raise
        
        self.three_days_ago = datetime.now() - timedelta(days=3)
        self.new_warnings = []
        self.captured_warnings_data = []
        
        print("✅ 爬蟲初始化完成\n")

    def check_keywords(self, text):
        """檢查關鍵字"""
        if not text:
            return []
        return [k for k in self.keywords if k.lower() in text.lower()]

    def parse_date(self, date_str):
        """解析日期"""
        if not date_str:
            return None
        
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
            time.sleep(3)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
            
            print("  📋 正在收集警告列表...")
            items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a")
            
            warnings_to_process = []
            
            for idx, item in enumerate(items, 1):
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
                    
                    if not link or link.startswith(('javascript:', '#')):
                        continue
                    
                    try:
                        publish_time = item.find_element(By.CSS_SELECTOR, ".time").text.strip()
                    except:
                        match = re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text)
                        publish_time = match.group() if match else ""

                    if publish_time:
                        p_date = self.parse_date(publish_time)
                        if p_date and p_date < self.three_days_ago:
                            continue
                    
                    title_coords = self.coord_extractor.extract_coordinates(title)
                    
                    warnings_to_process.append({
                        'title': title,
                        'link': link,
                        'publish_time': publish_time,
                        'keywords': matched,
                        'title_coords': title_coords
                    })
                    
                    print(f"  ✅ [{idx}] 收集: {title[:40]}...")
                    
                except Exception as e:
                    continue
            
            print(f"  📊 共收集到 {len(warnings_to_process)} 個待處理警告")
            
            for idx, warning in enumerate(warnings_to_process, 1):
                try:
                    print(f"\n  📍 [{idx}/{len(warnings_to_process)}] 處理: {warning['title'][:40]}...")
                    
                    coordinates = list(warning['title_coords'])
                    
                    try:
                        print(f"    🌐 訪問詳細頁...")
                        self.driver.get(warning['link'])
                        time.sleep(3)
                        
                        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                        
                        if len(self.driver.page_source) > 500:
                            try:
                                content_div = self.driver.find_element(By.CSS_SELECTOR, ".text#ch_p")
                                page_text = content_div.text
                            except:
                                page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                            
                            if len(page_text) > 50:
                                page_coords = self.coord_extractor.extract_coordinates(page_text)
                                
                                if page_coords:
                                    for pc in page_coords:
                                        if pc not in coordinates:
                                            coordinates.append(pc)
                                    print(f"    ✅ 從頁面提取到 {len(page_coords)} 個座標")
                    
                    except Exception as e:
                        print(f"    ⚠️ 訪問詳細頁失敗: {str(e)[:100]}")
                    
                    if coordinates:
                        print(f"    📍 總共提取到 {len(coordinates)} 個座標")
                    
                    db_data = (
                        bureau_name,
                        warning['title'],
                        warning['link'],
                        warning['publish_time'],
                        ', '.join(warning['keywords']),
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        coordinates
                    )
                    
                    is_new, w_id = self.db_manager.save_warning(db_data)
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id,
                            'bureau': bureau_name,
                            'title': warning['title'],
                            'link': warning['link'],
                            'time': warning['publish_time'],
                            'keywords': warning['keywords'],
                            'coordinates': coordinates
                        })
                        print(f"    ✅ 新警告已儲存 (ID: {w_id})")
                        
                        if self.enable_teams and self.send_mode == 'individual':
                            if self.teams_notifier.send_warning_notification((w_id,) + db_data):
                                self.db_manager.mark_as_notified(w_id)
                            time.sleep(1)
                    else:
                        print(f"    ⏭️ 警告已存在")
                
                except Exception as e:
                    print(f"  ❌ 處理警告 {idx} 時出錯: {str(e)[:100]}")
                    continue
            
            print(f"\n  ✅ {bureau_name} 處理完成")
            
            try:
                print(f"  🔙 返回航行警告列表頁...")
                self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
                time.sleep(3)
                
                nav_btn = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '航行警告')]"))
                )
                self.driver.execute_script("arguments[0].click();", nav_btn)
                time.sleep(2)
                
                print(f"  ✅ 已返回列表頁")
            except Exception as e:
                print(f"  ⚠️ 返回列表頁失敗: {e}")
        
        except Exception as e:
            print(f"❌ 抓取 {bureau_name} 錯誤: {e}")
            traceback.print_exc()

    def scrape_tw_maritime_notices(self):
        """爬取台灣航港局航行警告"""
        if not self.enable_tw:
            return
        
        print("\n" + "="*60)
        print("🇹🇼 開始爬取台灣航港局航行警告")
        print("="*60)
        
        try:
            notices = self.tw_crawler.crawl_recent_notices(max_pages=5)
            
            if not notices:
                print("  ⚠️ 未找到符合條件的台灣航行警告")
                return
            
            print(f"\n  📊 共找到 {len(notices)} 筆台灣航行警告")
            
            for idx, notice in enumerate(notices, 1):
                try:
                    title = notice.get('title', '')
                    link = notice.get('link', '')
                    date = notice.get('date', '')
                    unit = notice.get('unit', '台灣航港局')
                    
                    matched = self.check_keywords(title)
                    if not matched:
                        continue
                    
                    print(f"\n  📍 [{idx}] 處理: {title[:40]}...")
                    
                    coordinates = self.coord_extractor.extract_coordinates(title)
                    
                    if link and link.startswith('http'):
                        try:
                            print(f"    🌐 訪問詳細頁: {link}")
                            response = requests.get(link, timeout=15, verify=False)
                            response.encoding = 'utf-8'
                            
                            if response.status_code == 200:
                                soup = BeautifulSoup(response.text, 'html.parser')
                                
                                content_div = soup.find('div', id='content', class_='content')
                                
                                if content_div:
                                    content_text = content_div.get_text(separator=' ', strip=True)
                                    page_coords = self.coord_extractor.extract_coordinates(content_text)
                                    
                                    if page_coords:
                                        new_coords_count = 0
                                        for pc in page_coords:
                                            if pc not in coordinates:
                                                coordinates.append(pc)
                                                new_coords_count += 1
                                        
                                        if new_coords_count > 0:
                                            print(f"    ✅ 從內容中提取到 {new_coords_count} 個新座標")
                                else:
                                    page_coords = self.coord_extractor.extract_coordinates(soup.get_text())
                                    if page_coords:
                                        coordinates.extend([c for c in page_coords if c not in coordinates])

                        except Exception as e:
                            print(f"    ⚠️ 訪問詳細頁失敗: {str(e)[:100]}")
                    
                    if coordinates:
                        print(f"    📍 總共提取到 {len(coordinates)} 個座標")
                    
                    if isinstance(date, datetime):
                        date_str = date.strftime('%Y-%m-%d')
                    else:
                        date_str = str(date)

                    db_data = (
                        f"台灣-{unit}",
                        title,
                        link,
                        date_str,
                        ', '.join(matched),
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        coordinates
                    )
                    
                    is_new, w_id = self.db_manager.save_warning(db_data)
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id,
                            'bureau': f"台灣-{unit}",
                            'title': title,
                            'link': link,
                            'time': date_str,
                            'keywords': matched,
                            'coordinates': coordinates
                        })
                        print(f"    ✅ 新警告已儲存 (ID: {w_id})")
                        
                        if self.enable_teams and self.send_mode == 'individual':
                            if self.teams_notifier.send_warning_notification((w_id,) + db_data):
                                self.db_manager.mark_as_notified(w_id)
                            time.sleep(1)
                    else:
                        print(f"    ⏭️ 警告已存在")
                
                except Exception as e:
                    print(f"  ❌ 處理警告 {idx} 時出錯: {str(e)[:100]}")
                    traceback.print_exc()
                    continue
            
            print(f"\n  ✅ 台灣航港局處理完成")
        
        except Exception as e:
            print(f"❌ 爬取台灣航港局錯誤: {e}")
            traceback.print_exc()

    def _generate_report(self, duration, map_path=None):
        """生成報告 HTML"""
        font_style = "font-family: 'Microsoft JhengHei', 'PingFang TC', 'Noto Sans TC', sans-serif;"
        count = len(self.captured_warnings_data)
        status_color = "#2E7D32" if count == 0 else "#D9534F"
        
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')
        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')
        
        total_coords = sum(len(w.get('coordinates', [])) for w in self.captured_warnings_data)
        
        html = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ {font_style} color:#333; margin:0; padding:0; }}
                .header {{ background:#003366; color:white; padding:20px; }}
                .header h2 {{ margin:0; font-size:24px; }}
                .header .date {{ margin-top:8px; font-size:12px; opacity:0.9; }}
                .summary {{ background:#f8f9fa; padding:15px; border-bottom:1px solid #ddd; }}
                .summary strong {{ color:{status_color}; font-size:16px; }}
                .map-section {{ text-align:center; margin:20px 0; padding:15px; background:#fff; }}
                .map-section h3 {{ color:#003366; margin-bottom:10px; font-size:18px; }}
                .map-section img {{ max-width:100%; height:auto; border:1px solid #ccc; border-radius:4px; box-shadow:0 2px 4px rgba(0,0,0,0.1); }}
                table {{ width:100%; border-collapse:collapse; font-size:14px; }}
                thead tr {{ background:#005a8d; color:white; }}
                th {{ padding:10px; text-align:left; }}
                td {{ padding:10px; border-bottom:1px solid #eee; }}
                tr:nth-child(even) {{ background:#f2f7fb; }}
                tr:nth-child(odd) {{ background:#ffffff; }}
                a {{ color:#0056b3; text-decoration:none; font-weight:bold; }}
                a:hover {{ text-decoration:underline; }}
                .footer {{ margin-top:30px; padding:20px; background:#eee; text-align:center; font-size:12px; color:#666; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>🚢 海事航行警告監控報告</h2>
                <div class="date">📅 {now_str_LT} (TPE) | {now_str_UTC} (UTC)</div>
            </div>
            <div class="summary">
                <strong>📊 本次執行新增 {count} 個警告</strong><br>
                <span style="font-size:14px;">📍 共提取座標點: {total_coords} 個</span><br>
                <span style="font-size:12px; color:#666;">⏱️ 執行耗時: {duration:.2f} 秒</span>
            </div>
        """
        
        if map_path and os.path.exists(map_path):
            html += """
            <div class="map-section">
                <h3>🗺️ 警告區域分佈圖</h3>
                <img src="cid:map_image" alt="Maritime Warnings Map">
            </div>
            """
        
        if count > 0:
            html += """
            <div style="padding:15px;">
            <table>
                <thead>
                    <tr>
                        <th style="width:15%;">發布單位</th>
                        <th style="width:45%;">標題 / 連結</th>
                        <th style="width:15%;">發布時間</th>
                        <th style="width:25%;">座標摘要</th>
                    </tr>
                </thead>
                <tbody>"""
            
            for i, item in enumerate(self.captured_warnings_data):
                coords = item.get('coordinates', [])
                coord_html = "<span style='color:#999;'>無座標數據</span>"
                if coords:
                    coord_list_str = []
                    for lat, lon in coords[:3]:
                        coord_list_str.append(f"{lat:.3f}°N, {lon:.3f}°E")
                    coord_html = "<br>".join(coord_list_str)
                    if len(coords) > 3:
                        coord_html += f"<br><span style='color:#666; font-size:0.9em;'>...共 {len(coords)} 點</span>"
                
                html += f"""
                    <tr>
                        <td style="font-weight:bold;">{item['bureau']}</td>
                        <td>
                            <a href="{item['link']}" target="_blank">{item['title']}</a>
                            <br><span style="font-size:12px; color:#666;">關鍵字: {', '.join(item['keywords'])}</span>
                        </td>
                        <td>{item['time']}</td>
                        <td style="font-family:monospace; font-size:12px;">{coord_html}</td>
                    </tr>"""
            
            html += """
                </tbody>
            </table>
            </div>"""
        else:
            html += """
            <div style="padding:30px; text-align:center; color:#666;">
                <p>✅ 目前沒有監控到新的航行警告。</p>
            </div>
            """
        
        html += """
            <div class="footer">
                <p>此郵件由自動化系統發送，請勿直接回覆。</p>
                <p>Wan Hai Lines Ltd. | Fleet Risk Management Dept.</p>
            </div>
        </body>
        </html>
        """
        
        json_data = {
            "execution_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "duration": round(duration, 2),
            "new_warnings_count": count,
            "total_coordinates": total_coords,
            "new_warnings": self.captured_warnings_data
        }
        
        return json_data, html

    def run(self):
        """主執行流程"""
        start = datetime.now()
        map_path = None
        
        try:
            print(f"⏱️ 開始執行... (通知模式: {self.send_mode})")
            
            # ========== 1. 中國海事局爬取 ==========
            print("\n" + "="*60)
            print("🇨🇳 開始爬取中國海事局")
            print("="*60)
            
            try:
                print("🌐 正在載入海事局網站...")
                self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
                time.sleep(5)
                
                print("🔍 尋找「航行警告」按鈕...")
                nav_btn = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '航行警告')]"))
                )
                self.driver.execute_script("arguments[0].click();", nav_btn)
                time.sleep(3)
                print("✅ 已點擊「航行警告」")
                
                bureaus = [
                    b.text.strip() 
                    for b in self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text") 
                    if b.text.strip()
                ]
                
                print(f"📍 找到 {len(bureaus)} 個海事局")
                
                for b_name in bureaus:
                    try:
                        elem = self.driver.find_element(
                            By.XPATH, 
                            f"//div[@class='nav_lv2_text' and contains(text(), '{b_name}')]"
                        )
                        self.scrape_bureau_warnings(b_name, elem)
                    except Exception as e:
                        print(f"⚠️ 跳過 {b_name}: {str(e)[:100]}")
                        continue
                
                print(f"\n✅ 中國海事局爬取完成")
                
            except Exception as e:
                print(f"❌ 中國海事局爬取失敗: {e}")
                traceback.print_exc()

            # ========== 2. 台灣航港局爬取 ==========
            if self.enable_tw:
                self.scrape_tw_maritime_notices()
            
            # ========== 3. 批量發送 Teams 通知 ==========
            if self.send_mode == 'batch' and self.enable_teams and self.new_warnings:
                print(f"\n📤 準備 Teams 批量發送...")
                
                unnotified = self.db_manager.get_unnotified_warnings()
                warnings_to_send = [w for w in unnotified if w[0] in self.new_warnings]
                
                if warnings_to_send:
                    if self.teams_notifier.send_batch_notification(warnings_to_send):
                        for w_id in self.new_warnings: 
                            self.db_manager.mark_as_notified(w_id)
                        print("✅ Teams 批量發送完成")
            
            # ========== 4. 繪製地圖 ==========
            if self.captured_warnings_data and self.map_plotter:
                print("\n🗺️ 正在繪製海圖...")
                warnings_for_map = [
                    {
                        'title': w['title'],
                        'coordinates': w.get('coordinates', []),
                        'bureau': w['bureau']
                    }
                    for w in self.captured_warnings_data
                    if w.get('coordinates')
                ]
                
                if warnings_for_map:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    map_filename = f"maritime_warnings_{timestamp}.png"
                    map_path = self.map_plotter.plot_warnings(warnings_for_map, map_filename)
            
            # ========== 5. 統計和報告 ==========
            duration = (datetime.now() - start).total_seconds()
            print(f"\n{'='*60}")
            print(f"✅ 執行完成")
            print(f"⏱️ 耗時: {duration:.2f} 秒")
            print(f"📊 新警告: {len(self.new_warnings)} 筆")
            
            # 統計各來源
            cn_count = sum(1 for w in self.captured_warnings_data if not w['bureau'].startswith('台灣'))
            tw_count = sum(1 for w in self.captured_warnings_data if w['bureau'].startswith('台灣'))
            print(f"   🇨🇳 中國: {cn_count} 筆")
            print(f"   🇹🇼 台灣: {tw_count} 筆")
            
            if map_path:
                print(f"🗺️ 地圖: {map_path}")
            print(f"{'='*60}\n")
            
            # ========== 6. Email 和 Excel ==========
            if self.new_warnings:
                print("📧 正在生成並發送 Email 報告...")
                j_data, h_data = self._generate_report(duration, map_path)
                self.email_notifier.send_trigger_email(j_data, h_data, map_path)
                
                print("📊 正在匯出 Excel...")
                self.db_manager.export_to_excel()
            else:
                print("📧 無新警告，跳過 Email 發送")
            
        except KeyboardInterrupt:
            print("\n⚠️ 使用者中斷執行")
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"❌ 執行錯誤: {e}")
            print(f"{'='*60}")
            traceback.print_exc()
                
        finally:
            try:
                self.driver.quit()
                print("🔚 瀏覽器已關閉")
            except:
                pass
            
            # 關閉資料庫連線
            try:
                self.db_manager.close()
                print("🔚 資料庫連線已關閉")
            except:
                pass


# ==================== 主程式進入點 ====================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚢 海事局航行警告監控系統 v2.0")
    print("   🇨🇳 中國海事局 (CN_MSA)")
    print("   🇹🇼 台灣航港局 (TW_MOTC)")
    print("="*60 + "\n")
    
    # 讀取環境變數
    TEAMS_WEBHOOK = os.getenv('TEAMS_WEBHOOK_URL')
    MAIL_USER = os.getenv('MAIL_USER')
    MAIL_PASS = os.getenv('MAIL_PASSWORD')
    TARGET_EMAIL = os.getenv('TARGET_EMAIL')
    
    # 顯示設定狀態
    print("📋 系統設定檢查:")
    print(f"  • Teams 通知: {'✅ 已設定' if TEAMS_WEBHOOK else '❌ 未設定'}")
    print(f"  • Email 通知: {'✅ 已設定' if (MAIL_USER and MAIL_PASS and TARGET_EMAIL) else '❌ 未設定'}")
    print(f"  • 台灣航港局: {'✅ 已啟用' if BS4_AVAILABLE else '❌ 未啟用 (缺少 BeautifulSoup4)'}")
    print(f"  • 地圖繪製: {'✅ 已啟用' if MAPPING_AVAILABLE else '❌ 未啟用 (缺少 cartopy)'}")
    print()
    
    try:
        # 初始化爬蟲
        scraper = MSANavigationWarningsScraper(
            webhook_url=TEAMS_WEBHOOK,
            enable_teams=bool(TEAMS_WEBHOOK),
            send_mode='batch',  # 'batch' 或 'individual'
            headless=True,      # 無頭模式
            mail_user=MAIL_USER,
            mail_pass=MAIL_PASS,
            target_email=TARGET_EMAIL,
            enable_tw=True      # 啟用台灣航港局爬取
        )
        
        # 執行
        scraper.run()
        
        print("\n🎉 系統執行結束！")
        print("="*60 + "\n")
        
    except KeyboardInterrupt:
        print("\n⚠️ 程式被使用者中斷")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程式執行失敗: {e}")
        traceback.print_exc()
        sys.exit(1)

