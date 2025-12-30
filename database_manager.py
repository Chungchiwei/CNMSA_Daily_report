import sqlite3
from datetime import datetime
import pandas as pd

class DatabaseManager:
    def __init__(self, db_name='navigation_warnings.db'):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """初始化 SQLite 資料庫"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                maritime_bureau TEXT,
                title TEXT,
                link TEXT,
                publish_time TEXT,
                keywords_matched TEXT,
                scrape_time TEXT,
                is_notified INTEGER DEFAULT 0,
                notified_time TEXT,
                UNIQUE(maritime_bureau, title, publish_time)
            )
        ''')
        
        # 建立索引以提升查詢效能
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_is_notified 
            ON warnings(is_notified)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_scrape_time 
            ON warnings(scrape_time)
        ''')
        
        conn.commit()
        conn.close()
        print(f"✓ 資料庫初始化完成: {self.db_name}")
    
    def save_warning(self, data):
        """
        儲存警告資料到資料庫
        data: tuple (maritime_bureau, title, link, publish_time, keywords_matched, scrape_time)
        返回: (success: bool, warning_id: int or None)
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO warnings 
                (maritime_bureau, title, link, publish_time, keywords_matched, scrape_time)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', data)
            
            conn.commit()
            
            # 檢查是否真的插入了新資料
            if cursor.rowcount > 0:
                warning_id = cursor.lastrowid
                print(f"✓ 新警告已儲存 (ID: {warning_id}): {data[1][:50]}...")
                return True, warning_id
            else:
                # 資料已存在，獲取現有 ID
                cursor.execute('''
                    SELECT id FROM warnings 
                    WHERE maritime_bureau=? AND title=? AND publish_time=?
                ''', (data[0], data[1], data[3]))
                result = cursor.fetchone()
                if result:
                    return False, result[0]
                return False, None
                
        except Exception as e:
            print(f"✗ 資料庫儲存錯誤: {e}")
            return False, None
        finally:
            conn.close()
    
    def get_unnotified_warnings(self):
        """獲取尚未通知的警告"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT id, maritime_bureau, title, link, publish_time, keywords_matched, scrape_time
                FROM warnings
                WHERE is_notified = 0
                ORDER BY scrape_time DESC
            ''')
            
            results = cursor.fetchall()
            return results
            
        except Exception as e:
            print(f"✗ 查詢未通知警告時出錯: {e}")
            return []
        finally:
            conn.close()
    
    def mark_as_notified(self, warning_id):
        """標記警告為已通知"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE warnings
                SET is_notified = 1, notified_time = ?
                WHERE id = ?
            ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), warning_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            print(f"✗ 標記通知狀態時出錯: {e}")
            return False
        finally:
            conn.close()
    
    def get_all_warnings(self, limit=None):
        """獲取所有警告"""
        conn = sqlite3.connect(self.db_name)
        
        try:
            query = 'SELECT * FROM warnings ORDER BY scrape_time DESC'
            if limit:
                query += f' LIMIT {limit}'
            
            df = pd.read_sql_query(query, conn)
            return df
            
        except Exception as e:
            print(f"✗ 查詢所有警告時出錯: {e}")
            return pd.DataFrame()
        finally:
            conn.close()
    
    def export_to_excel(self, filename=None):
        """匯出資料到 Excel"""
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'navigation_warnings_{timestamp}.xlsx'
        
        df = self.get_all_warnings()
        
        if not df.empty:
            df.to_excel(filename, index=False, engine='openpyxl')
            print(f"✓ Excel 檔案已儲存: {filename}")
            return True
        else:
            print("✗ 沒有資料可以匯出")
            return False
    
    def get_statistics(self):
        """獲取統計資訊"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            # 總警告數
            cursor.execute('SELECT COUNT(*) FROM warnings')
            total = cursor.fetchone()[0]
            
            # 已通知數
            cursor.execute('SELECT COUNT(*) FROM warnings WHERE is_notified = 1')
            notified = cursor.fetchone()[0]
            
            # 未通知數
            cursor.execute('SELECT COUNT(*) FROM warnings WHERE is_notified = 0')
            unnotified = cursor.fetchone()[0]
            
            # 各海事局統計
            cursor.execute('''
                SELECT maritime_bureau, COUNT(*) as count
                FROM warnings
                GROUP BY maritime_bureau
                ORDER BY count DESC
            ''')
            bureau_stats = cursor.fetchall()
            
            # 各關鍵字統計
            cursor.execute('''
                SELECT keywords_matched, COUNT(*) as count
                FROM warnings
                GROUP BY keywords_matched
                ORDER BY count DESC
            ''')
            keyword_stats = cursor.fetchall()
            
            return {
                'total': total,
                'notified': notified,
                'unnotified': unnotified,
                'bureau_stats': bureau_stats,
                'keyword_stats': keyword_stats
            }
            
        except Exception as e:
            print(f"✗ 獲取統計資訊時出錯: {e}")
            return None
        finally:
            conn.close()
    
    def print_statistics(self):
        """列印統計資訊"""
        stats = self.get_statistics()
        
        if stats:
            print("\n" + "=" * 60)
            print("資料庫統計資訊")
            print("=" * 60)
            print(f"總警告數: {stats['total']}")
            print(f"已通知: {stats['notified']}")
            print(f"未通知: {stats['unnotified']}")
            
            print("\n各海事局警告數:")
            for bureau, count in stats['bureau_stats'][:10]:
                print(f"  {bureau}: {count}")
            
            print("\n各關鍵字匹配數:")
            for keyword, count in stats['keyword_stats'][:10]:
                print(f"  {keyword}: {count}")
            print("=" * 60)


if __name__ == "__main__":
    # 測試資料庫管理功能
    db = DatabaseManager()
    
    # 顯示統計資訊
    db.print_statistics()
    
    # 顯示未通知的警告
    unnotified = db.get_unnotified_warnings()
    print(f"\n未通知的警告數: {len(unnotified)}")
