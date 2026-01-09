#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資料庫管理模組 - SQLite 版本 (支援多源海事警告)
"""

import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import json
import os

class DatabaseManager:
    def __init__(self, db_name=None):
        """初始化 SQLite 資料庫"""
        # 從環境變數讀取或使用預設值
        if db_name is None:
            from dotenv import load_dotenv
            load_dotenv()
            db_name = os.getenv('DB_FILE_PATH', 'navigation_warnings.db')
        
        self.db_name = db_name
        print(f"📁 使用 SQLite 資料庫: {self.db_name}")
        self.init_database()
    
    def init_database(self):
        """初始化 SQLite 資料庫"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # 建立主表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                maritime_bureau TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT,
                publish_time TEXT,
                keywords_matched TEXT,
                scrape_time TEXT NOT NULL,
                coordinates TEXT,
                source_type TEXT DEFAULT 'CN_MSA',
                source_country TEXT DEFAULT 'CN',
                is_notified INTEGER DEFAULT 0,
                notified_time TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(maritime_bureau, title, publish_time, source_type)
            )
        ''')
        
        # 檢查是否需要新增欄位（向後相容）
        cursor.execute("PRAGMA table_info(warnings)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # 新增多源支援欄位
        if 'source_type' not in columns:
            print("🔄 新增 source_type 欄位...")
            cursor.execute('ALTER TABLE warnings ADD COLUMN source_type TEXT DEFAULT "CN_MSA"')
            conn.commit()
            print("✅ source_type 欄位新增完成")
        
        if 'source_country' not in columns:
            print("🔄 新增 source_country 欄位...")
            cursor.execute('ALTER TABLE warnings ADD COLUMN source_country TEXT DEFAULT "CN"')
            conn.commit()
            print("✅ source_country 欄位新增完成")
        
        if 'coordinates' not in columns:
            print("🔄 新增 coordinates 欄位...")
            cursor.execute('ALTER TABLE warnings ADD COLUMN coordinates TEXT')
            conn.commit()
            print("✅ coordinates 欄位新增完成")
        
        if 'created_at' not in columns:
            print("🔄 新增 created_at 欄位...")
            cursor.execute('ALTER TABLE warnings ADD COLUMN created_at TEXT')
            conn.commit()
            print("✅ created_at 欄位新增完成")
        
        if 'updated_at' not in columns:
            print("🔄 新增 updated_at 欄位...")
            cursor.execute('ALTER TABLE warnings ADD COLUMN updated_at TEXT')
            conn.commit()
            print("✅ updated_at 欄位新增完成")
        
        # 更新現有資料的 source_type 和 source_country（如果為空）
        cursor.execute('''
            UPDATE warnings 
            SET source_type = 'CN_MSA', source_country = 'CN'
            WHERE source_type IS NULL OR source_type = ''
        ''')
        conn.commit()
        
        # 建立索引以提升查詢效能
        indexes = [
            ('idx_is_notified', 'is_notified'),
            ('idx_scrape_time', 'scrape_time'),
            ('idx_maritime_bureau', 'maritime_bureau'),
            ('idx_coordinates', 'coordinates'),
            ('idx_source_type', 'source_type'),
            ('idx_source_country', 'source_country'),
            ('idx_source_bureau', 'source_type, maritime_bureau')
        ]
        
        for index_name, index_columns in indexes:
            cursor.execute(f'''
                CREATE INDEX IF NOT EXISTS {index_name} 
                ON warnings({index_columns})
            ''')
        
        conn.commit()
        conn.close()
        print(f"✅ SQLite 資料庫初始化完成")
    
    def save_warning(self, data, source_type="CN_MSA"):
        """
        儲存警告資料到資料庫 (支援多源)
        data: tuple (maritime_bureau, title, link, publish_time, keywords_matched, scrape_time, coordinates)
        source_type: 'CN_MSA' (中國海事局) 或 'TW_MPB' (台灣航港局)
        返回: (is_new: bool, warning_id: int or None)
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            # 處理座標資料
            coordinates = data[6] if len(data) > 6 else None
            
            # 如果 coordinates 是 list，轉換為 JSON 字串
            if isinstance(coordinates, list):
                coordinates = json.dumps(coordinates, ensure_ascii=False)
            
            # 根據來源類型設定國家代碼
            source_country = "TW" if source_type == "TW_MPB" else "CN"
            
            # 當前時間
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute('''
                INSERT OR IGNORE INTO warnings 
                (maritime_bureau, title, link, publish_time, keywords_matched, scrape_time, 
                 coordinates, source_type, source_country, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data[0], data[1], data[2], data[3], data[4], data[5], 
                  coordinates, source_type, source_country, current_time, current_time))
            
            conn.commit()
            
            # 檢查是否真的插入了新資料
            if cursor.rowcount > 0:
                warning_id = cursor.lastrowid
                source_flag = "🇹🇼" if source_type == "TW_MPB" else "🇨🇳"
                print(f"  💾 {source_flag} 新資料已儲存 (ID: {warning_id})")
                return True, warning_id
            else:
                # 資料已存在，獲取現有 ID 並更新座標（如果有新座標）
                cursor.execute('''
                    SELECT id, coordinates FROM warnings 
                    WHERE maritime_bureau=? AND title=? AND publish_time=? AND source_type=?
                ''', (data[0], data[1], data[3], source_type))
                result = cursor.fetchone()
                
                if result:
                    existing_id = result[0]
                    existing_coords = result[1]
                    
                    # 如果有新座標且舊資料沒有座標，則更新
                    if coordinates and not existing_coords:
                        cursor.execute('''
                            UPDATE warnings 
                            SET coordinates = ?, updated_at = ?
                            WHERE id = ?
                        ''', (coordinates, current_time, existing_id))
                        conn.commit()
                        source_flag = "🇹🇼" if source_type == "TW_MPB" else "🇨🇳"
                        print(f"  🔄 {source_flag} 已更新座標資料 (ID: {existing_id})")
                    
                    return False, existing_id
                
                return False, None
                
        except Exception as e:
            print(f"❌ 資料庫儲存錯誤: {e}")
            import traceback
            traceback.print_exc()
            return False, None
        finally:
            conn.close()
    
    def get_unnotified_warnings(self, source_type=None):
        """
        獲取尚未通知的警告（含座標）
        source_type: None (全部), 'CN_MSA', 'TW_MPB'
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            if source_type:
                cursor.execute('''
                    SELECT id, maritime_bureau, title, link, publish_time, 
                           keywords_matched, scrape_time, coordinates, source_type, source_country
                    FROM warnings
                    WHERE is_notified = 0 AND source_type = ?
                    ORDER BY scrape_time DESC
                ''', (source_type,))
            else:
                cursor.execute('''
                    SELECT id, maritime_bureau, title, link, publish_time, 
                           keywords_matched, scrape_time, coordinates, source_type, source_country
                    FROM warnings
                    WHERE is_notified = 0
                    ORDER BY scrape_time DESC
                ''')
            
            results = cursor.fetchall()
            return results
            
        except Exception as e:
            print(f"❌ 查詢未通知警告時出錯: {e}")
            return []
        finally:
            conn.close()
    
    def mark_as_notified(self, warning_id):
        """標記警告為已通知"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute('''
                UPDATE warnings
                SET is_notified = 1, notified_time = ?, updated_at = ?
                WHERE id = ?
            ''', (current_time, current_time, warning_id))
            
            conn.commit()
            
            # 檢查是否真的更新了
            if cursor.rowcount == 0:
                print(f"⚠️ 警告 ID {warning_id} 不存在或已標記")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ 標記通知狀態時出錯: {e}")
            return False
        finally:
            conn.close()
    
    def get_all_warnings(self, limit=None, source_type=None):
        """
        獲取所有警告
        source_type: None (全部), 'CN_MSA', 'TW_MPB'
        """
        conn = sqlite3.connect(self.db_name)
        
        try:
            if source_type:
                query = 'SELECT * FROM warnings WHERE source_type = ? ORDER BY scrape_time DESC'
                params = (source_type,)
            else:
                query = 'SELECT * FROM warnings ORDER BY scrape_time DESC'
                params = ()
            
            if limit:
                query += f' LIMIT {limit}'
            
            if params:
                df = pd.read_sql_query(query, conn, params=params)
            else:
                df = pd.read_sql_query(query, conn)
            
            return df
            
        except Exception as e:
            print(f"❌ 查詢所有警告時出錯: {e}")
            return pd.DataFrame()
        finally:
            conn.close()
    
    def get_warnings_with_coordinates(self, source_type=None):
        """
        獲取所有含座標的警告
        source_type: None (全部), 'CN_MSA', 'TW_MPB'
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            if source_type:
                cursor.execute('''
                    SELECT id, maritime_bureau, title, link, publish_time, 
                           keywords_matched, scrape_time, coordinates, source_type, source_country
                    FROM warnings
                    WHERE coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]'
                    AND source_type = ?
                    ORDER BY scrape_time DESC
                ''', (source_type,))
            else:
                cursor.execute('''
                    SELECT id, maritime_bureau, title, link, publish_time, 
                           keywords_matched, scrape_time, coordinates, source_type, source_country
                    FROM warnings
                    WHERE coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]'
                    ORDER BY scrape_time DESC
                ''')
            
            results = cursor.fetchall()
            
            # 解析座標 JSON
            parsed_results = []
            for row in results:
                row_list = list(row)
                try:
                    if row_list[7]:  # coordinates 欄位
                        row_list[7] = json.loads(row_list[7])
                except:
                    row_list[7] = []
                parsed_results.append(tuple(row_list))
            
            return parsed_results
            
        except Exception as e:
            print(f"❌ 查詢含座標警告時出錯: {e}")
            return []
        finally:
            conn.close()
    
    def export_to_excel(self, filename=None, source_type=None):
        """
        匯出資料到 Excel（含座標解析和多源支援）
        source_type: None (全部), 'CN_MSA', 'TW_MPB'
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            source_suffix = f"_{source_type}" if source_type else "_ALL"
            filename = f'navigation_warnings{source_suffix}_{timestamp}.xlsx'
        
        df = self.get_all_warnings(source_type=source_type)
        
        if not df.empty:
            try:
                # 解析座標欄位
                def parse_coordinates(coord_str):
                    if pd.isna(coord_str) or coord_str == '' or coord_str == '[]':
                        return '無座標'
                    try:
                        coords = json.loads(coord_str)
                        if not coords:
                            return '無座標'
                        # 格式化顯示前3個座標
                        coord_text = '\n'.join([f"({c[0]:.4f}°, {c[1]:.4f}°)" for c in coords[:3]])
                        if len(coords) > 3:
                            coord_text += f"\n...還有 {len(coords)-3} 個座標"
                        return coord_text
                    except:
                        return '座標格式錯誤'
                
                # 來源標記
                def format_source(row):
                    if row['source_type'] == 'TW_MPB':
                        return f"🇹🇼 台灣航港局"
                    else:
                        return f"🇨🇳 中國海事局"
                
                df['coordinates_display'] = df['coordinates'].apply(parse_coordinates)
                df['source_display'] = df.apply(format_source, axis=1)
                
                # 重新排序欄位
                columns_order = [
                    'id', 'source_display', 'maritime_bureau', 'title', 'link', 'publish_time',
                    'keywords_matched', 'coordinates_display', 'scrape_time',
                    'is_notified', 'notified_time'
                ]
                
                # 只選擇存在的欄位
                columns_order = [col for col in columns_order if col in df.columns]
                df = df[columns_order]
                
                # 重新命名欄位（中文）
                df.rename(columns={
                    'id': 'ID',
                    'source_display': '資料來源',
                    'maritime_bureau': '發布單位',
                    'title': '標題',
                    'link': '連結',
                    'publish_time': '發布時間',
                    'keywords_matched': '關鍵字',
                    'coordinates_display': '座標',
                    'scrape_time': '抓取時間',
                    'is_notified': '已通知',
                    'notified_time': '通知時間'
                }, inplace=True)
                
                # 儲存到 Excel
                with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='航行警告')
                    
                    # 調整欄寬
                    worksheet = writer.sheets['航行警告']
                    from openpyxl.utils import get_column_letter
                    for idx, col in enumerate(df.columns, 1):
                        max_length = max(
                            df[col].astype(str).apply(len).max(),
                            len(col)
                        )
                        column_letter = get_column_letter(idx)
                        worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)
                
                source_desc = {
                    'CN_MSA': '中國海事局',
                    'TW_MPB': '台灣航港局',
                    None: '多源整合'
                }.get(source_type, '未知來源')
                
                print(f"✅ {source_desc} Excel 檔案已儲存: {filename}")
                return True
                
            except Exception as e:
                print(f"❌ Excel 匯出失敗: {e}")
                import traceback
                traceback.print_exc()
                return False
        else:
            print("⚠️ 沒有資料可以匯出")
            return False
    
    def get_statistics(self):
        """獲取統計資訊（含多源統計）"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            # 總警告數
            cursor.execute('SELECT COUNT(*) FROM warnings')
            total = cursor.fetchone()[0]
            
            # 各來源統計
            cursor.execute('''
                SELECT source_type, source_country, COUNT(*) as count
                FROM warnings
                GROUP BY source_type, source_country
                ORDER BY count DESC
            ''')
            source_stats = cursor.fetchall()
            
            # 已通知數
            cursor.execute('SELECT COUNT(*) FROM warnings WHERE is_notified = 1')
            notified = cursor.fetchone()[0]
            
            # 未通知數
            cursor.execute('SELECT COUNT(*) FROM warnings WHERE is_notified = 0')
            unnotified = cursor.fetchone()[0]
            
            # 含座標數
            cursor.execute('''
                SELECT COUNT(*) FROM warnings 
                WHERE coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]'
            ''')
            with_coords = cursor.fetchone()[0]
            
            # 各來源含座標統計
            cursor.execute('''
                SELECT source_type, COUNT(*) as count
                FROM warnings 
                WHERE coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]'
                GROUP BY source_type
                ORDER BY count DESC
            ''')
            coords_by_source = cursor.fetchall()
            
            # 總座標點數
            cursor.execute('''
                SELECT coordinates FROM warnings 
                WHERE coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]'
            ''')
            total_coord_points = 0
            for row in cursor.fetchall():
                try:
                    coords = json.loads(row[0])
                    total_coord_points += len(coords)
                except:
                    pass
            
            # 各海事局統計（按來源分組）
            cursor.execute('''
                SELECT source_type, maritime_bureau, COUNT(*) as count
                FROM warnings
                GROUP BY source_type, maritime_bureau
                ORDER BY source_type, count DESC
            ''')
            bureau_stats = cursor.fetchall()
            
            # 各關鍵字統計
            cursor.execute('''
                SELECT keywords_matched, COUNT(*) as count
                FROM warnings
                WHERE keywords_matched IS NOT NULL AND keywords_matched != ''
                GROUP BY keywords_matched
                ORDER BY count DESC
            ''')
            keyword_stats = cursor.fetchall()
            
            # 最近7天統計（按來源分組）
            cursor.execute('''
                SELECT DATE(scrape_time) as date, source_type, COUNT(*) as count
                FROM warnings
                WHERE scrape_time >= datetime('now', '-7 days')
                GROUP BY DATE(scrape_time), source_type
                ORDER BY date DESC, source_type
            ''')
            recent_stats = cursor.fetchall()
            
            return {
                'total': total,
                'source_stats': source_stats,
                'notified': notified,
                'unnotified': unnotified,
                'with_coordinates': with_coords,
                'coords_by_source': coords_by_source,
                'total_coordinate_points': total_coord_points,
                'bureau_stats': bureau_stats,
                'keyword_stats': keyword_stats,
                'recent_stats': recent_stats
            }
            
        except Exception as e:
            print(f"❌ 獲取統計資訊時出錯: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            conn.close()
    
    def print_statistics(self):
        """列印統計資訊（多源版本）"""
        stats = self.get_statistics()
        
        if stats:
            print("\n" + "=" * 60)
            print("📊 多源海事警告資料庫統計")
            print("=" * 60)
            print(f"總警告數: {stats['total']}")
            
            # 各來源統計
            if stats['source_stats']:
                print("\n各來源統計:")
                for source_type, source_country, count in stats['source_stats']:
                    flag = "🇹🇼" if source_country == "TW" else "🇨🇳"
                    source_name = "台灣航港局" if source_type == "TW_MPB" else "中國海事局"
                    print(f"  {flag} {source_name}: {count} 筆")
            
            print(f"\n通知狀態:")
            print(f"  已通知: {stats['notified']}")
            print(f"  未通知: {stats['unnotified']}")
            
            if stats['total'] > 0:
                coord_percentage = stats['with_coordinates'] / stats['total'] * 100
                print(f"\n座標資訊:")
                print(f"  含座標: {stats['with_coordinates']} ({coord_percentage:.1f}%)")
                print(f"  總座標點數: {stats['total_coordinate_points']}")
                
                # 各來源座標統計
                if stats['coords_by_source']:
                    print("  各來源含座標統計:")
                    for source_type, count in stats['coords_by_source']:
                        flag = "🇹🇼" if source_type == "TW_MPB" else "🇨🇳"
                        source_name = "台灣航港局" if source_type == "TW_MPB" else "中國海事局"
                        print(f"    {flag} {source_name}: {count} 筆")
            
            if stats['recent_stats']:
                print("\n最近7天新增 (按來源):")
                current_date = None
                for date, source_type, count in stats['recent_stats']:
                    if date != current_date:
                        print(f"  {date}:")
                        current_date = date
                    flag = "🇹🇼" if source_type == "TW_MPB" else "🇨🇳"
                    source_name = "台灣航港局" if source_type == "TW_MPB" else "中國海事局"
                    print(f"    {flag} {source_name}: {count} 筆")
            
            if stats['bureau_stats']:
                print("\n各發布單位警告數 (前10名):")
                cn_bureaus = [(b, c) for s, b, c in stats['bureau_stats'] if s == 'CN_MSA'][:5]
                tw_bureaus = [(b, c) for s, b, c in stats['bureau_stats'] if s == 'TW_MPB'][:5]
                
                if cn_bureaus:
                    print("  🇨🇳 中國海事局:")
                    for bureau, count in cn_bureaus:
                        print(f"    {bureau}: {count}")
                
                if tw_bureaus:
                    print("  🇹🇼 台灣航港局:")
                    for bureau, count in tw_bureaus:
                        print(f"    {bureau}: {count}")
            
            if stats['keyword_stats']:
                print("\n關鍵字匹配統計 (前10名):")
                for keyword, count in stats['keyword_stats'][:10]:
                    print(f"  {keyword}: {count}")
            
            print("=" * 60)
    
    def cleanup_old_records(self, days=30, source_type=None):
        """
        清理超過指定天數的舊記錄
        source_type: None (全部), 'CN_MSA', 'TW_MPB'
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            if source_type:
                cursor.execute('''
                    DELETE FROM warnings
                    WHERE scrape_time < datetime('now', '-' || ? || ' days')
                    AND source_type = ?
                ''', (days, source_type))
                source_desc = "台灣航港局" if source_type == "TW_MPB" else "中國海事局"
            else:
                cursor.execute('''
                    DELETE FROM warnings
                    WHERE scrape_time < datetime('now', '-' || ? || ' days')
                ''', (days,))
                source_desc = "全部來源"
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            print(f"✅ 已清理 {source_desc} {deleted_count} 筆超過 {days} 天的舊記錄")
            return deleted_count
            
        except Exception as e:
            print(f"❌ 清理舊記錄時出錯: {e}")
            return 0
        finally:
            conn.close()
    
    def backup_database(self, backup_path=None):
        """備份資料庫"""
        if backup_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f'backup_{self.db_name}_{timestamp}'
        
        try:
            import shutil
            shutil.copy2(self.db_name, backup_path)
            print(f"✅ 資料庫已備份至: {backup_path}")
            return True
        except Exception as e:
            print(f"❌ 備份失敗: {e}")
            return False
    
    def get_source_summary(self):
        """獲取各來源摘要資訊"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT 
                    source_type,
                    source_country,
                    COUNT(*) as total_count,
                    SUM(CASE WHEN is_notified = 1 THEN 1 ELSE 0 END) as notified_count,
                    SUM(CASE WHEN is_notified = 0 THEN 1 ELSE 0 END) as unnotified_count,
                    SUM(CASE WHEN coordinates IS NOT NULL AND coordinates != '' AND coordinates != '[]' THEN 1 ELSE 0 END) as with_coords_count,
                    MAX(scrape_time) as latest_scrape
                FROM warnings
                GROUP BY source_type, source_country
                ORDER BY total_count DESC
            ''')
            
            results = cursor.fetchall()
            
            summary = {}
            for row in results:
                source_type, source_country, total, notified, unnotified, with_coords, latest = row
                
                summary[source_type] = {
                    'country': source_country,
                    'total': total,
                    'notified': notified,
                    'unnotified': unnotified,
                    'with_coordinates': with_coords,
                    'latest_scrape': latest,
                    'flag': "🇹🇼" if source_country == "TW" else "🇨🇳",
                    'name': "台灣航港局" if source_type == "TW_MPB" else "中國海事局"
                }
            
            return summary
            
        except Exception as e:
            print(f"❌ 獲取來源摘要時出錯: {e}")
            return {}
        finally:
            conn.close()
    
    def close(self):
        """關閉資料庫連線（SQLite 不需要，但保留介面一致性）"""
        pass


if __name__ == "__main__":
    # 測試多源資料庫管理功能
    try:
        print("🧪 測試多源 SQLite 資料庫管理功能")
        print("=" * 60)
        
        db = DatabaseManager()
        
        # 顯示統計資訊
        db.print_statistics()
        
        # 顯示各來源摘要
        summary = db.get_source_summary()
        if summary:
            print(f"\n📋 各來源摘要:")
            for source_type, info in summary.items():
                print(f"  {info['flag']} {info['name']}: {info['total']} 筆 (未通知: {info['unnotified']})")
        
        # 顯示未通知的警告
        unnotified_cn = db.get_unnotified_warnings('CN_MSA')
        unnotified_tw = db.get_unnotified_warnings('TW_MPB')
        print(f"\n🇨🇳 中國海事局未通知: {len(unnotified_cn)} 筆")
        print(f"🇹🇼 台灣航港局未通知: {len(unnotified_tw)} 筆")
        
        # 顯示含座標的警告
        with_coords_cn = db.get_warnings_with_coordinates('CN_MSA')
        with_coords_tw = db.get_warnings_with_coordinates('TW_MPB')
        print(f"\n🇨🇳 中國海事局含座標: {len(with_coords_cn)} 筆")
        print(f"🇹🇼 台灣航港局含座標: {len(with_coords_tw)} 筆")
        
        print("\n" + "=" * 60)
        print("✅ 多源資料庫測試完成！")
        
    except Exception as e:
        print(f"\n❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
