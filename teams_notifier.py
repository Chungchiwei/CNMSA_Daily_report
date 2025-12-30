import requests
import json
from datetime import datetime

class TeamsNotifier:
    def __init__(self, webhook_url):
        """
        初始化 Teams 通知器
        webhook_url: Teams Incoming Webhook URL
        """
        self.webhook_url = webhook_url
    
    def _fix_url(self, url):
        """
        修正 URL 格式
        處理相對路徑、空值等問題
        """
        if not url:
            return "https://www.msa.gov.cn/page/outter/weather.jsp"
        
        url = url.strip()
        
        # 如果是相對路徑（以 / 開頭）
        if url.startswith('/'):
            return f"https://www.msa.gov.cn{url}"
        
        # 如果已經是完整 URL
        if url.startswith('http://') or url.startswith('https://'):
            return url
        
        # 如果是 JavaScript 或其他特殊連結
        if url.startswith('javascript:') or url.startswith('#'):
            return "https://www.msa.gov.cn/page/outter/weather.jsp"
        
        # 其他情況，加上基礎 URL
        return f"https://www.msa.gov.cn/{url}"
    
    def _create_adaptive_card(self, title, body_elements, actions=None):
        """
        創建 Adaptive Card 格式的訊息
        title: 卡片標題
        body_elements: 卡片內容元素列表
        actions: 動作按鈕列表（可選）
        """
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
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
                }
            ]
        }
        
        # 如果有動作按鈕，添加到卡片中
        if actions:
            card["attachments"][0]["content"]["actions"] = actions
        
        return card
    
    def test_connection(self):
        """
        測試 Teams Webhook 連接
        """
        test_card = self._create_adaptive_card(
            "🔔 測試通知",
            [
                {
                    "type": "TextBlock",
                    "text": "這是一個測試訊息，用於驗證 Teams Webhook 連接是否正常。",
                    "wrap": True
                },
                {
                    "type": "TextBlock",
                    "text": f"測試時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "wrap": True,
                    "size": "Small",
                    "isSubtle": True
                }
            ]
        )
        
        try:
            response = requests.post(
                self.webhook_url,
                json=test_card,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 202:
                print("✓ Teams Webhook 連接測試成功")
                return True
            else:
                print(f"✗ Teams Webhook 連接測試失敗")
                print(f"   狀態碼: {response.status_code}")
                print(f"   回應: {response.text}")
                return False
                
        except Exception as e:
            print(f"✗ Teams Webhook 連接測試出錯: {e}")
            return False
    
    def send_warning_notification(self, warning_data):
        """
        發送單個警告通知到 Teams
        warning_data: (id, maritime_bureau, title, link, publish_time, keywords_matched, scrape_time)
        """
        warning_id, maritime_bureau, title, link, publish_time, keywords_matched, scrape_time = warning_data
        
        # 修正 URL
        fixed_link = self._fix_url(link)
        
        # 建立卡片內容
        body_elements = [
            {
                "type": "Container",
                "style": "warning",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "💡 **使用提示**",
                        "weight": "Bolder",
                        "size": "Small"
                    },
                    {
                        "type": "TextBlock",
                        "text": "• 如果點擊按鈕顯示「ACCESS DENIED」\n• 請複製下方連結到瀏覽器開啟\n• 或在 Teams 設定中啟用「在預設瀏覽器中開啟連結」",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "Small"
                    }
                ]
            },
            {
                "type": "TextBlock",
                "text": "━━━━━━━━━━━━━━━━━━━━",
                "wrap": True
            },
            {
                "type": "FactSet",
                "facts": [
                    {
                        "title": "🏢 海事局:",
                        "value": maritime_bureau
                    },
                    {
                        "title": "📋 警告標題:",
                        "value": title
                    },
                    {
                        "title": "📅 發布時間:",
                        "value": publish_time
                    },
                    {
                        "title": "🔍 匹配關鍵字:",
                        "value": keywords_matched
                    },
                    {
                        "title": "⏰ 抓取時間:",
                        "value": scrape_time
                    }
                ]
            },
            {
                "type": "TextBlock",
                "text": "━━━━━━━━━━━━━━━━━━━━",
                "wrap": True
            },
            {
                "type": "TextBlock",
                "text": "🔗 **完整網址（建議複製到瀏覽器開啟）:**",
                "wrap": True,
                "weight": "Bolder",
                "size": "Small",
                "color": "Accent"
            },
            {
                "type": "TextBlock",
                "text": fixed_link,
                "wrap": True,
                "size": "Small",
                "fontType": "Monospace"
            }
        ]
        
        # 建立動作按鈕
        actions = [
            {
                "type": "Action.OpenUrl",
                "title": "🌐 開啟連結",
                "url": fixed_link
            },
            {
                "type": "Action.OpenUrl",
                "title": "📋 海事局首頁",
                "url": "https://www.msa.gov.cn/page/outter/weather.jsp"
            }
        ]
        
        card_data = self._create_adaptive_card("🚨 航行警告通知", body_elements, actions)
        
        try:
            response = requests.post(
                self.webhook_url,
                json=card_data,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 202:
                print(f"✓ Teams 通知發送成功 (ID: {warning_id})")
                return True
            else:
                print(f"✗ Teams 通知發送失敗 (ID: {warning_id})")
                print(f"   狀態碼: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"✗ 發送 Teams 通知時出錯 (ID: {warning_id}): {e}")
            return False
    
    def send_batch_notification(self, warnings_list):
        """
        發送批量警告通知到 Teams
        warnings_list: 警告列表，每個元素為 (id, maritime_bureau, title, link, publish_time, keywords_matched, scrape_time)
        """
        if not warnings_list:
            print("沒有警告需要發送")
            return True
        
        # 建立卡片內容
        body_elements = [
            {
                "type": "Container",
                "style": "emphasis",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "💡 **重要提示**",
                        "weight": "Bolder",
                        "color": "Attention"
                    },
                    {
                        "type": "TextBlock",
                        "text": "如果點擊連結顯示「ACCESS DENIED」，請：\n  1️⃣ 複製下方連結到瀏覽器開啟\n 2️⃣ 或在 Teams 設定中啟用「在預設瀏覽器中開啟連結」",
                        "wrap": True,
                        "size": "Small"
                    }
                ]
            },
            {
                "type": "TextBlock",
                "text": f"發現 **{len(warnings_list)}** 個新的航行警告",
                "wrap": True,
                "size": "Medium",
                "weight": "Bolder",
                "spacing": "Medium"
            },
            {
                "type": "TextBlock",
                "text": "━━━━━━━━━━━━━━━━━━━━",
                "wrap": True
            }
        ]
        
        # 收集所有動作按鈕
        actions = []
        
        # 添加每個警告的資訊（最多顯示 8 個）
        for idx, warning_data in enumerate(warnings_list[:8], 1):
            warning_id, maritime_bureau, title, link, publish_time, keywords_matched, scrape_time = warning_data
            
            # 修正 URL
            fixed_link = self._fix_url(link)
            
            body_elements.append({
                "type": "TextBlock",
                "text": f"**{idx}. {maritime_bureau}**",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Accent",
                "spacing": "Medium"
            })
            
            body_elements.append({
                "type": "TextBlock",
                "text": title[:150] + ("..." if len(title) > 150 else ""),
                "wrap": True,
                "size": "Default"
            })
            
            body_elements.append({
                "type": "FactSet",
                "facts": [
                    {
                        "title": "關鍵字:",
                        "value": keywords_matched
                    },
                    {
                        "title": "發布時間:",
                        "value": publish_time
                    }
                ],
                "spacing": "Small"
            })
            
            # 添加可複製的連結
            body_elements.append({
                "type": "TextBlock",
                "text": f"🔗 {fixed_link}",
                "wrap": True,
                "size": "Small",
                "fontType": "Monospace",
                "spacing": "Small"
            })
            
            # 添加按鈕到動作列表（Adaptive Card 最多支持 6 個按鈕）
            if len(actions) < 5:  # 保留一個位置給海事局首頁
                actions.append({
                    "type": "Action.OpenUrl",
                    "title": f"📄 警告 {idx}",
                    "url": fixed_link
                })
            
            # 添加分隔線
            if idx < min(len(warnings_list), 8):
                body_elements.append({
                    "type": "TextBlock",
                    "text": "━━━━━━━━━━━━━━━━━━━━",
                    "wrap": True,
                    "spacing": "Small"
                })
        
        # 如果超過 8 個，添加提示
        if len(warnings_list) > 8:
            body_elements.append({
                "type": "TextBlock",
                "text": f"*還有 {len(warnings_list) - 8} 個警告未顯示，請查看 Excel 報表*",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "Medium"
            })
        
        # 添加海事局首頁按鈕
        actions.append({
            "type": "Action.OpenUrl",
            "title": "🌐 海事局網站",
            "url": "https://www.msa.gov.cn/page/outter/weather.jsp"
        })
        
        card_data = self._create_adaptive_card(
            f"🚨 批量航行警告通知 ({len(warnings_list)} 個)",
            body_elements,
            actions
        )
        
        try:
            response = requests.post(
                self.webhook_url,
                json=card_data,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 202:
                print(f"✓ Teams 批量通知發送成功 ({len(warnings_list)} 個警告)")
                return True
            else:
                print(f"✗ Teams 批量通知發送失敗")
                print(f"   狀態碼: {response.status_code}")
                print(f"   回應: {response.text}")
                return False
                
        except Exception as e:
            print(f"✗ 發送 Teams 批量通知時出錯: {e}")
            return False
    
    def send_summary_notification(self, stats):
        """
        發送統計摘要通知到 Teams
        stats: 統計資訊字典
        """
        body_elements = [
            {
                "type": "TextBlock",
                "text": "本次執行統計摘要",
                "wrap": True,
                "size": "Medium"
            },
            {
                "type": "TextBlock",
                "text": "━━━━━━━━━━━━━━━━━━━━",
                "wrap": True
            },
            {
                "type": "FactSet",
                "facts": [
                    {
                        "title": "📊 總警告數:",
                        "value": str(stats.get('total_warnings', 0))
                    },
                    {
                        "title": "🆕 新發現警告:",
                        "value": str(stats.get('new_warnings', 0))
                    },
                    {
                        "title": "🕐 最後抓取時間:",
                        "value": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                ]
            }
        ]
        
        # 如果有海事局統計
        if 'bureau_stats' in stats and stats['bureau_stats']:
            body_elements.append({
                "type": "TextBlock",
                "text": "━━━━━━━━━━━━━━━━━━━━",
                "wrap": True
            })
            body_elements.append({
                "type": "TextBlock",
                "text": "**各海事局警告數量:**",
                "wrap": True,
                "weight": "Bolder"
            })
            
            bureau_facts = []
            bureau_stats = stats['bureau_stats']
            
            # 檢查是字典還是列表
            if isinstance(bureau_stats, dict):
                # 如果是字典格式
                for bureau, count in bureau_stats.items():
                    bureau_facts.append({
                        "title": f"• {bureau}:",
                        "value": str(count)
                    })
            elif isinstance(bureau_stats, list):
                # 如果是列表格式 [(bureau, count), ...]
                for item in bureau_stats:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        bureau, count = item[0], item[1]
                        bureau_facts.append({
                            "title": f"• {bureau}:",
                            "value": str(count)
                        })
            
            if bureau_facts:
                body_elements.append({
                    "type": "FactSet",
                    "facts": bureau_facts
                })
        
        card_data = self._create_adaptive_card(
            "📈 執行統計報告",
            body_elements
        )
        
        try:
            response = requests.post(
                self.webhook_url,
                json=card_data,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 202:
                print("✓ Teams 統計摘要發送成功")
                return True
            else:
                print(f"✗ Teams 統計摘要發送失敗")
                print(f"   狀態碼: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"✗ 發送 Teams 統計摘要時出錯: {e}")
            import traceback
            traceback.print_exc()
            return False
