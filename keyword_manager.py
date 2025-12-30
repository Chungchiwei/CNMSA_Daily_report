import json
import os
from datetime import datetime

class KeywordManager:
    def __init__(self, config_file='keywords_config.json'):
        self.config_file = config_file
        self.keywords = []
        self.load_keywords()
    
    def load_keywords(self):
        """載入關鍵字設定"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.keywords = data.get('keywords', [])
                    print(f"✅ 已載入 {len(self.keywords)} 個關鍵字")
            except Exception as e:
                print(f"❌ 載入關鍵字設定失敗: {e}")
                self.set_default_keywords()
        else:
            print("⚠️ 關鍵字設定檔不存在，使用預設值")
            self.set_default_keywords()
    
    def set_default_keywords(self):
        """設定預設關鍵字"""
        self.keywords = [
            "军事训练", "MILITARY EXERCISES", "军事演习", "失控", "NOT UNDER COMMAND",
            "ROCKET FIRING", "火箭发射", "NOT UNDER CONTROL", "导弹发射", "MISSILE FIRING",
            "危险操作", "DANGEROUS OPERATIONS", "爆炸物处理", "EXPLOSIVE ORDNANCE", 
            "扫雷作业", "MINE CLEARANCE OPERATIONS", "水下作业", "UNDERWATER OPERATIONS", 
            "潜水作业", "DIVING OPERATIONS", "海上演习", "NAVAL EXERCISES",
            "射击演习", "FIRING EXERCISES", "实弹射击", "LIVE FIRING", 
            "军事活动", "MILITARY ACTIVITY", "军事行动", "MILITARY OPERATIONS", 
            "封锁区", "RESTRICTED AREA", "禁航区", "NO NAVIGATION AREA",
            "危险区域", "DANGER AREA", "军事封锁", "MILITARY BLOCKADE", 
            "军事禁区", "MILITARY ZONE"
        ]
        self.save_keywords()
    
    def save_keywords(self):
        """儲存關鍵字設定"""
        try:
            data = {
                'keywords': self.keywords,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"✅ 關鍵字設定已儲存到 {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ 儲存關鍵字設定失敗: {e}")
            return False
    
    def add_keyword(self, keyword):
        """新增關鍵字"""
        keyword = keyword.strip()
        
        # 長度驗證
        if len(keyword) < 2:
            print("❌ 關鍵字至少需要 2 個字元")
            return False
        
        if not keyword:
            print("❌ 關鍵字不能為空")
            return False
        
        if keyword in self.keywords:
            print(f"⚠️ 關鍵字 '{keyword}' 已存在")
            return False
        
        self.keywords.append(keyword)
        self.save_keywords()
        print(f"✅ 已新增關鍵字: {keyword}")
        return True
    
    def remove_keyword(self, keyword):
        """移除關鍵字"""
        if keyword in self.keywords:
            self.keywords.remove(keyword)
            self.save_keywords()
            print(f"✅ 已移除關鍵字: {keyword}")
            return True
        else:
            print(f"⚠️ 關鍵字 '{keyword}' 不存在")
            return False
    
    def update_keyword(self, old_keyword, new_keyword):
        """更新關鍵字"""
        new_keyword = new_keyword.strip()
        
        if len(new_keyword) < 2:
            print("❌ 新關鍵字至少需要 2 個字元")
            return False
        
        if not new_keyword:
            print("❌ 新關鍵字不能為空")
            return False
        
        if old_keyword in self.keywords:
            index = self.keywords.index(old_keyword)
            self.keywords[index] = new_keyword
            self.save_keywords()
            print(f"✅ 已更新關鍵字: {old_keyword} → {new_keyword}")
            return True
        else:
            print(f"⚠️ 關鍵字 '{old_keyword}' 不存在")
            return False
    
    def list_keywords(self):
        """列出所有關鍵字"""
        if not self.keywords:
            print("⚠️ 目前沒有設定任何關鍵字")
            return
        
        print("\n" + "=" * 60)
        print(f"📋 目前關鍵字列表 (共 {len(self.keywords)} 個)")
        print("=" * 60)
        for i, keyword in enumerate(self.keywords, 1):
            print(f"{i:2d}. {keyword}")
        print("=" * 60 + "\n")
    
    def get_keywords(self):
        """取得關鍵字列表"""
        return self.keywords.copy()
    
    def import_keywords(self, keywords_list):
        """批量匯入關鍵字"""
        added = 0
        for keyword in keywords_list:
            keyword = keyword.strip()
            if keyword and len(keyword) >= 2 and keyword not in self.keywords:
                self.keywords.append(keyword)
                added += 1
        
        if added > 0:
            self.save_keywords()
            print(f"✅ 已匯入 {added} 個新關鍵字")
        else:
            print("⚠️ 沒有新增任何關鍵字")
        
        return added
    
    def export_keywords(self, filename='keywords_export.txt'):
        """匯出關鍵字到文字檔"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                for keyword in self.keywords:
                    f.write(keyword + '\n')
            print(f"✅ 關鍵字已匯出到 {filename}")
            return True
        except Exception as e:
            print(f"❌ 匯出關鍵字失敗: {e}")
            return False
    
    def clear_keywords(self):
        """清空所有關鍵字"""
        self.keywords = []
        self.save_keywords()
        print("✅ 已清空所有關鍵字")


def interactive_menu():
    """互動式選單"""
    manager = KeywordManager()
    
    while True:
        print("\n" + "=" * 60)
        print("🔑 航行警告關鍵字管理程式")
        print("=" * 60)
        print("1. 查看所有關鍵字")
        print("2. 新增關鍵字")
        print("3. 移除關鍵字")
        print("4. 修改關鍵字")
        print("5. 批量匯入關鍵字 (從文字檔)")
        print("6. 匯出關鍵字到文字檔")
        print("7. 重設為預設關鍵字")
        print("8. 清空所有關鍵字")
        print("0. 離開")
        print("=" * 60)
        
        choice = input("\n請選擇功能 (0-8): ").strip()
        
        if choice == '1':
            manager.list_keywords()
            
        elif choice == '2':
            keyword = input("請輸入要新增的關鍵字: ").strip()
            manager.add_keyword(keyword)
            
        elif choice == '3':
            manager.list_keywords()
            keyword = input("請輸入要移除的關鍵字: ").strip()
            manager.remove_keyword(keyword)
            
        elif choice == '4':
            manager.list_keywords()
            old_keyword = input("請輸入要修改的關鍵字: ").strip()
            new_keyword = input("請輸入新的關鍵字: ").strip()
            manager.update_keyword(old_keyword, new_keyword)
            
        elif choice == '5':
            filename = input("請輸入文字檔名稱 (預設: keywords_import.txt): ").strip()
            if not filename:
                filename = 'keywords_import.txt'
            
            if os.path.exists(filename):
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        keywords = [line.strip() for line in f if line.strip()]
                    manager.import_keywords(keywords)
                except Exception as e:
                    print(f"❌ 讀取檔案失敗: {e}")
            else:
                print(f"⚠️ 檔案 '{filename}' 不存在")
                
        elif choice == '6':
            filename = input("請輸入匯出檔名 (預設: keywords_export.txt): ").strip()
            if not filename:
                filename = 'keywords_export.txt'
            manager.export_keywords(filename)
            
        elif choice == '7':
            confirm = input("確定要重設為預設關鍵字嗎？(y/n): ").strip().lower()
            if confirm == 'y':
                manager.set_default_keywords()
                print("✅ 已重設為預設關鍵字")
            
        elif choice == '8':
            confirm = input("確定要清空所有關鍵字嗎？(y/n): ").strip().lower()
            if confirm == 'y':
                manager.clear_keywords()
            
        elif choice == '0':
            print("\n👋 再見！")
            break
            
        else:
            print("\n❌ 無效的選擇，請重新輸入")


if __name__ == "__main__":
    interactive_menu()
