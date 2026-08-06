#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地理圍欄與船舶風險評估模組
可直接集成到現有航海警告監控系統

功能:
- 座標驗證與聚集
- 地理圍欄檢測 (Point-in-Polygon)
- 大圓距離計算
- 智能風險評分
- 艦隊級別風險評估
"""

import json
from datetime import datetime
from math import radians, cos, sin, asin, sqrt, atan2, degrees
from typing import Dict, List, Tuple, Optional

try:
    from shapely.geometry import Point, Polygon
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False
    print("⚠️ Shapely 庫未安裝。請執行: pip install shapely")


class CoordinateValidatorExtended:
    """增強型座標驗證與轉換"""

    @staticmethod
    def haversine_distance(coord1: Tuple[float, float],
                          coord2: Tuple[float, float]) -> float:
        """
        計算兩座標間的大圓距離（公里）

        使用 Haversine 公式計算球面上兩點間的距離

        Args:
            coord1: (緯度, 經度)
            coord2: (緯度, 經度)

        Returns:
            距離（公里）

        Example:
            >>> dist = CoordinateValidatorExtended.haversine_distance(
            ...     (22.3, 114.0),  # 香港
            ...     (25.0, 121.5)   # 台灣
            ... )
            >>> print(f"距離: {dist:.1f} km")
        """
        lat1, lon1 = coord1
        lat2, lon2 = coord2

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        distance_km = 6371 * c  # 地球平均半徑 6371 km

        return distance_km

    @staticmethod
    def calculate_bearing(coord1: Tuple[float, float],
                         coord2: Tuple[float, float]) -> float:
        """
        計算從 coord1 到 coord2 的方位角（0-360度）

        0度 = 正北
        90度 = 正東
        180度 = 正南
        270度 = 正西

        Args:
            coord1: 起點 (緯度, 經度)
            coord2: 終點 (緯度, 經度)

        Returns:
            方位角 (0-360)
        """
        lat1, lon1 = coord1
        lat2, lon2 = coord2

        dlon = radians(lon2 - lon1)
        y = sin(dlon) * cos(radians(lat2))
        x = cos(radians(lat1)) * sin(radians(lat2)) - sin(radians(lat1)) * cos(radians(lat2)) * cos(dlon)

        bearing = degrees(atan2(y, x))
        bearing = (bearing + 360) % 360

        return bearing

    @staticmethod
    def cluster_nearby_coordinates(coordinates: List[Tuple[float, float]],
                                  threshold_km: float = 1.0) -> List[Tuple[float, float]]:
        """
        聚集相鄰座標點（去除重複提取）

        使用凝聚式聚類算法：
        1. 初始化每個座標為獨立聚類
        2. 反復合併距離小於閾值的聚類
        3. 回傳各聚類的質心

        Args:
            coordinates: 座標列表
            threshold_km: 聚類距離閾值

        Returns:
            聚類後的質心列表
        """
        if not coordinates or len(coordinates) <= 1:
            return coordinates

        clusters = [[coord] for coord in coordinates]

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

    @staticmethod
    def validate_coordinate_precision(lat: float, lon: float) -> Tuple[bool, str]:
        """
        驗證座標精度

        要求座標精確到小數點後至少 4 位（約 10 公尺精度）

        Args:
            lat: 緯度
            lon: 經度

        Returns:
            (是否有效, 診斷信息)
        """
        # 基本範圍檢查
        if lat < -90 or lat > 90 or lon < -180 or lon > 180:
            return False, "座標超出全球範圍"

        # 精度檢查
        if lat == int(lat) or lon == int(lon):
            return False, "座標精度不足（應為小數點後至少 4 位）"

        # 邊界值檢查
        if (lat in [-90, 0, 90]) or (lon in [-180, 0, 180]):
            return False, "座標疑似為邊界值（掃描錯誤可能性高）"

        return True, "座標精度合格"


class GeofenceDetector:
    """地理圍欄與風險區域檢測"""

    def __init__(self):
        if not SHAPELY_AVAILABLE:
            raise ImportError("Shapely 庫未安裝，無法使用地理圍欄功能")

        self.Point = Point
        self.Polygon = Polygon

    def is_point_in_polygon(self, point_lat: float, point_lon: float,
                           polygon_coords: List[Tuple[float, float]]) -> bool:
        """
        判斷點是否在多邊形內

        使用 Shapely 庫的 Ray Casting 算法

        Args:
            point_lat: 點的緯度
            point_lon: 點的經度
            polygon_coords: 多邊形座標列表 [(lat, lon), ...]

        Returns:
            True 若點在多邊形內
        """
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            return polygon.contains(point)
        except Exception as e:
            print(f"⚠️ 多邊形檢測失敗: {e}")
            return False

    def point_to_polygon_distance(self, point_lat: float, point_lon: float,
                                 polygon_coords: List[Tuple[float, float]]) -> float:
        """
        計算點到多邊形的最短距離

        Args:
            point_lat: 點的緯度
            point_lon: 點的經度
            polygon_coords: 多邊形座標列表

        Returns:
            距離（公里）
        """
        try:
            point = self.Point(point_lon, point_lat)
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            # 距離單位為度數，轉換為公里
            distance_degrees = point.distance(polygon)
            distance_km = distance_degrees * 111 * cos(radians(point_lat))

            return distance_km
        except Exception as e:
            print(f"⚠️ 距離計算失敗: {e}")
            return float('inf')

    def detect_zone_threat(self, vessel_lat: float, vessel_lon: float,
                          warning_data: Dict, buffer_km: float = 5.0) -> Dict:
        """
        判定船舶對警告區域的威脅等級

        Args:
            vessel_lat: 船舶緯度
            vessel_lon: 船舶經度
            warning_data: 警告資訊字典 {
                'type': 'point' 或 'polygon',
                'coordinates': [(lat, lon), ...],
                'title': str
            }
            buffer_km: 緩衝距離（公里）

        Returns:
            {
                'threat_level': 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'SAFE',
                'distance_km': float,
                'is_in_zone': bool,
                'certainty': 0-1
            }
        """
        coords = warning_data.get('coordinates', [])
        if not coords:
            return {
                'threat_level': 'SAFE',
                'distance_km': float('inf'),
                'is_in_zone': False,
                'certainty': 0.0
            }

        warn_type = warning_data.get('type', 'point')

        # 情況 1: 點狀警告
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

        # 情況 2: 多邊形警告
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

    def get_warning_polygon_area(self, polygon_coords: List[Tuple[float, float]]) -> Optional[float]:
        """
        計算多邊形警告區域面積

        Args:
            polygon_coords: 多邊形座標列表

        Returns:
            面積（平方公里），若計算失敗回傳 None
        """
        try:
            polygon = self.Polygon([(c[1], c[0]) for c in polygon_coords])
            area_sq_degrees = polygon.area
            area_sq_km = area_sq_degrees * 12100  # 1°² ≈ 12100 km²
            return round(area_sq_km, 2)
        except Exception as e:
            print(f"⚠️ 面積計算失敗: {e}")
            return None


class VesselRiskAssessment:
    """船舶碰撞與運營風險智能評分系統"""

    def __init__(self, geofence_detector: Optional[GeofenceDetector] = None):
        self.geofence = geofence_detector or GeofenceDetector()
        self.coord_validator = CoordinateValidatorExtended()

        # 船舶類型敏感度因子（敏感度越高，風險越高）
        self.type_factors = {
            'PASSENGER': 1.4,   # 客輪
            'TANKER': 1.3,      # 油輪
            'CONTAINER': 1.2,   # 貨櫃船
            'GENERAL': 1.0,     # 雜貨船
            'BULK': 0.9,        # 散貨船
            'FISHING': 0.7      # 漁船
        }

    def assess_vessel_threat(self, vessel_data: Dict,
                            warnings_data: List[Dict]) -> Dict:
        """
        對單艘船舶進行綜合威脅評估

        考慮因素：
        - 距離危險區的遠近
        - 船舶速度與接近速度
        - 船舶類型敏感性
        - 吃水影響（淺灘風險）
        - 警告區域類型

        Args:
            vessel_data: {
                'name': str,
                'lat': float,
                'lon': float,
                'speed_knots': float,
                'heading': float,  # 0-360
                'draft_m': float,
                'type': str  # TANKER|CONTAINER|GENERAL|...
            }
            warnings_data: 警告列表

        Returns:
            {
                'vessel_name': str,
                'overall_risk_score': 0-100,
                'threat_level': str,
                'action_urgency': str,
                'nearby_warnings': [...],
                'recommendations': [...],
                'action_required': bool
            }
        """
        vessel_lat = vessel_data.get('lat')
        vessel_lon = vessel_data.get('lon')
        vessel_speed = vessel_data.get('speed_knots', 0)
        vessel_draft = vessel_data.get('draft_m', 0)
        vessel_type = vessel_data.get('type', 'GENERAL')
        vessel_heading = vessel_data.get('heading', 0)

        # 敏感度因子
        type_factor = self.type_factors.get(vessel_type, 1.0)
        draft_factor = 1.0 + (vessel_draft / 15.0) if vessel_draft > 0 else 1.0

        nearby_threats = []
        weighted_threat_score = 0
        total_weight = 0

        for idx, warning in enumerate(warnings_data):
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
                'LOW': 25
            }
            base_score = threat_scores.get(threat_level, 0)

            distance = threat_assessment.get('distance_km', float('inf'))
            certainty = threat_assessment.get('certainty', 0.5)

            # 計算接近因子
            warning_coords = warning.get('coordinates', [])
            approach_factor = 0.5
            if warning_coords:
                warning_center = (
                    sum(c[0] for c in warning_coords) / len(warning_coords),
                    sum(c[1] for c in warning_coords) / len(warning_coords)
                )

                bearing_to_warning = self.coord_validator.calculate_bearing(
                    (vessel_lat, vessel_lon), warning_center
                )

                heading_diff = abs(vessel_heading - bearing_to_warning)
                heading_diff = min(heading_diff, 360 - heading_diff)
                approach_factor = 1 - (heading_diff / 180)
                approach_factor = max(0, approach_factor)

            # 警告類型倍數
            warning_title = warning.get('title', '').lower()
            if '射擊' in warning_title:
                type_multiplier = 1.5
            elif '礙航' in warning_title:
                type_multiplier = 1.3
            elif '颶風' in warning_title or '台風' in warning_title:
                type_multiplier = 1.2
            else:
                type_multiplier = 1.0

            # 綜合分數
            adjusted_score = base_score * type_factor * draft_factor * type_multiplier
            distance_penalty = max(0, 1 - (distance / 20))
            approach_bonus = approach_factor * 0.3
            final_score = (adjusted_score * distance_penalty + approach_bonus * 50) * certainty

            threat_assessment['warning_title'] = warning.get('title', 'Unknown')
            threat_assessment['warning_id'] = warning.get('id', idx)
            threat_assessment['final_score'] = round(final_score, 2)

            nearby_threats.append(threat_assessment)
            weighted_threat_score += final_score
            total_weight += 1

        # 排序威脅
        nearby_threats.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        # 計算整體風險分
        overall_score = min(100, weighted_threat_score / max(1, total_weight)) if total_weight > 0 else 0

        # 判定威脅等級
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
            'overall_risk_score': round(overall_score, 2),
            'threat_level': overall_threat,
            'action_urgency': action_urgency,
            'nearby_warnings': nearby_threats[:5],
            'warning_count': len(nearby_threats),
            'recommendations': recommendations,
            'action_required': overall_threat in ['CRITICAL', 'HIGH'],
            'assessment_timestamp': datetime.now().isoformat()
        }

    def _generate_recommendations(self, threat_level: str,
                                 warnings: List[Dict],
                                 vessel_data: Dict) -> List[str]:
        """根據威脅等級生成航海建議"""
        recommendations = []
        vessel_type = vessel_data.get('type', 'GENERAL')

        if threat_level == 'CRITICAL':
            recommendations.append("🚨 立即改變航向至少 30 度或減速至 5 節以下")
            recommendations.append("📞 立即與港口當局、VTS 聯繫")
            recommendations.append("🛑 啟動應急程序，準備應急停車")
            recommendations.append("📡 將 AIS 設置為最高頻率報告")
            if vessel_type == 'TANKER':
                recommendations.append("⚠️ 油輪特警：減少機器負荷")

        elif threat_level == 'HIGH':
            recommendations.append("⚠️ 密切監測警告區域，準備改變航向")
            recommendations.append("🧭 評估替代航線")
            recommendations.append("📡 增加 AIS 報告頻率至每 30 秒")
            recommendations.append("👥 通知船長與航海員")

        elif threat_level == 'MEDIUM':
            recommendations.append("💡 留意警告區域的最新資訊")
            recommendations.append("📍 記錄當前位置與時間")
            recommendations.append("📐 計算安全通過的最少距離")

        else:
            recommendations.append("ℹ️ 維持常規監控")

        # 特定警告建議
        for warning in warnings[:2]:
            title = warning.get('warning_title', '')
            if '射擊' in title:
                recommendations.append("⚡ 軍事射擊訓練，應盡快遠離")
            elif '礙航' in title:
                recommendations.append("🚧 該區域有障礙物，應減速行駛")

        return recommendations

    def assess_fleet_status(self, fleet_data: List[Dict],
                           warnings_data: List[Dict]) -> Dict:
        """對整個船隊進行風險評估"""

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
            'critical_alerts': []
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
                    'score': assessment['overall_risk_score']
                })
            elif threat_level == 'HIGH':
                fleet_assessment['vessels_in_high_risk'] += 1
            else:
                fleet_assessment['vessels_safe'] += 1

        return fleet_assessment


# 使用示例
if __name__ == "__main__":
    print("地理圍欄與風險評估模組示例\n")

    # 初始化檢測器
    try:
        geofence = GeofenceDetector()
        risk_assessor = VesselRiskAssessment(geofence)
    except ImportError as e:
        print(f"❌ {e}")
        exit(1)

    # 示例船舶數據
    fleet = [
        {
            'name': 'VICTORY',
            'type': 'TANKER',
            'lat': 22.5,
            'lon': 113.5,
            'speed_knots': 12.5,
            'heading': 45,
            'draft_m': 10.5
        }
    ]

    # 示例警告數據
    warnings = [
        {
            'id': 1,
            'title': '射擊操演區',
            'type': 'polygon',
            'coordinates': [(22.4, 113.4), (22.5, 113.5), (22.6, 113.4)]
        }
    ]

    # 進行風險評估
    print("🔍 進行風險評估...\n")
    assessment = risk_assessor.assess_vessel_threat(fleet[0], warnings)

    print(f"船舶: {assessment['vessel_name']}")
    print(f"威脅等級: {assessment['threat_level']}")
    print(f"風險分數: {assessment['overall_risk_score']}/100")
    print(f"附近警告: {assessment['warning_count']} 個")
    print(f"\n建議:")
    for rec in assessment['recommendations']:
        print(f"  {rec}")