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
class CoordinateValidatorExtended:
    """增強型座標驗證與轉換"""
    
    def validate_coordinate_precision(self, lat, lon):
        """驗證座標精度與合理性"""
        # 座標應精確到小數點後4位（約10公尺精度）
        if lat == int(lat) or lon == int(lon):
            return False, "座標精度不足（應為小數點後至少4位）"
        
        # 檢查是否為邊界值（可能為掃描錯誤）
        if (lat in [-90, 0, 90]) or (lon in [-180, 0, 180]):
            return False, "座標疑似為邊界值"
        
        return True, "座標精度合格"
    
    def cluster_nearby_coordinates(self, coordinates, threshold_km=1.0):
        """將相近座標點聚集（去除重複提取）"""
        from math import radians, cos, sin, asin, sqrt
        
        clusters = []
        for coord in coordinates:
            is_new = True
            for cluster in clusters:
                # 計算大圓距離
                distance = self.haversine_distance(coord, cluster[0])
                if distance < threshold_km:
                    cluster.append(coord)
                    is_new = False
                    break
            if is_new:
                clusters.append([coord])
        
        # 回傳聚集中心
        return [self._calculate_centroid(c) for c in clusters]
    
    @staticmethod
    def haversine_distance(coord1, coord2):
        """計算兩點間大圓距離（公里）"""
        from math import radians, cos, sin, asin, sqrt
        
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        km = 6371 * c
        return km
    
    @staticmethod
    def _calculate_centroid(coordinates):
        """計算座標點的質心"""
        if not coordinates:
            return None
        avg_lat = sum(c[0] for c in coordinates) / len(coordinates)
        avg_lon = sum(c[1] for c in coordinates) / len(coordinates)
        return (avg_lat, avg_lon)
class GeofenceDetector:
    """地理圍欄與風險區域檢測"""
    
    def __init__(self):
        from shapely.geometry import Point, Polygon, MultiPoint
        self.Point = Point
        self.Polygon = Polygon
        self.MultiPoint = MultiPoint
    
    def is_point_in_polygon(self, point_lat, point_lon, polygon_coords):
        """判斷點是否在多邊形內（Ray Casting 算法）"""
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            return polygon.contains(point)
        except Exception as e:
            print(f"多邊形檢測失敗: {e}")
            return False
    
    def point_to_polygon_distance(self, point_lat, point_lon, polygon_coords):
        """計算點到多邊形邊界的最短距離"""
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            
            # 距離單位為度數，需轉換為公里（粗略：1°≈111km）
            distance_degrees = point.distance(polygon)
            distance_km = distance_degrees * 111
            
            return distance_km
        except Exception as e:
            print(f"距離計算失敗: {e}")
            return float('inf')
    
    def detect_zone_threat(self, vessel_lat, vessel_lon, warning_data, 
                           buffer_km=5.0):
        """偵測船舶對警告區域的威脅等級
        
        Args:
            vessel_lat, vessel_lon: 船舶當前位置
            warning_data: {'type': 'point'|'polygon', 'coordinates': [...]}
            buffer_km: 緩衝區距離（公里）
        
        Returns:
            {
                'threat_level': 'CRITICAL'|'HIGH'|'MEDIUM'|'LOW'|'SAFE',
                'distance_km': float,
                'is_in_zone': bool,
                'eta_hours': float (estimated time to entry)
            }
        """
        from math import radians, cos, sin, atan2, sqrt, degrees
        
        coords = warning_data.get('coordinates', [])
        if not coords:
            return {'threat_level': 'SAFE', 'distance_km': float('inf')}
        
        warn_type = warning_data.get('type', 'point')
        
        if warn_type == 'point' and len(coords) == 1:
            # 單點警告：計算距離
            distance_km = CoordinateValidatorExtended.haversine_distance(
                (vessel_lat, vessel_lon), coords[0]
            )
            
            if distance_km < buffer_km * 0.5:
                threat_level = 'CRITICAL'
            elif distance_km < buffer_km:
                threat_level = 'HIGH'
            elif distance_km < buffer_km * 2:
                threat_level = 'MEDIUM'
            elif distance_km < buffer_km * 5:
                threat_level = 'LOW'
            else:
                threat_level = 'SAFE'
        
        else:  # 多邊形警告
            is_in = self.is_point_in_polygon(vessel_lat, vessel_lon, coords)
            distance_km = self.point_to_polygon_distance(vessel_lat, vessel_lon, coords)
            
            if is_in:
                threat_level = 'CRITICAL'
            elif distance_km < buffer_km:
                threat_level = 'HIGH'
            elif distance_km < buffer_km * 2:
                threat_level = 'MEDIUM'
            elif distance_km < buffer_km * 5:
                threat_level = 'LOW'
            else:
                threat_level = 'SAFE'
        
        return {
            'threat_level': threat_level,
            'distance_km': distance_km,
            'is_in_zone': (threat_level == 'CRITICAL' and is_in) if warn_type != 'point' else False,
            'buffer_km': buffer_km
        }
class EnhancedMaritimeMapPlotter:
    """增強型地圖繪製（含風險層級與船舶軌跡）"""
    
    def plot_warnings_with_vessel_position(self, warnings_data, 
                                          vessel_data=None, 
                                          output_filename="maritime_with_vessel.png"):
        """繪製警告區域與船舶位置
        
        Args:
            vessel_data: {
                'name': str,
                'lat': float,
                'lon': float,
                'speed_knots': float,
                'heading': float (0-360),
                'threats': [warning_ids]
            }
        """
        if not MAPPING_AVAILABLE:
            print("❌ 地圖繪製功能不可用")
            return None
        
        try:
            import matplotlib.pyplot as plt
            from matplotlib.patches import Polygon, FancyArrow, Circle
            from matplotlib.patches import Wedge
            import cartopy.crs as ccrs
            import cartopy.feature as cfeature
            
            fig = plt.figure(figsize=(18, 14))
            ax = plt.axes(projection=ccrs.PlateCarree())
            
            # 計算顯示範圍
            all_coords = []
            for w in warnings_data:
                all_coords.extend(w.get('coordinates', []))
            
            if vessel_data and vessel_data.get('lat') and vessel_data.get('lon'):
                all_coords.append((vessel_data['lat'], vessel_data['lon']))
            
            if not all_coords:
                print("⚠️ 無座標資料")
                return None
            
            lats = [c[0] for c in all_coords]
            lons = [c[1] for c in all_coords]
            
            # 添加邊距
            lat_min, lat_max = min(lats) - 1, max(lats) + 1
            lon_min, lon_max = min(lons) - 1, max(lons) + 1
            
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], 
                         crs=ccrs.PlateCarree())
            
            # 添加底圖
            ax.add_feature(cfeature.LAND, facecolor='lightgray', edgecolor='black')
            ax.add_feature(cfeature.OCEAN, facecolor='lightblue')
            ax.add_feature(cfeature.COASTLINE, linewidth=1)
            ax.gridlines(draw_labels=True, linewidth=0.5, color='gray', alpha=0.5)
            
            # 繪製警告區域（含顏色編碼威脅等級）
            threat_colors = {
                'CRITICAL': '#d32f2f',  # 深紅
                'HIGH': '#f57c00',      # 橙色
                'MEDIUM': '#fbc02d',    # 黃色
                'LOW': '#1976d2'        # 藍色
            }
            
            for warning in warnings_data:
                coords = warning.get('coordinates', [])
                threat = warning.get('threat_level', 'LOW')
                color = threat_colors.get(threat, '#1976d2')
                
                if len(coords) > 1:
                    lons_line = [c[1] for c in coords] + [coords[0][1]]
                    lats_line = [c[0] for c in coords] + [coords[0][0]]
                    
                    ax.plot(lons_line, lats_line, color=color, linewidth=2.5,
                           transform=ccrs.PlateCarree(), alpha=0.8)
                    
                    polygon = Polygon([(c[1], c[0]) for c in coords],
                                     facecolor=color, alpha=0.15,
                                     transform=ccrs.PlateCarree())
                    ax.add_patch(polygon)
                else:
                    # 點狀警告繪製圓形緩衝區
                    for lat, lon in coords:
                        circle = Circle((lon, lat), radius=0.1,
                                      facecolor=color, alpha=0.2,
                                      edgecolor=color, linewidth=2,
                                      transform=ccrs.PlateCarree())
                        ax.add_patch(circle)
                        
                        ax.plot(lon, lat, marker='X', color=color, markersize=15,
                               transform=ccrs.PlateCarree())
            
            # 繪製船舶位置
            if vessel_data:
                vlon = vessel_data['lon']
                vlat = vessel_data['lat']
                
                # 船舶符號（三角形，指向航向）
                heading = vessel_data.get('heading', 0)
                ax.plot(vlon, vlat, marker='^', color='green', markersize=20,
                       transform=ccrs.PlateCarree(), markeredgecolor='darkgreen',
                       markeredgewidth=2)
                
                # 航向指示線
                if vessel_data.get('speed_knots', 0) > 0:
                    import numpy as np
                    dlon = 0.1 * np.sin(np.radians(heading))
                    dlat = 0.1 * np.cos(np.radians(heading))
                    ax.arrow(vlon, vlat, dlon, dlat, head_width=0.05,
                            head_length=0.05, fc='green', ec='green',
                            transform=ccrs.PlateCarree(), alpha=0.7)
                
                # 威脅指示（若有）
                if vessel_data.get('threats'):
                    threat_text = f"🚨 威脅: {len(vessel_data['threats'])}"
                    ax.text(vlon + 0.2, vlat + 0.2, threat_text,
                           fontsize=12, color='red', weight='bold',
                           transform=ccrs.PlateCarree(),
                           bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7))
                
                # 船舶資訊面板
                vessel_info = f"{vessel_data.get('name', 'VESSEL')}\n"
                vessel_info += f"速度: {vessel_data.get('speed_knots', 0):.1f} 節"
                ax.text(vlon - 0.5, vlat - 0.5, vessel_info,
                       fontsize=10, transform=ccrs.PlateCarree(),
                       bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.8))
            
            # 圖例
            from matplotlib.lines import Line2D
            legend_elements = [
                Line2D([0], [0], color='#d32f2f', lw=3, label='⚠️ 危險 (CRITICAL)'),
                Line2D([0], [0], color='#f57c00', lw=3, label='警告 (HIGH)'),
                Line2D([0], [0], color='#fbc02d', lw=3, label='留意 (MEDIUM)'),
                Line2D([0], [0], color='#1976d2', lw=3, label='低風險 (LOW)'),
                Line2D([0], [0], marker='^', color='w', markerfacecolor='green',
                      markersize=12, label='船舶位置')
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=11)
            
            plt.title("航海警告與船舶位置分析\n(含威脅評估)", 
                     fontsize=16, fontweight='bold', pad=20)
            
            output_path = os.path.join(self.output_dir, output_filename)
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            print(f"✅ 增強地圖已儲存: {output_path}")
            return output_path
            
        except Exception as e:
            print(f"❌ 增強地圖繪製失敗: {e}")
            traceback.print_exc()
            return None
class VesselRiskAssessment:
    """船舶碰撞風險智能評分"""
    
    def __init__(self, geofence_detector):
        self.geofence = geofence_detector
        self.risk_thresholds = {
            'CRITICAL': {'score': 90, 'action': 'IMMEDIATE_ALERT'},
            'HIGH': {'score': 70, 'action': 'URGENT_WARNING'},
            'MEDIUM': {'score': 50, 'action': 'ROUTINE_NOTICE'},
            'LOW': {'score': 30, 'action': 'INFO_LOG'}
        }
    
    def assess_vessel_threat(self, vessel_data, warnings_data):
        """對單艘船舶進行綜合威脅評估
        
        Returns:
            {
                'vessel_name': str,
                'overall_risk_score': 0-100,
                'threat_level': str,
                'nearby_warnings': [...],
                'recommendations': [...],
                'action_required': bool
            }
        """
        vessel_lat = vessel_data.get('lat')
        vessel_lon = vessel_data.get('lon')
        vessel_speed = vessel_data.get('speed_knots', 0)
        vessel_draft = vessel_data.get('draft_m', 0)  # 船舶吃水
        vessel_type = vessel_data.get('type', 'GENERAL')
        
        nearby_threats = []
        max_threat_score = 0
        
        for warning in warnings_data:
            # 計算威脅等級
            threat_assessment = self.geofence.detect_zone_threat(
                vessel_lat, vessel_lon, warning
            )
            
            # 增加詳細資訊
            threat_assessment['warning_title'] = warning.get('title', 'Unknown')
            threat_assessment['warning_type'] = warning.get('type', 'point')
            threat_assessment['warning_id'] = warning.get('id')
            
            threat_level = threat_assessment['threat_level']
            
            # 僅記錄非 SAFE 的威脅
            if threat_level != 'SAFE':
                nearby_threats.append(threat_assessment)
                threat_score = self.risk_thresholds[threat_level]['score']
                
                # 根據距離動態調整分數
                distance = threat_assessment.get('distance_km', float('inf'))
                if distance < 5:
                    threat_score = min(100, threat_score + 15)
                elif distance < 10:
                    threat_score = min(100, threat_score + 10)
                
                max_threat_score = max(max_threat_score, threat_score)
        
        # 排序威脅（距離最近優先）
        nearby_threats.sort(
            key=lambda x: x.get('distance_km', float('inf'))
        )
        
        # 判定整體威脅等級
        if max_threat_score >= 90:
            overall_threat = 'CRITICAL'
        elif max_threat_score >= 70:
            overall_threat = 'HIGH'
        elif max_threat_score >= 50:
            overall_threat = 'MEDIUM'
        elif max_threat_score >= 30:
            overall_threat = 'LOW'
        else:
            overall_threat = 'SAFE'
        
        # 生成建議
        recommendations = self._generate_recommendations(
            overall_threat, nearby_threats, vessel_data
        )
        
        return {
            'vessel_name': vessel_data.get('name', 'UNKNOWN'),
            'vessel_type': vessel_type,
            'vessel_position': (vessel_lat, vessel_lon),
            'vessel_speed': vessel_speed,
            'overall_risk_score': max_threat_score,
            'threat_level': overall_threat,
            'nearby_warnings': nearby_threats[:5],  # 顯示最近的 5 個
            'warning_count': len(nearby_threats),
            'recommendations': recommendations,
            'action_required': overall_threat in ['CRITICAL', 'HIGH']
        }
    
    def _generate_recommendations(self, threat_level, warnings, vessel_data):
        """根據威脅等級生成航海建議"""
        recommendations = []
        
        if threat_level == 'CRITICAL':
            recommendations.append("🚨 立即改變航向或減速")
            recommendations.append("📞 與港口當局/附近船舶聯繫")
            recommendations.append("🛑 準備應急程序")
            recommendations.append("📡 啟動 AIS 實時廣播")
        
        elif threat_level == 'HIGH':
            recommendations.append("⚠️ 密切監測警告區域")
            recommendations.append("🧭 考慮改變航線")
            recommendations.append("📡 增加 AIS 報告頻率")
            recommendations.append("👥 通知船長與船員")
        
        elif threat_level == 'MEDIUM':
            recommendations.append("💡 留意警告區域的最新資訊")
            recommendations.append("📍 記錄當前位置與時間")
            recommendations.append("📊 評估替代航線")
        
        elif threat_level == 'LOW':
            recommendations.append("ℹ️ 維持常規航向監控")
            recommendations.append("📚 查看警告詳細內容")
        
        # 特定建議（根據警告類型）
        for warning in warnings[:2]:
            title = warning.get('warning_title', '')
            if '射擊' in title:
                recommendations.append("⚡ 警告：該區域有軍事射擊訓練，遠離為佳")
            elif '礙航' in title:
                recommendations.append("🚧 注意：該區域有障礙物，減速行駛")
            elif '颶風' in title or '台風' in title:
                recommendations.append("🌪️ 警告：惡劣天氣，加強固定與安全措施")
        
        return recommendations
    
    def assess_fleet_status(self, fleet_data, warnings_data):
        """對整個船隊進行風險評估"""
        fleet_assessment = {
            'total_vessels': len(fleet_data),
            'vessels_in_danger': 0,
            'vessels_in_high_risk': 0,
            'vessels_safe': 0,
            'vessel_reports': [],
            'critical_alerts': [],
            'recommended_actions': []
        }
        
        for vessel in fleet_data:
            assessment = self.assess_vessel_threat(vessel, warnings_data)
            fleet_assessment['vessel_reports'].append(assessment)
            
            threat_level = assessment['threat_level']
            if threat_level == 'CRITICAL':
                fleet_assessment['vessels_in_danger'] += 1
                fleet_assessment['critical_alerts'].append(
                    f"🚨 {assessment['vessel_name']}: {threat_level}"
                )
            elif threat_level == 'HIGH':
                fleet_assessment['vessels_in_high_risk'] += 1
            else:
                fleet_assessment['vessels_safe'] += 1
        
        # 摘要建議
        if fleet_assessment['vessels_in_danger'] > 0:
            fleet_assessment['recommended_actions'].append(
                f"立即關注 {fleet_assessment['vessels_in_danger']} 艘危險船舶"
            )
        
        if fleet_assessment['vessels_in_high_risk'] > 0:
            fleet_assessment['recommended_actions'].append(
                f"密切監控 {fleet_assessment['vessels_in_high_risk']} 艘高風險船舶"
            )
        
        return fleet_assessment
class EnhancedNotificationSystem:
    """增強型通知系統（含風險評分與建議）"""
    
    def __init__(self, teams_notifier, email_notifier):
        self.teams = teams_notifier
        self.email = email_notifier
    
    def send_vessel_alert(self, assessment_data, webhook_url):
        """發送船舶特定風險提醒"""
        
        threat_level = assessment_data['threat_level']
        vessel_name = assessment_data['vessel_name']
        
        # 顏色編碼
        color_map = {
            'CRITICAL': '#d32f2f',
            'HIGH': '#f57c00',
            'MEDIUM': '#fbc02d',
            'LOW': '#1976d2',
            'SAFE': '#4caf50'
        }
        
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"🚢 船舶: {vessel_name}",
                "weight": "Bolder",
                "size": "Large",
                "color": "Accent"
            },
            {
                "type": "TextBlock",
                "text": f"威脅等級: {threat_level}",
                "weight": "Bolder",
                "color": "Attention",
                "size": "Medium"
            },
            {
                "type": "TextBlock",
                "text": f"風險分數: {assessment_data['overall_risk_score']}/100",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"位置: {assessment_data['vessel_position'][0]:.4f}°N, {assessment_data['vessel_position'][1]:.4f}°E",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"速度: {assessment_data['vessel_speed']:.1f} 節",
                "spacing": "Small"
            }
        ]
        
        # 附近警告
        if assessment_data['nearby_warnings']:
            body_elements.append({
                "type": "TextBlock",
                "text": "⚠️ 附近警告:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for warning in assessment_data['nearby_warnings'][:3]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": f"• {warning['warning_title'][:60]}\n  距離: {warning['distance_km']:.1f} 公里",
                    "size": "Small",
                    "spacing": "Small",
                    "wrap": True
                })
        
        # 建議
        if assessment_data['recommendations']:
            body_elements.append({
                "type": "TextBlock",
                "text": "💡 建議行動:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for rec in assessment_data['recommendations'][:4]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": rec,
                    "size": "Small",
                    "spacing": "Small"
                })
        
        # 發送 Teams 卡片
        payload = self.teams._create_adaptive_card(
            f"🚢 船舶風險提醒: {threat_level}",
            body_elements
        )
        
        try:
            import requests
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                verify=False
            )
            return response.status_code in [200, 202]
        except Exception as e:
            print(f"❌ Teams 通知失敗: {e}")
            return False
    
    def send_fleet_status_report(self, fleet_assessment, webhook_url):
        """發送艦隊狀態總報告"""
        
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"艦隊總數: {fleet_assessment['total_vessels']}",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"🚨 危險: {fleet_assessment['vessels_in_danger']}",
                "color": "Attention",
                "weight": "Bolder"
            },
            {
                "type": "TextBlock",
                "text": f"⚠️ 高風險: {fleet_assessment['vessels_in_high_risk']}",
                "color": "Warning"
            },
            {
                "type": "TextBlock",
                "text": f"✅ 安全: {fleet_assessment['vessels_safe']}",
                "color": "Good"
            }
        ]
        
        # 關鍵警報
        if fleet_assessment['critical_alerts']:
            body_elements.append({
                "type": "TextBlock",
                "text": "🚨 立即警報:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for alert in fleet_assessment['critical_alerts']:
                body_elements.append({
                    "type": "TextBlock",
                    "text": alert,
                    "size": "Small"
                })
        
        payload = self.teams._create_adaptive_card(
            "📊 艦隊風險狀態報告",
            body_elements
        )
        
        try:
            import requests
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                verify=False
            )
            return response.status_code in [200, 202]
        except Exception as e:
            print(f"❌ 艦隊報告發送失敗: {e}")
            return False
class EnhancedNotificationSystem:
    """增強型通知系統（含風險評分與建議）"""
    
    def __init__(self, teams_notifier, email_notifier):
        self.teams = teams_notifier
        self.email = email_notifier
    
    def send_vessel_alert(self, assessment_data, webhook_url):
        """發送船舶特定風險提醒"""
        
        threat_level = assessment_data['threat_level']
        vessel_name = assessment_data['vessel_name']
        
        # 顏色編碼
        color_map = {
            'CRITICAL': '#d32f2f',
            'HIGH': '#f57c00',
            'MEDIUM': '#fbc02d',
            'LOW': '#1976d2',
            'SAFE': '#4caf50'
        }
        
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"🚢 船舶: {vessel_name}",
                "weight": "Bolder",
                "size": "Large",
                "color": "Accent"
            },
            {
                "type": "TextBlock",
                "text": f"威脅等級: {threat_level}",
                "weight": "Bolder",
                "color": "Attention",
                "size": "Medium"
            },
            {
                "type": "TextBlock",
                "text": f"風險分數: {assessment_data['overall_risk_score']}/100",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"位置: {assessment_data['vessel_position'][0]:.4f}°N, {assessment_data['vessel_position'][1]:.4f}°E",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"速度: {assessment_data['vessel_speed']:.1f} 節",
                "spacing": "Small"
            }
        ]
        
        # 附近警告
        if assessment_data['nearby_warnings']:
            body_elements.append({
                "type": "TextBlock",
                "text": "⚠️ 附近警告:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for warning in assessment_data['nearby_warnings'][:3]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": f"• {warning['warning_title'][:60]}\n  距離: {warning['distance_km']:.1f} 公里",
                    "size": "Small",
                    "spacing": "Small",
                    "wrap": True
                })
        
        # 建議
        if assessment_data['recommendations']:
            body_elements.append({
                "type": "TextBlock",
                "text": "💡 建議行動:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for rec in assessment_data['recommendations'][:4]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": rec,
                    "size": "Small",
                    "spacing": "Small"
                })
        
        # 發送 Teams 卡片
        payload = self.teams._create_adaptive_card(
            f"🚢 船舶風險提醒: {threat_level}",
            body_elements
        )
        
        try:
            import requests
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                verify=False
            )
            return response.status_code in [200, 202]
        except Exception as e:
            print(f"❌ Teams 通知失敗: {e}")
            return False
    
    def send_fleet_status_report(self, fleet_assessment, webhook_url):
        """發送艦隊狀態總報告"""
        
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"艦隊總數: {fleet_assessment['total_vessels']}",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": f"🚨 危險: {fleet_assessment['vessels_in_danger']}",
                "color": "Attention",
                "weight": "Bolder"
            },
            {
                "type": "TextBlock",
                "text": f"⚠️ 高風險: {fleet_assessment['vessels_in_high_risk']}",
                "color": "Warning"
            },
            {
                "type": "TextBlock",
                "text": f"✅ 安全: {fleet_assessment['vessels_safe']}",
                "color": "Good"
            }
        ]
        
        # 關鍵警報
        if fleet_assessment['critical_alerts']:
            body_elements.append({
                "type": "TextBlock",
                "text": "🚨 立即警報:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for alert in fleet_assessment['critical_alerts']:
                body_elements.append({
                    "type": "TextBlock",
                    "text": alert,
                    "size": "Small"
                })
        
        payload = self.teams._create_adaptive_card(
            "📊 艦隊風險狀態報告",
            body_elements
        )
        
        try:
            import requests
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                verify=False
            )
            return response.status_code in [200, 202]
        except Exception as e:
            print(f"❌ 艦隊報告發送失敗: {e}")
            return False
# ==================== 新增模組：座標驗證與地理圍欄 ====================

from shapely.geometry import Point, Polygon, MultiPoint
from math import radians, cos, sin, asin, sqrt, atan2, degrees
import json

class CoordinateValidatorExtended:
    """增強型座標驗證與聚集"""
    
    @staticmethod
    def haversine_distance(coord1, coord2):
        """計算兩座標間的大圓距離（公里）
        
        原理: 利用球面幾何計算地球表面兩點間最短距離
        公式: d = 2R * arcsin(sqrt(sin²(Δφ/2) + cos(φ1)*cos(φ2)*sin²(Δλ/2)))
        其中 R = 6371 km (地球平均半徑)
        """
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        distance_km = 6371 * c
        
        return distance_km
    
    @staticmethod
    def calculate_bearing(coord1, coord2):
        """計算從 coord1 到 coord2 的方位角（0-360度）"""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        dlon = radians(lon2 - lon1)
        y = sin(dlon) * cos(radians(lat2))
        x = cos(radians(lat1)) * sin(radians(lat2)) - sin(radians(lat1)) * cos(radians(lat2)) * cos(dlon)
        
        bearing = degrees(atan2(y, x))
        bearing = (bearing + 360) % 360  # 轉換為 0-360
        
        return bearing
    
    @staticmethod
    def cluster_nearby_coordinates(coordinates, threshold_km=1.0):
        """聚集相鄰座標（去除重複提取）
        
        算法: 凝聚式聚類 (Agglomerative Clustering)
        1. 初始化每個座標為獨立聚類
        2. 反復合併距離小於閾值的聚類
        3. 回傳各聚類的質心
        """
        if not coordinates or len(coordinates) == 1:
            return coordinates
        
        clusters = [[coord] for coord in coordinates]
        
        # 合併相鄰聚類
        changed = True
        while changed and len(clusters) > 1:
            changed = False
            new_clusters = []
            used = [False] * len(clusters)
            
            for i in range(len(clusters)):
                if used[i]:
                    continue
                
                merged_cluster = clusters[i][:]
                
                for j in range(i + 1, len(clusters)):
                    if used[j]:
                        continue
                    
                    # 計算聚類間的最小距離
                    min_dist = float('inf')
                    for c1 in merged_cluster:
                        for c2 in clusters[j]:
                            dist = CoordinateValidatorExtended.haversine_distance(c1, c2)
                            min_dist = min(min_dist, dist)
                    
                    if min_dist < threshold_km:
                        merged_cluster.extend(clusters[j])
                        used[j] = True
                        changed = True
                
                new_clusters.append(merged_cluster)
            
            clusters = new_clusters
        
        # 計算各聚類的質心
        centroids = []
        for cluster in clusters:
            avg_lat = sum(c[0] for c in cluster) / len(cluster)
            avg_lon = sum(c[1] for c in cluster) / len(cluster)
            centroids.append((avg_lat, avg_lon))
        
        return centroids


class GeofenceDetector:
    """地理圍欄與風險區域檢測系統"""
    
    def __init__(self):
        self.Point = Point
        self.Polygon = Polygon
    
    def is_point_in_polygon(self, point_lat, point_lon, polygon_coords):
        """判斷點是否在多邊形內
        
        使用 Shapely 庫的 Ray Casting 算法：
        - 從點發出射線
        - 計算射線與多邊形邊的交點數
        - 奇數次交點表示在多邊形內
        """
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            
            if not polygon.is_valid:
                print(f"⚠️ 多邊形不合法，嘗試修正...")
                from shapely.ops import unary_union
                polygon = unary_union(polygon.buffer(0))
            
            return polygon.contains(point)
        except Exception as e:
            print(f"⚠️ 多邊形檢測失敗: {e}")
            return False
    
    def point_to_polygon_distance(self, point_lat, point_lon, polygon_coords):
        """計算點到多邊形的最短距離（公里）
        
        返回距離：
        - 0: 點在多邊形內或邊界上
        - >0: 點到多邊形邊界的最短距離
        """
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            
            # Shapely 中距離單位為度數，轉換為公里
            # 粗略換算：1°緯度 ≈ 111 km，1°經度 ≈ 111 * cos(緯度)
            distance_degrees = point.distance(polygon)
            avg_lat = point_lat
            distance_km = distance_degrees * 111 * cos(radians(avg_lat))
            
            return distance_km
        except Exception as e:
            print(f"⚠️ 距離計算失敗: {e}")
            return float('inf')
    
    def detect_zone_threat(self, vessel_lat, vessel_lon, warning_data, buffer_km=5.0):
        """判定船舶對警告區域的威脅等級
        
        返回值:
        {
            'threat_level': 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'SAFE',
            'distance_km': float,  # 到警告區域的距離
            'is_in_zone': bool,    # 是否在危險區內
            'buffer_km': float,    # 使用的緩衝距離
            'certainty': float     # 0-1, 判斷的確定性
        }
        """
        coords = warning_data.get('coordinates', [])
        if not coords:
            return {
                'threat_level': 'SAFE',
                'distance_km': float('inf'),
                'is_in_zone': False,
                'certainty': 1.0
            }
        
        warn_type = warning_data.get('type', 'point')
        
        # 情況 1: 點狀警告（如射擊區、施工點）
        if warn_type == 'point' or len(coords) == 1:
            distance_km = CoordinateValidatorExtended.haversine_distance(
                (vessel_lat, vessel_lon), coords[0]
            )
            
            threat_map = [
                (buffer_km * 0.25, 'CRITICAL', 0.95),
                (buffer_km * 0.5, 'HIGH', 0.9),
                (buffer_km, 'MEDIUM', 0.85),
                (buffer_km * 2, 'LOW', 0.7),
                (buffer_km * 5, 'LOW', 0.5),
                (float('inf'), 'SAFE', 0.0)
            ]
            
            threat_level = 'SAFE'
            certainty = 0.0
            
            for threshold, level, cert in threat_map:
                if distance_km < threshold:
                    threat_level = level
                    certainty = cert
                    break
            
            is_in_zone = distance_km < buffer_km * 0.5
        
        # 情況 2: 多邊形警告（如作業區、颶風路徑）
        else:
            is_in_zone = self.is_point_in_polygon(vessel_lat, vessel_lon, coords)
            distance_km = self.point_to_polygon_distance(vessel_lat, vessel_lon, coords)
            
            if is_in_zone:
                threat_level = 'CRITICAL'
                certainty = 1.0
            elif distance_km < buffer_km * 0.5:
                threat_level = 'HIGH'
                certainty = 0.95
            elif distance_km < buffer_km:
                threat_level = 'MEDIUM'
                certainty = 0.9
            elif distance_km < buffer_km * 2:
                threat_level = 'LOW'
                certainty = 0.7
            else:
                threat_level = 'SAFE'
                certainty = 0.5
        
        return {
            'threat_level': threat_level,
            'distance_km': round(distance_km, 2),
            'is_in_zone': is_in_zone,
            'buffer_km': buffer_km,
            'certainty': certainty
        }
    
    def get_warning_polygon_area(self, polygon_coords):
        """計算多邊形警告區域面積（平方公里）"""
        try:
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            
            # Shapely 計算的面積單位為平方度數
            area_sq_degrees = polygon.area
            # 轉換為平方公里（粗略：1°² ≈ 12100 km² 在赤道）
            area_sq_km = area_sq_degrees * 12100
            
            return round(area_sq_km, 2)
        except Exception as e:
            print(f"⚠️ 面積計算失敗: {e}")
            return None


class VesselRiskAssessment:
    """船舶碰撞與運營風險智能評分系統"""
    
    def __init__(self, geofence_detector):
        self.geofence = geofence_detector
        self.coord_validator = CoordinateValidatorExtended()
    
    def assess_vessel_threat(self, vessel_data, warnings_data):
        """對單艘船舶進行綜合威脅評估
        
        考慮因素:
        1. 距離危險區的遠近 (distance)
        2. 船舶速度 (closing_speed)
        3. 船舶類型敏感性 (vessel_type_factor)
        4. 吃水影響 (draft_factor)
        5. 警告區域面積 (zone_size_factor)
        """
        vessel_lat = vessel_data.get('lat')
        vessel_lon = vessel_data.get('lon')
        vessel_speed = vessel_data.get('speed_knots', 0)
        vessel_draft = vessel_data.get('draft_m', 0)
        vessel_type = vessel_data.get('type', 'GENERAL')
        vessel_heading = vessel_data.get('heading', 0)
        
        # 船舶類型敏感度因子
        type_factors = {
            'TANKER': 1.3,      # 油輪：敏感度最高
            'CONTAINER': 1.2,   # 貨櫃船
            'GENERAL': 1.0,     # 雜貨船
            'BULK': 0.9,        # 散貨船
            'PASSENGER': 1.4,   # 客輪：最敏感
            'FISHING': 0.7      # 漁船：敏感度較低
        }
        type_factor = type_factors.get(vessel_type, 1.0)
        
        # 吃水影響因子（吃水越深越容易擱淺）
        draft_factor = 1.0 + (vessel_draft / 15.0) if vessel_draft > 0 else 1.0
        
        nearby_threats = []
        weighted_threat_score = 0
        total_weight = 0
        
        for idx, warning in enumerate(warnings_data):
            # 計算基礎威脅等級
            threat_assessment = self.geofence.detect_zone_threat(
                vessel_lat, vessel_lon, warning
            )
            
            threat_level = threat_assessment['threat_level']
            
            if threat_level == 'SAFE':
                continue
            
            # 基礎分數對映
            threat_scores = {
                'CRITICAL': 100,
                'HIGH': 75,
                'MEDIUM': 50,
                'LOW': 25,
                'SAFE': 0
            }
            base_score = threat_scores[threat_level]
            
            distance = threat_assessment.get('distance_km', float('inf'))
            certainty = threat_assessment.get('certainty', 0.5)
            
            # 計算接近速度（船舶朝向警告區的速度分量）
            warning_coords = warning.get('coordinates', [])
            if warning_coords:
                if len(warning_coords) > 1:
                    # 多邊形：使用質心
                    warning_center = (
                        sum(c[0] for c in warning_coords) / len(warning_coords),
                        sum(c[1] for c in warning_coords) / len(warning_coords)
                    )
                else:
                    warning_center = warning_coords[0]
                
                bearing_to_warning = self.coord_validator.calculate_bearing(
                    (vessel_lat, vessel_lon), warning_center
                )
                
                # 計算航向與威脅方向的差異（0 = 直接駛向）
                heading_diff = abs(vessel_heading - bearing_to_warning)
                heading_diff = min(heading_diff, 360 - heading_diff)
                
                # 接近因子（角度差越小，接近因子越大）
                approach_factor = 1 - (heading_diff / 180)
                approach_factor = max(0, approach_factor)
            else:
                approach_factor = 0.5
            
            # 根據警告類型調整分數
            warning_title = warning.get('title', '').lower()
            if '射擊' in warning_title:
                type_multiplier = 1.5
            elif '礙航' in warning_title:
                type_multiplier = 1.3
            elif '颶風' in warning_title or '台風' in warning_title:
                type_multiplier = 1.2
            else:
                type_multiplier = 1.0
            
            # 綜合分數計算
            adjusted_score = base_score * type_factor * draft_factor * type_multiplier
            distance_penalty = max(0, 1 - (distance / 20))  # 距離越遠懲罰越大
            approach_bonus = approach_factor * 0.3  # 直接駛向增加 30% 權重
            
            final_score = (adjusted_score * distance_penalty + approach_bonus * 50) * certainty
            
            threat_assessment['warning_title'] = warning.get('title', 'Unknown')
            threat_assessment['warning_type'] = warning.get('type', 'point')
            threat_assessment['warning_id'] = warning.get('id', idx)
            threat_assessment['final_score'] = round(final_score, 2)
            threat_assessment['bearing_to_warning'] = bearing_to_warning if warning_coords else None
            
            nearby_threats.append(threat_assessment)
            
            # 加權計算整體風險分
            weighted_threat_score += final_score
            total_weight += 1
        
        # 排序威脅
        nearby_threats.sort(key=lambda x: x.get('final_score', 0), reverse=True)
        
        # 計算整體風險分（0-100）
        if total_weight > 0:
            overall_score = min(100, weighted_threat_score / total_weight)
        else:
            overall_score = 0
        
        # 判定整體威脅等級
        if overall_score >= 85:
            overall_threat = 'CRITICAL'
            action_urgency = 'IMMEDIATE'
        elif overall_score >= 65:
            overall_threat = 'HIGH'
            action_urgency = 'URGENT'
        elif overall_score >= 45:
            overall_threat = 'MEDIUM'
            action_urgency = 'SOON'
        elif overall_score >= 25:
            overall_threat = 'LOW'
            action_urgency = 'MONITOR'
        else:
            overall_threat = 'SAFE'
            action_urgency = 'ROUTINE'
        
        # 生成建議
        recommendations = self._generate_recommendations(
            overall_threat, nearby_threats, vessel_data
        )
        
        return {
            'vessel_name': vessel_data.get('name', 'UNKNOWN'),
            'vessel_type': vessel_type,
            'vessel_position': (round(vessel_lat, 6), round(vessel_lon, 6)),
            'vessel_speed': vessel_speed,
            'vessel_heading': vessel_heading,
            'vessel_draft': vessel_draft,
            'overall_risk_score': round(overall_score, 2),
            'threat_level': overall_threat,
            'action_urgency': action_urgency,
            'nearby_warnings': nearby_threats[:5],
            'warning_count': len(nearby_threats),
            'recommendations': recommendations,
            'action_required': overall_threat in ['CRITICAL', 'HIGH'],
            'assessment_timestamp': datetime.now().isoformat(),
            'confidence': round(sum(t['certainty'] for t in nearby_threats) / max(1, len(nearby_threats)), 2)
        }
    
    def _generate_recommendations(self, threat_level, warnings, vessel_data):
        """根據威脅等級生成航海建議"""
        recommendations = []
        vessel_type = vessel_data.get('type', 'GENERAL')
        
        if threat_level == 'CRITICAL':
            recommendations.append("🚨 立即行動：改變航向至少 30 度或減速至 5 節以下")
            recommendations.append("📞 立即與港口當局、VTS 或附近船舶聯繫")
            recommendations.append("🛑 啟動應急程序，準備應急停車")
            recommendations.append("📡 將 AIS 設置為最高頻率報告（每 10 秒）")
            recommendations.append("🎯 在海圖上標記警告區域，規劃繞行路線")
            if vessel_type == 'TANKER':
                recommendations.append("⚠️ 油輪特警：減少機器負荷，提高操舵反應")
            
        elif threat_level == 'HIGH':
            recommendations.append("⚠️ 密切監測警告區域，準備改變航向")
            recommendations.append("🧭 評估替代航線，考慮繞行")
            recommendations.append("📡 增加 AIS 報告頻率至每 30 秒")
            recommendations.append("👥 通知船長與航海員，進行航海會議")
            recommendations.append("📊 檢查燃油/供應情況以應對航線延長")
        
        elif threat_level == 'MEDIUM':
            recommendations.append("💡 留意警告區域的最新資訊與氣象更新")
            recommendations.append("📍 記錄當前位置、時間與航向")
            recommendations.append("📐 在海圖上標記警告，計算安全通過的最少距離")
            recommendations.append("📡 確保 AIS 工作正常，保持標準報告頻率")
        
        elif threat_level == 'LOW':
            recommendations.append("ℹ️ 維持常規航向與速度監控")
            recommendations.append("📚 查看警告詳細內容，了解具體情況")
            recommendations.append("📊 評估是否需要進一步減速或轉向")
        
        else:  # SAFE
            recommendations.append("✅ 當前安全。保持常規監控與 AIS 報告。")
        
        # 根據警告類型的特定建議
        for warning in warnings[:2]:
            title = warning.get('warning_title', '')
            if '射擊' in title:
                recommendations.append("⚡ 特別警告：該區域有軍事射擊訓練，應盡快遠離該區")
            elif '礙航' in title:
                recommendations.append("🚧 障礙物警告：該區域有沉船/結構，應減速並提高警惕")
            elif '颶風' in title or '台風' in title:
                recommendations.append("🌪️ 氣象警告：惡劣天氣，加強固定、備妥應急措施")
            elif '淺灘' in title or '岩石' in title:
                recommendations.append("⛵ 地形危害：該區域可能淺灘或暗礁，應依海圖通過")
        
        return recommendations
    
    def assess_fleet_status(self, fleet_data, warnings_data):
        """對整個船隊進行風險評估
        
        生成艦隊級別的風險統計與優先度排序
        """
        fleet_assessment = {
            'total_vessels': len(fleet_data),
            'assessment_time': datetime.now().isoformat(),
            'threat_distribution': {
                'CRITICAL': 0,
                'HIGH': 0,
                'MEDIUM': 0,
                'LOW': 0,
                'SAFE': 0
            },
            'vessels_in_critical_danger': 0,
            'vessels_in_high_risk': 0,
            'vessels_safe': 0,
            'vessel_reports': [],
            'critical_alerts': [],
            'recommended_actions': []
        }
        
        for vessel in fleet_data:
            assessment = self.assess_vessel_threat(vessel, warnings_data)
            fleet_assessment['vessel_reports'].append(assessment)
            
            threat_level = assessment['threat_level']
            fleet_assessment['threat_distribution'][threat_level] += 1
            
            if threat_level == 'CRITICAL':
                fleet_assessment['vessels_in_critical_danger'] += 1
                fleet_assessment['critical_alerts'].append({
                    'vessel': assessment['vessel_name'],
                    'threat_level': threat_level,
                    'score': assessment['overall_risk_score'],
                    'nearest_warning': assessment['nearby_warnings'][0]['warning_title'] if assessment['nearby_warnings'] else 'N/A',
                    'distance': assessment['nearby_warnings'][0]['distance_km'] if assessment['nearby_warnings'] else None
                })
            elif threat_level == 'HIGH':
                fleet_assessment['vessels_in_high_risk'] += 1
            else:
                fleet_assessment['vessels_safe'] += 1
        
        # 排序關鍵警報（按風險分數）
        fleet_assessment['critical_alerts'].sort(
            key=lambda x: x['score'], reverse=True
        )
        
        # 生成摘要建議
        if fleet_assessment['vessels_in_critical_danger'] > 0:
            fleet_assessment['recommended_actions'].append(
                f"🚨 立即關注 {fleet_assessment['vessels_in_critical_danger']} "
                f"艘危險船舶，可能需要派遣支援"
            )
        
        if fleet_assessment['vessels_in_high_risk'] > 0:
            fleet_assessment['recommended_actions'].append(
                f"⚠️ 密切監控 {fleet_assessment['vessels_in_high_risk']} "
                f"艘高風險船舶，預備應急措施"
            )
        
        if fleet_assessment['threat_distribution']['MEDIUM'] > 0:
            fleet_assessment['recommended_actions'].append(
                f"💡 定期更新 {fleet_assessment['threat_distribution']['MEDIUM']} "
                f"艘中風險船舶的航線建議"
            )
        
        safety_percentage = round(
            (fleet_assessment['vessels_safe'] / max(1, fleet_assessment['total_vessels'])) * 100, 1
        )
        fleet_assessment['recommended_actions'].append(
            f"📊 當前艦隊安全率: {safety_percentage}%"
        )
        
        return fleet_assessment


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


# ==================== 5. 台灣航港局爬蟲 (Selenium 版本，修正動態載入) ====================
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

                        # ✅ 修改日期檢查邏輯
                        if publish_time:
                            p_date = self.parse_date(publish_time)
                            if p_date:
                                # 檢查是否為當天
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
                            # 沒有日期資訊則跳過
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
                 mail_user=None, mail_pass=None, target_email=None, 
                 enable_vessel_tracking=False):
        # ... 現有初始化代碼 ...
        
        # 新增風險評估系統
        if enable_vessel_tracking:
            self.geofence_detector = GeofenceDetector()
            self.risk_assessor = VesselRiskAssessment(self.geofence_detector)
            self.enable_vessel_tracking = True
        else:
            self.enable_vessel_tracking = False
    
    def run_all_scrapers_with_risk_assessment(self, fleet_data=None):
        """執行爬蟲並進行風險評估
        
        Args:
            fleet_data: [{
                'name': str,           # 船舶名稱
                'lat': float,
                'lon': float,
                'speed_knots': float,
                'heading': float,      # 0-360 度
                'draft_m': float,      # 吃水（米）
                'type': str            # TANKER|CONTAINER|GENERAL|etc
            }, ...]
        """
        start_time = datetime.now()
        map_path = None
        
        print(f"{'='*70}")
        print(f"🌊 海事警告監控與船舶風險評估系統")
        print(f"{'='*70}")
        
        try:
            # 1. 爬取警告
            cn_warnings = self.cn_scraper.scrape_all_bureaus()
            self.all_new_warnings.extend(cn_warnings)
            self.all_captured_data.extend(self.cn_scraper.captured_warnings_data)
            
            tw_warnings = self.tw_scraper.scrape_all_pages()
            self.all_new_warnings.extend(tw_warnings)
            self.all_captured_data.extend(self.tw_scraper.captured_warnings_data)
            
            # 2. 繪製地圖
            if self.all_captured_data and self.map_plotter:
                print("\n🗺️ 正在繪製海圖...")
                warnings_for_map = [
                    {
                        'title': w['title'],
                        'coordinates': w.get('coordinates', []),
                        'bureau': w['bureau'],
                        'source': w.get('source', 'CN_MSA'),
                        'type': 'polygon' if len(w.get('coordinates', [])) > 1 else 'point',
                        'id': w.get('id')
                    }
                    for w in self.all_captured_data
                    if w.get('coordinates')
                ]
                
                # 如果有船舶數據，添加到地圖
                if fleet_data and self.enable_vessel_tracking:
                    enhanced_plotter = EnhancedMaritimeMapPlotter()
                    for vessel in fleet_data:
                        vessel_threats = self.risk_assessor.assess_vessel_threat(
                            vessel, warnings_for_map
                        )
                        enhanced_plotter.plot_warnings_with_vessel_position(
                            warnings_for_map,
                            vessel_data={
                                'name': vessel.get('name', 'UNKNOWN'),
                                'lat': vessel.get('lat'),
                                'lon': vessel.get('lon'),
                                'speed_knots': vessel.get('speed_knots', 0),
                                'heading': vessel.get('heading', 0),
                                'threats': [w['id'] for w in vessel_threats['nearby_warnings']]
                            },
                            output_filename=f"maritime_with_{vessel.get('name', 'vessel')}.png"
                        )
                else:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    map_filename = f"maritime_warnings_{timestamp}.png"
                    map_path = self.map_plotter.plot_warnings(warnings_for_map, map_filename)
            
            # 3. 風險評估
            if self.enable_vessel_tracking and fleet_data:
                print(f"\n⚠️ 正在評估 {len(fleet_data)} 艘船舶的風險...")
                
                fleet_assessment = self.risk_assessor.assess_fleet_status(
                    fleet_data, 
                    [w for w in self.all_captured_data if w.get('coordinates')]
                )
                
                self._send_fleet_risk_report(fleet_assessment)
                
                # 發送個別船舶警報
                for vessel_report in fleet_assessment['vessel_reports']:
                    if vessel_report['action_required']:
                        self._send_vessel_risk_alert(vessel_report)
            
            # 4. 發送通知
            if self.enable_teams and self.all_captured_data:
                self.send_notifications()
            
            # 5. 生成報告
            duration = (datetime.now() - start_time).total_seconds()
            self.generate_final_report(duration, map_path)
            
        except Exception as e:
            print(f"❌ 執行過程發生錯誤: {e}")
            traceback.print_exc()
    
    def _send_vessel_risk_alert(self, assessment_data):
        """發送單艘船舶風險警報"""
        
        threat_level = assessment_data['threat_level']
        vessel_name = assessment_data['vessel_name']
        score = assessment_data['overall_risk_score']
        
        print(f"\n📢 發送船舶警報: {vessel_name} ({threat_level}, 分數: {score})")
        
        # 構建 Teams 適應卡
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"⚠️ {threat_level}",
                "weight": "Bolder",
                "size": "Large",
                "color": "Attention"
            },
            {
                "type": "TextBlock",
                "text": f"🚢 {vessel_name}",
                "size": "Large",
                "weight": "Bolder"
            },
            {
                "type": "TextBlock",
                "text": f"分數: {score}/100 | 類型: {assessment_data['vessel_type']}",
                "spacing": "Small"
            }
        ]
        
        # 位置資訊
        lat, lon = assessment_data['vessel_position']
        body_elements.append({
            "type": "TextBlock",
            "text": f"📍 位置: {lat:.4f}°N {lon:.4f}°E",
            "spacing": "Small"
        })
        
        # 附近警告
        if assessment_data['nearby_warnings']:
            body_elements.append({
                "type": "TextBlock",
                "text": "🚨 附近威脅:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for warning in assessment_data['nearby_warnings'][:3]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": f"• {warning['warning_title'][:50]}\n  距離: {warning['distance_km']} km",
                    "size": "Small",
                    "wrap": True
                })
        
        # 建議
        if assessment_data['recommendations']:
            body_elements.append({
                "type": "TextBlock",
                "text": "✅ 建議:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for rec in assessment_data['recommendations'][:3]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": rec,
                    "size": "Small"
                })
        
        # 發送
        if self.teams_notifier:
            payload = self.teams_notifier._create_adaptive_card(
                f"🚢 船舶風險警報: {threat_level}",
                body_elements
            )
            
            try:
                import requests
                requests.post(
                    self.teams_notifier.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                    verify=False
                )
                print(f"  ✅ 警報已發送")
            except Exception as e:
                print(f"  ❌ 發送失敗: {e}")
    
    def _send_fleet_risk_report(self, fleet_assessment):
        """發送艦隊風險總報告"""
        
        print(f"\n📊 發送艦隊風險報告...")
        
        body_elements = [
            {
                "type": "TextBlock",
                "text": f"艦隊總數: {fleet_assessment['total_vessels']}",
                "weight": "Bolder",
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": (
                    f"🚨 危險: {fleet_assessment['vessels_in_critical_danger']} | "
                    f"⚠️ 高風險: {fleet_assessment['vessels_in_high_risk']} | "
                    f"✅ 安全: {fleet_assessment['vessels_safe']}"
                ),
                "spacing": "Small"
            }
        ]
        
        # 詳細統計
        dist = fleet_assessment['threat_distribution']
        body_elements.append({
            "type": "TextBlock",
            "text": (
                f"風險分佈 - "
                f"CRITICAL: {dist['CRITICAL']} | "
                f"HIGH: {dist['HIGH']} | "
                f"MEDIUM: {dist['MEDIUM']}"
            ),
            "size": "Small",
            "spacing": "Small"
        })
        
        # 關鍵警報
        if fleet_assessment['critical_alerts']:
            body_elements.append({
                "type": "TextBlock",
                "text": "🚨 關鍵警報:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for alert in fleet_assessment['critical_alerts'][:5]:
                body_elements.append({
                    "type": "TextBlock",
                    "text": f"• {alert['vessel']}: {alert['threat_level']} (分數: {alert['score']})",
                    "size": "Small"
                })
        
        # 建議
        if fleet_assessment['recommended_actions']:
            body_elements.append({
                "type": "TextBlock",
                "text": "💡 建議:",
                "weight": "Bolder",
                "spacing": "Medium"
            })
            
            for action in fleet_assessment['recommended_actions']:
                body_elements.append({
                    "type": "TextBlock",
                    "text": action,
                    "size": "Small"
                })
        
        # 發送
        if self.teams_notifier:
            payload = self.teams_notifier._create_adaptive_card(
                "📊 艦隊風險評估報告",
                body_elements
            )
            
            try:
                import requests
                requests.post(
                    self.teams_notifier.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                    verify=False
                )
                print(f"  ✅ 艦隊報告已發送")
            except Exception as e:
                print(f"  ❌ 發送失敗: {e}")
# ==================== 環境變數讀取 ====================
print("📋 正在讀取環境變數...")

# ========== 必要設定 ==========
TEAMS_WEBHOOK = os.getenv("TEAMS_WEBHOOK_URL", "")
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
TARGET_EMAIL = os.getenv("TARGET_EMAIL", "")
MAIL_SMTP_SERVER = os.getenv("MAIL_SMTP_SERVER", "smtp.gmail.com")
MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "587"))

# ========== 資料庫設定 ==========
DB_FILE_PATH = os.getenv("DB_FILE_PATH", "navigation_warnings.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
MAX_BACKUP_FILES = int(os.getenv("MAX_BACKUP_FILES", "7"))

# ========== 爬蟲設定 ==========
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "3600"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# ========== 關鍵字設定 ==========
KEYWORDS_CONFIG = os.getenv("KEYWORDS_CONFIG", "keywords_config.json")

# ========== Chrome 設定 ==========
CHROME_HEADLESS = os.getenv("CHROME_HEADLESS", "true").lower() == "true"

# ========== 通知設定 ==========
ENABLE_EMAIL_NOTIFICATIONS = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "true").lower() == "true"
ENABLE_TEAMS_NOTIFICATIONS = os.getenv("ENABLE_TEAMS_NOTIFICATIONS", "true").lower() == "true"

# ========== 資料來源設定 ==========
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
        db_manager = DatabaseManager(db_name=DB_FILE_PATH)  # ✅ 改為 db_name
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
        
        # 初始化地圖繪製器
        map_plotter = None
        if MAPPING_AVAILABLE:
            print("🗺️  初始化地圖繪製器...")
            map_plotter = MaritimeMapPlotter()
        
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
        
        # ========== 繪製地圖 ==========
        map_path = None
        if all_captured_data and map_plotter:
            print("\n🗺️  正在繪製海事警告地圖...")
            warnings_for_map = [
                {
                    'title': w['title'],
                    'coordinates': w.get('coordinates', []),
                    'bureau': w['bureau'],
                    'source': w.get('source', 'CN_MSA'),
                    'type': 'polygon' if len(w.get('coordinates', [])) > 1 else 'point',
                    'id': w.get('id')
                }
                for w in all_captured_data
                if w.get('coordinates')
            ]
            
            if warnings_for_map:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                map_filename = f"maritime_warnings_{timestamp}.png"
                map_path = map_plotter.plot_warnings(warnings_for_map, map_filename)
        
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
        
        if map_path:
            print(f"🗺️  地圖檔案: {map_path}")
        
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
