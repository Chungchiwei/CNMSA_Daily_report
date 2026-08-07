import json
import os
from datetime import datetime
import re

class KeywordManager:
    def __init__(self, config_file='keywords_config.json'):
        self.config_file = config_file
        self.keywords = []
        self.keyword_categories = {}
        self.load_keywords()

    def load_keywords(self):
        """載入關鍵字設定"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.keywords = data.get('keywords', [])
                    self.keyword_categories = data.get('categories', {})
                    print(f"✅ 已載入 {len(self.keywords)} 個關鍵字")
                    if self.keyword_categories:
                        print(f"📂 已載入 {len(self.keyword_categories)} 個分類")
            except Exception as e:
                print(f"❌ 載入關鍵字設定失敗: {e}")
                self.set_default_keywords()
        else:
            print("⚠️ 關鍵字設定檔不存在，使用預設值")
            self.set_default_keywords()

    def set_default_keywords(self):
        """設定預設關鍵字（支援中文繁簡體和英文）"""
        # 軍事演習相關
        military_keywords = [
            # 中文簡體
            "军事训练", "军事演习", "海上演习", "射击演习", "实弹射击",
            "军事活动", "军事行动", "军事封锁", "军事禁区", "军事演练",
            "军事任务",
            # 中文繁體
            "軍事訓練", "軍事演習", "海上演習", "射擊演習", "實彈射擊",
            "軍事活動", "軍事行動", "軍事封鎖", "軍事禁區", "軍事演練",
            # 英文
            "MILITARY EXERCISES", "NAVAL EXERCISES", "FIRING EXERCISES",
            "LIVE FIRING", "MILITARY ACTIVITY", "MILITARY OPERATIONS",
            "MILITARY BLOCKADE", "MILITARY ZONE"
        ]

        # 危險作業相關
        danger_keywords = [
            # 中文簡體
            "失控", "危险操作", "爆炸物处理", "扫雷作业", "水下作业", "潜水作业",
            # 中文繁體
            "失控", "危險操作", "爆炸物處理", "掃雷作業", "水下作業", "潛水作業",
            # 英文
            "NOT UNDER COMMAND", "NOT UNDER CONTROL", "DANGEROUS OPERATIONS",
            "EXPLOSIVE ORDNANCE", "MINE CLEARANCE OPERATIONS",
            "UNDERWATER OPERATIONS", "DIVING OPERATIONS"
        ]

        # 武器發射相關
        weapon_keywords = [
            # 中文簡體
            "火箭发射", "导弹发射", "火炮射击",
            # 中文繁體
            "火箭發射", "導彈發射", "火炮射擊",
            # 英文
            "ROCKET FIRING", "MISSILE FIRING", "ARTILLERY FIRING"
        ]

        # 區域管制相關
        area_keywords = [
            # 中文簡體
            "封锁区", "禁航区", "危险区域", "管制区", "警戒区",
            # 中文繁體
            "封鎖區", "禁航區", "危險區域", "管制區", "警戒區",
            # 英文
            "RESTRICTED AREA", "NO NAVIGATION AREA", "DANGER AREA",
            "CONTROL AREA", "WARNING AREA"
        ]

        # 台灣特有關鍵字
        taiwan_keywords = [
            # 中文繁體
            "國防部", "海軍", "空軍", "陸軍", "國軍", "演訓", "操演",
            "飛彈", "戰機", "軍艦", "潛艦", "雷達", "偵察",
            "礙航", "航行安全", "船舶注意", "協尋", "搜救",
            # 英文
            "ROC NAVY", "ROC AIR FORCE", "TAIWAN STRAIT", "SEARCH AND RESCUE"
        ]

        # 中國特有關鍵字
        china_keywords = [
            # 中文簡體
            "人民解放军", "海军", "空军", "陆军", "东部战区", "南部战区",
            "导弹试射", "舰艇编队", "战备巡逻", "联合演练",
            # 英文
            "PLA", "PEOPLE'S LIBERATION ARMY", "EAST CHINA SEA", "SOUTH CHINA SEA"
        ]

        # 設定分類
        self.keyword_categories = {
            "軍事演習": military_keywords,
            "危險作業": danger_keywords,
            "武器發射": weapon_keywords,
            "區域管制": area_keywords,
            "台灣特有": taiwan_keywords,
            "中國特有": china_keywords
        }

        # 合併所有關鍵字並去重
        all_keywords = set()
        for keywords in self.keyword_categories.values():
            all_keywords.update(keywords)

        self.keywords = sorted(list(all_keywords))

        self.save_keywords()
        print(f"✅ 已設定 {len(self.keywords)} 個預設關鍵字")

    def save_keywords(self):
        """儲存關鍵字設定"""
        try:
            data = {
                'keywords': self.keywords,
                'categories': self.keyword_categories,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'version': '2.0',
                'sources': ['CN_MSA', 'TW_MPB']
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"✅ 關鍵字設定已儲存到 {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ 儲存關鍵字設定失敗: {e}")
            return False

    def detect_language(self, text):
        """檢測文字語言類型"""
        # 繁體中文特徵字
        traditional_chars = set('國軍艦飛彈導潛偵礙協尋臺灣')
        # 簡體中文特徵字
        simplified_chars = set('国军舰飞弹导潜侦碍协寻台湾')

        text_chars = set(text)

        # 檢查是否為英文
        if text.isupper() and re.match(r'^[A-Z\s]+$', text):
            return 'EN'

        # 檢查繁體特徵
        if text_chars & traditional_chars:
            return 'TW'

        # 檢查簡體特徵
        if text_chars & simplified_chars:
            return 'CN'

        # 檢查是否包含中文
        if any('\u4e00' <= c <= '\u9fff' for c in text):
            # 進一步判斷繁簡體
            try:
                # 使用 Unicode 範圍判斷
                if any(ord(c) in range(0x3400, 0x4DBF) for c in text):
                    return 'TW'
                return 'CN'
            except:
                return 'CN'

        return 'OTHER'

    def add_keyword(self, keyword, category=None):
        """新增關鍵字"""
        keyword = keyword.strip()

        if len(keyword) < 2:
            print("❌ 關鍵字至少需要 2 個字元")
            return False

        # 檢查是否已存在（不區分大小寫）
        if any(k.lower() == keyword.lower() for k in self.keywords):
            print(f"⚠️ 關鍵字 '{keyword}' 已存在")
            return False

        self.keywords.append(keyword)

        # 如果指定分類，加入分類
        if category:
            if category not in self.keyword_categories:
                self.keyword_categories[category] = []
            self.keyword_categories[category].append(keyword)

        # 重新排序
        self.keywords = sorted(self.keywords)

        self.save_keywords()
        print(f"✅ 已新增關鍵字: {keyword}" + (f" (分類: {category})" if category else ""))
        return True

    def remove_keyword(self, keyword):
        """移除關鍵字"""
        found_keyword = None
        for k in self.keywords:
            if k.lower() == keyword.lower():
                found_keyword = k
                break

        if found_keyword:
            self.keywords.remove(found_keyword)

            # 從所有分類中移除
            for category, keywords in self.keyword_categories.items():
                if found_keyword in keywords:
                    keywords.remove(found_keyword)

            self.save_keywords()
            print(f"✅ 已移除關鍵字: {found_keyword}")
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

        found_keyword = None
        for k in self.keywords:
            if k.lower() == old_keyword.lower():
                found_keyword = k
                break

        if found_keyword:
            index = self.keywords.index(found_keyword)
            self.keywords[index] = new_keyword

            # 更新所有分類中的關鍵字
            for category, keywords in self.keyword_categories.items():
                if found_keyword in keywords:
                    keywords[keywords.index(found_keyword)] = new_keyword

            self.keywords = sorted(self.keywords)

            self.save_keywords()
            print(f"✅ 已更新關鍵字: {found_keyword} → {new_keyword}")
            return True
        else:
            print(f"⚠️ 關鍵字 '{old_keyword}' 不存在")
            return False

    def list_keywords(self, show_categories=False):
        """列出所有關鍵字"""
        if not self.keywords:
            print("⚠️ 目前沒有設定任何關鍵字")
            return

        print("\n" + "=" * 60)
        print(f"📋 多源海事警告關鍵字列表 (共 {len(self.keywords)} 個)")
        print("=" * 60)

        if show_categories and self.keyword_categories:
            for category, keywords in self.keyword_categories.items():
                if keywords:
                    print(f"\n📂 {category} ({len(keywords)} 個):")
                    for i, keyword in enumerate(sorted(keywords), 1):
                        lang = self.detect_language(keyword)
                        lang_mark = {'TW': '🇹🇼', 'CN': '🇨🇳', 'EN': '🌐'}.get(lang, '📝')
                        print(f"   {i:2d}. {lang_mark} {keyword}")

            # 顯示未分類的關鍵字
            categorized = set()
            for keywords in self.keyword_categories.values():
                categorized.update(keywords)

            uncategorized = [k for k in self.keywords if k not in categorized]
            if uncategorized:
                print(f"\n📝 未分類 ({len(uncategorized)} 個):")
                for i, keyword in enumerate(uncategorized, 1):
                    lang = self.detect_language(keyword)
                    lang_mark = {'TW': '🇹🇼', 'CN': '🇨🇳', 'EN': '🌐'}.get(lang, '📝')
                    print(f"   {i:2d}. {lang_mark} {keyword}")
        else:
            for i, keyword in enumerate(self.keywords, 1):
                lang = self.detect_language(keyword)
                lang_mark = {'TW': '🇹🇼', 'CN': '🇨🇳', 'EN': '🌐'}.get(lang, '📝')
                print(f"{i:2d}. {lang_mark} {keyword}")

        print("=" * 60 + "\n")

    def get_keywords(self):
        """取得關鍵字列表"""
        return self.keywords.copy()

    def get_keywords_by_source(self, source_type):
        """根據來源類型獲取相關關鍵字。

        改為以 keywords_config.json 既有的 categories 分類為準，而不是逐字的
        detect_language() 語言偵測。原因：detect_language() 只認得一組寫死的
        15 個繁體特徵字（見上方 traditional_chars），像「演訓」「射擊公告」
        「限航區」這類常見詞完全不含那些特徵字，會被誤判成簡體/CN，導致
        get_keywords_by_source("TW_MPB") 把這些明明是台灣航港局常用詞的關鍵字
        排除在外。改用分類判斷後，只需排除「屬於對方國家專屬」的分類
        （中國特有／台灣特有），其餘共用分類（武器發射／軍事演習／區域管制／
        危險作業／船艦類型／航空器／偵測設備／海事通告）雙方都會拿到，不再
        依賴不可靠的單字判斷。
        """
        if not self.keyword_categories:
            return self.get_keywords()

        if source_type == "TW_MPB":
            exclude_categories = {"中國特有"}
        elif source_type == "CN_MSA":
            exclude_categories = {"台灣特有"}
        else:
            exclude_categories = set()

        result = []
        seen = set()
        for category, kws in self.keyword_categories.items():
            if category in exclude_categories:
                continue
            for kw in kws:
                if kw not in seen:
                    seen.add(kw)
                    result.append(kw)

        # 保留不屬於任何分類的舊資料相容（flat keywords 裡有些詞可能沒有掛分類）
        categorized = set()
        for kws in self.keyword_categories.values():
            categorized.update(kws)
        for kw in self.keywords:
            if kw not in categorized and kw not in seen:
                seen.add(kw)
                result.append(kw)

        return result

    def import_keywords(self, keywords_list, category=None):
        """批量匯入關鍵字"""
        added = 0
        for keyword in keywords_list:
            keyword = keyword.strip()
            if (keyword and len(keyword) >= 2 and
                not any(k.lower() == keyword.lower() for k in self.keywords)):
                self.keywords.append(keyword)

                if category:
                    if category not in self.keyword_categories:
                        self.keyword_categories[category] = []
                    self.keyword_categories[category].append(keyword)

                added += 1

        if added > 0:
            self.keywords = sorted(self.keywords)
            self.save_keywords()
            print(f"✅ 已匯入 {added} 個新關鍵字" + (f" (分類: {category})" if category else ""))
        else:
            print("⚠️ 沒有新增任何關鍵字")

        return added

    def export_keywords(self, filename='keywords_export.txt', source_type=None):
        """匯出關鍵字到文字檔"""
        try:
            keywords_to_export = self.get_keywords_by_source(source_type) if source_type else self.keywords

            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"# 多源海事警告關鍵字匯出\n")
                f.write(f"# 匯出時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 來源類型: {source_type or '全部'}\n")
                f.write(f"# 關鍵字數量: {len(keywords_to_export)}\n\n")

                for keyword in keywords_to_export:
                    f.write(keyword + '\n')

            print(f"✅ 關鍵字已匯出到 {filename} ({len(keywords_to_export)} 個)")
            return True
        except Exception as e:
            print(f"❌ 匯出關鍵字失敗: {e}")
            return False

    def add_category(self, category_name):
        """新增關鍵字分類"""
        if category_name in self.keyword_categories:
            print(f"⚠️ 分類 '{category_name}' 已存在")
            return False

        self.keyword_categories[category_name] = []
        self.save_keywords()
        print(f"✅ 已新增分類: {category_name}")
        return True

    def remove_category(self, category_name):
        """移除關鍵字分類（不刪除關鍵字本身）"""
        if category_name not in self.keyword_categories:
            print(f"⚠️ 分類 '{category_name}' 不存在")
            return False

        del self.keyword_categories[category_name]
        self.save_keywords()
        print(f"✅ 已移除分類: {category_name}")
        return True

    def get_statistics(self):
        """獲取關鍵字統計資訊"""
        tw_count = len([k for k in self.keywords if self.detect_language(k) == 'TW'])
        cn_count = len([k for k in self.keywords if self.detect_language(k) == 'CN'])
        en_count = len([k for k in self.keywords if self.detect_language(k) == 'EN'])

        stats = {
            'total': len(self.keywords),
            'categories': len(self.keyword_categories),
            'chinese_traditional': tw_count,
            'chinese_simplified': cn_count,
            'english': en_count,
            'by_category': {cat: len(keywords) for cat, keywords in self.keyword_categories.items()}
        }
        return stats

    def clear_keywords(self):
        """清空所有關鍵字"""
        self.keywords = []
        self.keyword_categories = {}
        self.save_keywords()
        print("✅ 已清空所有關鍵字和分類")


def interactive_menu():
    """互動式選單"""
    manager = KeywordManager()

    while True:
        print("\n" + "=" * 60)
        print("🔑 多源海事警告關鍵字管理程式")
        print("=" * 60)
        print("1. 查看所有關鍵字")
        print("2. 按分類查看關鍵字")
        print("3. 新增關鍵字")
        print("4. 移除關鍵字")
        print("5. 修改關鍵字")
        print("6. 批量匯入關鍵字")
        print("7. 匯出關鍵字")
        print("8. 按來源匯出關鍵字")
        print("9. 新增分類")
        print("10. 移除分類")
        print("11. 查看統計資訊")
        print("12. 重設為預設關鍵字")
        print("13. 清空所有關鍵字")
        print("0. 離開")
        print("=" * 60)

        choice = input("\n請選擇功能 (0-13): ").strip()

        if choice == '1':
            manager.list_keywords(show_categories=False)

        elif choice == '2':
            manager.list_keywords(show_categories=True)

        elif choice == '3':
            keyword = input("請輸入要新增的關鍵字: ").strip()
            if manager.keyword_categories:
                print("可用分類:", ', '.join(manager.keyword_categories.keys()))
                category = input("請輸入分類 (可選，直接按 Enter 跳過): ").strip()
                category = category if category else None
            else:
                category = None
            manager.add_keyword(keyword, category)

        elif choice == '4':
            manager.list_keywords()
            keyword = input("請輸入要移除的關鍵字: ").strip()
            manager.remove_keyword(keyword)

        elif choice == '5':
            manager.list_keywords()
            old_keyword = input("請輸入要修改的關鍵字: ").strip()
            new_keyword = input("請輸入新的關鍵字: ").strip()
            manager.update_keyword(old_keyword, new_keyword)

        elif choice == '6':
            filename = input("請輸入文字檔名稱 (預設: keywords_import.txt): ").strip()
            if not filename:
                filename = 'keywords_import.txt'

            if manager.keyword_categories:
                print("可用分類:", ', '.join(manager.keyword_categories.keys()))
                category = input("請輸入分類 (可選，直接按 Enter 跳過): ").strip()
                category = category if category else None
            else:
                category = None

            if os.path.exists(filename):
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        keywords = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                    manager.import_keywords(keywords, category)
                except Exception as e:
                    print(f"❌ 讀取檔案失敗: {e}")
            else:
                print(f"⚠️ 檔案 '{filename}' 不存在")

        elif choice == '7':
            filename = input("請輸入匯出檔名 (預設: keywords_export.txt): ").strip()
            if not filename:
                filename = 'keywords_export.txt'
            manager.export_keywords(filename)

        elif choice == '8':
            print("\n來源選項:")
            print("1. CN_MSA (中國海事局)")
            print("2. TW_MPB (台灣航港局)")
            source_choice = input("請選擇來源 (1-2): ").strip()

            source_map = {'1': 'CN_MSA', '2': 'TW_MPB'}
            source_type = source_map.get(source_choice)

            if source_type:
                filename = f"keywords_{source_type.lower()}.txt"
                manager.export_keywords(filename, source_type)
            else:
                print("❌ 無效的選擇")

        elif choice == '9':
            category = input("請輸入新分類名稱: ").strip()
            if category:
                manager.add_category(category)
            else:
                print("❌ 分類名稱不能為空")

        elif choice == '10':
            if manager.keyword_categories:
                print("現有分類:", ', '.join(manager.keyword_categories.keys()))
                category = input("請輸入要移除的分類名稱: ").strip()
                if category:
                    manager.remove_category(category)
            else:
                print("⚠️ 目前沒有任何分類")

        elif choice == '11':
            stats = manager.get_statistics()
            print(f"\n📊 關鍵字統計資訊:")
            print(f"總關鍵字數: {stats['total']}")
            print(f"分類數: {stats['categories']}")
            print(f"🇹🇼 繁體中文: {stats['chinese_traditional']}")
            print(f"🇨🇳 簡體中文: {stats['chinese_simplified']}")
            print(f"🌐 英文: {stats['english']}")
            if stats['by_category']:
                print(f"\n各分類統計:")
                for cat, count in stats['by_category'].items():
                    print(f"  {cat}: {count}")

        elif choice == '12':
            confirm = input("⚠️  確定要重設為預設關鍵字嗎？(y/n): ").strip().lower()
            if confirm == 'y':
                manager.set_default_keywords()

        elif choice == '13':
            confirm = input("⚠️  確定要清空所有關鍵字嗎？此操作無法復原！(y/n): ").strip().lower()
            if confirm == 'y':
                manager.clear_keywords()

        elif choice == '0':
            print("\n👋 再見！")
            break

        else:
            print("\n❌ 無效的選擇，請重新輸入")


if __name__ == "__main__":
    interactive_menu()
