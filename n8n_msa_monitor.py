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
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
import urllib3
from database_manager import DatabaseManager
from keyword_manager import KeywordManager

# 停用警告
warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger('selenium').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

load_dotenv()

# ==================== 1. 統一的通知系統 ====================
class UnifiedTeamsNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    def _fix_url(self, url, base_domain=""):
        """修正 URL 格式，支援多個來源"""
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

    def send_unified_notification(self, warning_data, source_type="CN_MSA"):
        """發送統一格式的警告通知"""
        if not self.webhook_url: 
            return False
        
        try:
            warning_id, bureau, title, link, pub_time, keywords, scrape_time = warning_data
            
            # 根據來源設定不同的基礎域名和圖示
            if source_type == "TW_MPB":
                base_domain = "https://www.motcmpb.gov.tw"
                source_icon = "🇹🇼"
                source_name = "台灣航港局"
                home_url = "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"
            else:  # CN_MSA
                base_domain = "https://www.msa.gov.cn"
                source_icon = "🇨🇳"
                source_name = "中國海事局"
                home_url = "https://www.msa.gov.cn/page/outter/weather.jsp"
            
            fixed_link = self._fix_url(link, base_domain)
            
            body = [
                {
                    "type": "TextBlock", 
                    "text": f"{source_icon} 來源: {source_name}", 
                    "size": "Medium", 
                    "weight": "Bolder",
                    "color": "Accent"
                },
                {
                    "type": "TextBlock", 
                    "text": "💡 點擊按鈕若失敗，請複製下方連結", 
                    "size": "Small", 
                    "isSubtle": True, 
                    "wrap": True
                },
                {
                    "type": "FactSet", 
                    "facts": [
                        {"title": "🏢 發布單位:", "value": bureau},
                        {"title": "📋 標題:", "value": title},
                        {"title": "📅 發布時間:", "value": pub_time},
                        {"title": "🔍 關鍵字:", "value": keywords}
                    ]
                },
                {
                    "type": "TextBlock", 
                    "text": "🔗 連結:", 
                    "weight": "Bolder", 
                    "size": "Small"
                },
                {
                    "type": "TextBlock", 
                    "text": fixed_link, 
                    "wrap": True, 
                    "size": "Small", 
                    "fontType": "Monospace"
                }
            ]
            
            actions = [
                {
                    "type": "Action.OpenUrl", 
                    "title": "🌐 開啟公告", 
                    "url": fixed_link
                },
                {
                    "type": "Action.OpenUrl", 
                    "title": f"🏠 {source_name}首頁", 
                    "url": home_url
                }
            ]
            
            payload = self._create_adaptive_card(f"🚨 {source_name} 航行警告通知", body, actions)
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                print(f"  ✅ Teams 通知發送成功 (ID: {warning_id}, 來源: {source_type})")
                return True
            else:
                print(f"  ❌ Teams 通知失敗: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Teams 通知發送失敗: {e}")
            return False

    def send_batch_notification(self, warnings_list, source_type="CN_MSA"):
        """發送批量警告通知"""
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
                _, bureau, title, link, pub_time, _, _ = w
                fixed_link = self._fix_url(link, base_domain)
                
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
                        "text": f"📅 {pub_time}", 
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
            
            response = requests.post(
                self.webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"}, 
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                print(f"✅ {source_name} Teams 批量通知發送成功 ({len(warnings_list)} 筆)")
                return True
            else:
                print(f"❌ {source_name} Teams 批量通知失敗: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ {source_name} Teams 批量發送失敗: {e}")
            return False

# ==================== 1.5. Email 通知系統 ====================
class GmailRelayNotifier:
    """Gmail SMTP 郵件通知系統"""
    def __init__(self, mail_user, mail_pass, target_email):
        self.mail_user = mail_user
        self.mail_pass = mail_pass
        self.target_email = target_email
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        # 檢查設定
        if not all([mail_user, mail_pass, target_email]):
            print("⚠️ Email 通知未完整設定，將跳過郵件發送")
            self.enabled = False
        else:
            self.enabled = True
            print("✅ Email 通知系統已啟用")
    
    def send_trigger_email(self, json_data, html_content):
        """發送觸發郵件"""
        if not self.enabled:
            print("ℹ️ Email 通知未啟用，跳過發送")
            return False
        
        try:
            # 建立郵件
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"🌊 海事警告監控報告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            msg['From'] = self.mail_user
            msg['To'] = self.target_email
            
            # 純文字版本（備用）
            text_content = f"""
海事警告監控系統報告

執行時間: {json_data.get('execution_time', 'N/A')}
執行耗時: {json_data.get('duration', 0)} 秒
總計新警告: {json_data.get('total_warnings', 0)} 筆
- 中國海事局: {json_data.get('cn_msa_warnings', 0)} 筆
- 台灣航港局: {json_data.get('tw_mpb_warnings', 0)} 筆

詳細內容請查看 HTML 版本郵件。
            """
            
            # 附加內容
            part1 = MIMEText(text_content, 'plain', 'utf-8')
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)
            
            # 發送郵件
            print(f"📧 正在發送郵件至 {self.target_email}...")
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            
            print(f"✅ 郵件發送成功")
            return True
            
        except smtplib.SMTPAuthenticationError:
            print("❌ Email 認證失敗，請檢查帳號密碼")
            return False
        except smtplib.SMTPException as e:
            print(f"❌ SMTP 錯誤: {e}")
            return False
        except Exception as e:
            print(f"❌ 郵件發送失敗: {e}")
            traceback.print_exc()
            return False
    
    def send_error_notification(self, error_message, error_traceback=None):
        """發送錯誤通知郵件"""
        if not self.enabled:
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"⚠️ 海事警告監控系統錯誤 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            msg['From'] = self.mail_user
            msg['To'] = self.target_email
            
            html_content = f"""
            <html><body style="font-family: Arial, sans-serif; color:#333;">
                <div style="background:#dc3545; color:white; padding:20px; border-radius:6px 6px 0 0;">
                    <h2 style="margin: 0;">⚠️ 系統錯誤通知</h2>
                </div>
                <div style="padding:20px; border:1px solid #ddd;">
                    <p><strong>錯誤時間:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    <p><strong>錯誤訊息:</strong></p>
                    <pre style="background:#f8f9fa; padding:15px; border-radius:4px; overflow-x:auto;">{error_message}</pre>
                    {f'<p><strong>詳細追蹤:</strong></p><pre style="background:#f8f9fa; padding:15px; border-radius:4px; overflow-x:auto; font-size:12px;">{error_traceback}</pre>' if error_traceback else ''}
                </div>
            </body></html>
            """
            
            part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part)
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            
            print("✅ 錯誤通知郵件發送成功")
            return True
            
        except Exception as e:
            print(f"❌ 錯誤通知郵件發送失敗: {e}")
            return False
# ==================== 2. 台灣航港局爬蟲類別 ====================
class TWMaritimePortBureauScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, days=3):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        
        self.base_url = "https://www.motcmpb.gov.tw/Information/Notice"
        self.params = {
            'SiteId': '1',
            'NodeId': '483'
        }
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.motcmpb.gov.tw/'
        }
        self.days = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.new_warnings = []
        self.captured_warnings_data = []
        
        # 定義要抓取的分類 (礙航公告和射擊公告)
        self.target_categories = {
            '333': '礙航公告',
            '334': '射擊公告'
        }
        
        print(f"  📅 台灣航港局爬蟲設定: 抓取最近 {days} 天資料 (從 {self.cutoff_date.strftime('%Y-%m-%d')} 起)")
    
    def check_keywords(self, text):
        """檢查文字中是否包含關鍵字"""
        if not text:
            return []
        
        matched = []
        
        # 檢查原有關鍵字
        for k in self.keywords:
            if k.lower() in text.lower():
                matched.append(k)
        
        # 額外檢查礙航和射擊關鍵字
        if '礙航' in text and '礙航' not in matched:
            matched.append('礙航')
        if '射擊' in text and '射擊' not in matched:
            matched.append('射擊')
        
        return matched
    
    def parse_date(self, date_string):
        """解析日期字串(支援民國年和西元年)"""
        try:
            date_string = date_string.strip()
            
            # 處理民國年格式 (例如: 114-01-13 或 114/01/13)
            roc_match = re.match(r'^(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})$', date_string)
            if roc_match:
                year = int(roc_match.group(1)) + 1911
                month = int(roc_match.group(2))
                day = int(roc_match.group(3))
                return datetime(year, month, day)
            
            # 處理西元年格式
            date_formats = [
                '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日'
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
            
            print(f"    ⚠️ 無法解析日期: {date_string}")
            return None
        except Exception as e:
            print(f"    ⚠️ 日期解析錯誤: {e}")
            return None
    
    def is_within_date_range(self, date_string):
        """檢查日期是否在最近N天內"""
        if not date_string:
            return True  # 如果沒有日期,預設為符合條件
        
        parsed_date = self.parse_date(date_string)
        if parsed_date:
            is_valid = parsed_date >= self.cutoff_date
            if not is_valid:
                print(f"    ⏭️ 跳過舊資料: {date_string} (早於 {self.cutoff_date.strftime('%Y-%m-%d')})")
            return is_valid
        
        return True  # 解析失敗時預設為符合條件
    
    def get_notices(self, page=1, base_category_id=None):
        """爬取指定頁面的航行警告"""
        try:
            params = self.params.copy()
            if page > 1:
                params['page'] = page
            if base_category_id:
                params['baseCategoryId'] = base_category_id
            
            category_name = self.target_categories.get(base_category_id, '全部') if base_category_id else '全部'
            print(f"  正在請求台灣航港局 [{category_name}] 第 {page} 頁...")
            
            response = requests.get(
                self.base_url, 
                params=params, 
                headers=self.headers,
                timeout=30,
                verify=False
            )
            response.raise_for_status()
            response.encoding = 'utf-8'
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            contents_div = soup.find('div', class_='contents')
            if not contents_div:
                print(f"    ⚠️ 找不到 contents div")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            dl_list = contents_div.find_all('dl')
            print(f"    📋 找到 {len(dl_list)} 個 dl 元素")
            
            if len(dl_list) <= 1:
                print(f"    ⚠️ 沒有資料列 (只有標題列)")
                return {'has_data': False, 'notices': [], 'processed': 0}
            
            notices = []
            processed_count = 0
            skipped_date = 0
            skipped_keyword = 0
            
            # 跳過第一個 dl(標題列)
            for idx, dl in enumerate(dl_list[1:], 1):
                try:
                    dt_list = dl.find_all('dt')
                    dd = dl.find('dd')
                    
                    if len(dt_list) < 3 or not dd:
                        continue
                    
                    processed_count += 1
                    
                    number = dt_list[0].get_text(strip=True)
                    date = dt_list[1].get_text(strip=True)
                    unit = dt_list[2].get_text(strip=True) if len(dt_list) > 2 else ''
                    
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
                    
                    print(f"    [{idx}] {number} | {date} | {title[:30]}...")
                    
                    # 檢查日期範圍
                    if not self.is_within_date_range(date):
                        skipped_date += 1
                        continue
                    
                    # 檢查關鍵字(包含礙航和射擊)
                    matched_keywords = self.check_keywords(title)
                    if not matched_keywords:
                        print(f"        ⏭️ 無關鍵字匹配")
                        skipped_keyword += 1
                        continue
                    
                    print(f"        ✅ 關鍵字匹配: {', '.join(matched_keywords)}")
                    
                    notices.append({
                        'number': number,
                        'date': date,
                        'title': title,
                        'unit': unit,
                        'link': link,
                        'keywords': matched_keywords,
                        'category': category_name
                    })
                    
                    # 存入資料庫
                    db_data = (
                        unit or "台灣航港局",
                        title,
                        link,
                        date,
                        ', '.join(matched_keywords),
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    )
                    
                    is_new, w_id = self.db_manager.save_warning(db_data, source_type="TW_MPB")
                    
                    if is_new and w_id:
                        self.new_warnings.append(w_id)
                        self.captured_warnings_data.append({
                            'id': w_id,
                            'bureau': unit or "台灣航港局",
                            'title': title,
                            'link': link,
                            'time': date,
                            'keywords': matched_keywords,
                            'source': 'TW_MPB',
                            'category': category_name
                        })
                        print(f"        💾 已存入資料庫 (ID: {w_id})")
                    else:
                        print(f"        ℹ️ 資料已存在")
                    
                except Exception as e:
                    print(f"    ⚠️ 處理項目 {idx} 時出錯: {e}")
                    traceback.print_exc()
                    continue
            
            print(f"    📊 統計: 處理 {processed_count} 筆, 符合條件 {len(notices)} 筆, 日期過濾 {skipped_date} 筆, 關鍵字過濾 {skipped_keyword} 筆")
            
            return {
                'has_data': processed_count > 0,
                'notices': notices,
                'processed': processed_count
            }
            
        except Exception as e:
            print(f"  ❌ 請求台灣航港局第 {page} 頁失敗: {e}")
            traceback.print_exc()
            return {'has_data': False, 'notices': [], 'processed': 0}
    
    def scrape_all_pages(self, max_pages=5):
        """爬取所有頁面"""
        print(f"\n🇹🇼 開始爬取台灣航港局航行警告...")
        print(f"  🎯 目標分類: {', '.join(self.target_categories.values())}")
        
        # 爬取礙航公告和射擊公告
        for category_id, category_name in self.target_categories.items():
            print(f"\n  📋 爬取分類: {category_name} (ID: {category_id})")
            
            for page in range(1, max_pages + 1):
                result = self.get_notices(page, category_id)
                
                # 如果這一頁沒有任何資料,停止爬取
                if not result['has_data']:
                    print(f"    🛑 第 {page} 頁沒有資料,停止爬取此分類")
                    break
                
                # 如果處理的資料數量少於預期,可能已經到最後一頁
                if result['processed'] < 15:  # 預設每頁15筆
                    print(f"    ℹ️ 第 {page} 頁資料不足 ({result['processed']} 筆),可能是最後一頁")
                    break
                
                time.sleep(2)  # 避免請求過快
        
        print(f"\n🇹🇼 台灣航港局爬取完成")
        print(f"  📊 總計新增: {len(self.new_warnings)} 筆警告")
        print(f"  📝 詳細資料: {len(self.captured_warnings_data)} 筆")
        
        return self.new_warnings


# ==================== 3. 修改後的中國海事局爬蟲 ====================
class CNMSANavigationWarningsScraper:
    def __init__(self, db_manager, keyword_manager, teams_notifier, headless=True):
        self.db_manager = db_manager
        self.keyword_manager = keyword_manager
        self.keywords = keyword_manager.get_keywords()
        self.teams_notifier = teams_notifier
        
        print("🇨🇳 初始化中國海事局爬蟲...")
        
        # WebDriver 設定
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
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        
        try:
            # 優先使用環境變數指定的路徑
            manual_path = os.getenv('CHROMEDRIVER_PATH', '')
            
            if manual_path and os.path.exists(manual_path):
                print(f"  ✅ 使用手動指定的 ChromeDriver: {manual_path}")
                service = Service(manual_path)
            else:
                # 方法 1: 使用 webdriver_manager 並禁用 SSL 驗證
                import ssl
                from webdriver_manager.chrome import ChromeDriverManager
                
                # 臨時禁用 SSL 驗證
                os.environ['WDM_SSL_VERIFY'] = '0'
                
                try:
                    service = Service(ChromeDriverManager().install())
                    print("  ✅ 使用 webdriver_manager 下載的 ChromeDriver")
                except Exception as e:
                    print(f"  ⚠️ webdriver_manager 失敗: {e}")
                    print("  🔄 嘗試使用系統已安裝的 ChromeDriver...")
                    
                    # 方法 2: 使用系統路徑中的 chromedriver
                    try:
                        service = Service()
                        print("  ✅ 使用系統路徑的 ChromeDriver")
                    except Exception as e2:
                        print(f"  ⚠️ 系統 ChromeDriver 也失敗: {e2}")
                        print("  🔄 嘗試手動指定 ChromeDriver 路徑...")
                        
                        # 方法 3: 手動指定路徑
                        possible_paths = [
                            r"C:\chromedriver\chromedriver.exe",
                            r"C:\Program Files\chromedriver\chromedriver.exe",
                            r".\chromedriver.exe",
                            "./chromedriver.exe",
                            os.path.join(os.getcwd(), "chromedriver.exe")
                        ]
                        
                        chromedriver_path = None
                        for path in possible_paths:
                            if os.path.exists(path):
                                chromedriver_path = path
                                break
                        
                        if chromedriver_path:
                            service = Service(chromedriver_path)
                            print(f"  ✅ 使用 ChromeDriver: {chromedriver_path}")
                        else:
                            raise Exception(
                                "無法找到 ChromeDriver。請執行以下步驟：\n"
                                "1. 下載 ChromeDriver: https://chromedriver.chromium.org/downloads\n"
                                "2. 將 chromedriver.exe 放到專案目錄\n"
                                "3. 或設定環境變數 CHROMEDRIVER_PATH"
                            )
            
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
        """檢查關鍵字"""
        return [k for k in self.keywords if k.lower() in text.lower()]
    
    def parse_date(self, date_str):
        """解析日期"""
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%Y-%m-%d %H:%M:%S']:
            try: 
                return datetime.strptime(date_str.strip(), fmt)
            except: 
                continue
        return None
    
    def scrape_bureau_warnings(self, bureau_name, bureau_element):
        """抓取單一海事局警告"""
        print(f"  🔍 抓取: {bureau_name}")
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", bureau_element)
            time.sleep(2)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
            items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a")
            
            for item in items:
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
                    
                    try: 
                        publish_time = item.find_element(By.CSS_SELECTOR, ".time").text.strip()
                    except: 
                        match = re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text)
                        publish_time = match.group() if match else ""

                    if publish_time:
                        p_date = self.parse_date(publish_time)
                        if p_date and p_date < self.three_days_ago: 
                            continue

                    # 存入資料庫
                    db_data = (
                        bureau_name, 
                        title, 
                        link, 
                        publish_time, 
                        ', '.join(matched), 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                            'source': 'CN_MSA'
                        })
                        print(f"    ✅ 新警告: {title[:40]}...")
                        
                except Exception as e:
                    print(f"    ⚠️ 處理項目時出錯: {e}")
                    continue
                    
        except Exception as e:
            print(f"  ❌ 抓取 {bureau_name} 錯誤: {e}")
    
    def scrape_all_bureaus(self):
        """爬取所有海事局"""
        print(f"\n🇨🇳 開始爬取中國海事局航行警告...")
        
        try:
            # 載入網頁
            print("  📡 正在載入中國海事局網站...")
            self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
            time.sleep(5)
            
            # 點擊航行警告
            print("  🖱️ 點擊航行警告選項...")
            nav_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '航行警告')]"))
            )
            self.driver.execute_script("arguments[0].click();", nav_btn)
            time.sleep(3)
            
            # 獲取海事局列表
            print("  📋 獲取海事局列表...")
            bureaus = [
                b.text.strip() 
                for b in self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text") 
                if b.text.strip()
            ]
            
            print(f"  📍 找到 {len(bureaus)} 個海事局")
            
            # 遍歷海事局
            for b_name in bureaus:
                try:
                    elem = self.driver.find_element(
                        By.XPATH, 
                        f"//div[@class='nav_lv2_text' and contains(text(), '{b_name}')]"
                    )
                    self.scrape_bureau_warnings(b_name, elem)
                    time.sleep(1)  # 避免請求過快
                except Exception as e:
                    print(f"    ⚠️ 跳過 {b_name}: {e}")
                    continue
            
        except Exception as e:
            print(f"❌ 中國海事局爬取錯誤: {e}")
            traceback.print_exc()
        finally:
            try:
                print("  🔒 關閉 WebDriver...")
                self.driver.quit()
            except:
                pass
        
        print(f"🇨🇳 中國海事局爬取完成，新增 {len(self.new_warnings)} 筆警告")
        return self.new_warnings


# ==================== 4. 統一的多源監控系統 ====================
class UnifiedMaritimeWarningSystem:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', 
                 mail_user=None, mail_pass=None, target_email=None):
        print("🚀 初始化統一海事警告監控系統...")
        
        # 初始化核心組件
        self.keyword_manager = KeywordManager()
        self.db_manager = DatabaseManager()
        self.teams_notifier = UnifiedTeamsNotifier(webhook_url) if webhook_url else None
        self.email_notifier = GmailRelayNotifier(mail_user, mail_pass, target_email)
        
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        
        # 初始化各爬蟲
        self.cn_scraper = CNMSANavigationWarningsScraper(
            self.db_manager, self.keyword_manager, self.teams_notifier
        )
        self.tw_scraper = TWMaritimePortBureauScraper(
            self.db_manager, self.keyword_manager, self.teams_notifier
        )
        
        self.all_new_warnings = []
        self.all_captured_data = []
        
        print("✅ 統一監控系統初始化完成\n")
    
    def run_all_scrapers(self):
        """執行所有爬蟲"""
        start_time = datetime.now()
        
        print(f"{'='*60}")
        print(f"🌊 開始執行多源海事警告監控")
        print(f"{'='*60}")
        
        try:
            # 1. 執行中國海事局爬蟲
            cn_warnings = self.cn_scraper.scrape_all_bureaus()
            self.all_new_warnings.extend(cn_warnings)
            self.all_captured_data.extend(self.cn_scraper.captured_warnings_data)
            
            # 2. 執行台灣航港局爬蟲  
            tw_warnings = self.tw_scraper.scrape_all_pages()
            self.all_new_warnings.extend(tw_warnings)
            self.all_captured_data.extend(self.tw_scraper.captured_warnings_data)
            
            # 3. 發送通知
            if self.enable_teams and self.all_new_warnings:
                self.send_notifications()
            
            # 4. 生成報告
            duration = (datetime.now() - start_time).total_seconds()
            self.generate_final_report(duration)
            
        except Exception as e:
            print(f"❌ 執行過程發生錯誤: {e}")
            traceback.print_exc()
    
    def send_notifications(self):
        """發送通知"""
        if self.send_mode == 'batch':
            # 分別發送各來源的批量通知
            cn_warnings = [w for w in self.all_captured_data if w.get('source') == 'CN_MSA']
            tw_warnings = [w for w in self.all_captured_data if w.get('source') == 'TW_MPB']
            
            if cn_warnings:
                cn_data = []
                for w in cn_warnings:
                    cn_data.append((
                        w['id'], w['bureau'], w['title'], w['link'], 
                        w['time'], ', '.join(w['keywords']), 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                
                if self.teams_notifier.send_batch_notification(tw_data, "TW_MPB"):
                    for w in tw_warnings:
                        self.db_manager.mark_as_notified(w['id'])
    
    def generate_final_report(self, duration):
        """生成最終報告"""
        print(f"\n{'='*60}")
        print(f"📊 執行結果摘要")
        print(f"{'='*60}")
        print(f"⏱️ 總耗時: {duration:.2f} 秒")
        print(f"🇨🇳 中國海事局新警告: {len([w for w in self.all_captured_data if w.get('source') == 'CN_MSA'])} 筆")
        print(f"🇹🇼 台灣航港局新警告: {len([w for w in self.all_captured_data if w.get('source') == 'TW_MPB'])} 筆")
        print(f"📈 總計新警告: {len(self.all_new_warnings)} 筆")
        print(f"{'='*60}")
        
        if self.all_new_warnings:
            # 生成並發送 Email 報告
            json_data, html_data = self._generate_unified_report(duration)
            self.email_notifier.send_trigger_email(json_data, html_data)
            
            # 匯出 Excel
            self.db_manager.export_to_excel()
            print("✅ 報告生成完成")
        else:
            print("ℹ️ 無新警告，跳過報告生成")
    
    def _generate_unified_report(self, duration):
        """生成統一報告"""
        font_style = "font-family: 'Microsoft JhengHei', '微軟正黑體', 'Segoe UI', sans-serif;"
        count = len(self.all_captured_data)
        status_color = "#2E7D32" if count == 0 else "#D9534F"
        
        utc_now = datetime.now(timezone.utc)
        now_str_UTC = utc_now.strftime('%Y-%m-%d %H:%M')
        lt_now = utc_now + timedelta(hours=8)
        now_str_LT = lt_now.strftime('%Y-%m-%d %H:%M')
        
        # 統計各來源數量
        cn_count = len([w for w in self.all_captured_data if w.get('source') == 'CN_MSA'])
        tw_count = len([w for w in self.all_captured_data if w.get('source') == 'TW_MPB'])
        
        html = f"""
        <html><body style="{font_style} color:#333; line-height:1.5;">
            <div style="background:#003366; color:white; padding:20px; border-radius:6px 6px 0 0;">
                <h2 style="margin: 0; font-size: 25px; font-weight: 700; letter-spacing: 0.5px;"> 
                🌊 多源海事警告監控系統
                </h2>
                <div style="margin-top: 8px; font-size: 12px; color: #a3cbe8; font-weight: 500;">
                📅 Last Update: {now_str_LT} (TPE) <span style="opacity: 0.5;">|</span> {now_str_UTC} (UTC)
                </div>
            </div>
            <div style="background:#f8f9fa; border:1px solid #ddd; padding:15px; margin-bottom:20px;">
                <strong style="color:{status_color};">📊 監控報告摘要</strong><br>
                🇨🇳 中國海事局: {cn_count} 個新警告<br>
                🇹🇼 台灣航港局: {tw_count} 個新警告<br>
                <strong>總計: {count} 個新警告</strong>
            </div>
        """
        
        if count > 0:
            html += f"""<table style="width:100%; border-collapse:collapse; font-size:14px; border:1px solid #ddd;">
                <tr style="background:#f0f4f8; text-align:left;">
                    <th style="padding:10px; border-bottom:2px solid #ccc;">來源</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈單位</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">警告標題</th>
                    <th style="padding:10px; border-bottom:2px solid #ccc;">發佈時間</th>
                </tr>"""
            
            for i, item in enumerate(self.all_captured_data):
                bg = "#fff" if i % 2 == 0 else "#f9f9f9"
                source_flag = "🇨🇳" if item.get('source') == 'CN_MSA' else "🇹🇼"
                source_name = "中國海事局" if item.get('source') == 'CN_MSA' else "台灣航港局"
                
                kw_html = "".join([
                    f"<span style='background:#fff3cd; padding:2px 5px; margin-right:5px; border-radius:3px; font-size:12px;'>{k}</span>" 
                    for k in item['keywords']
                ])
                
                html += f"""<tr style="background:{bg};">
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{source_flag} {source_name}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee; font-weight:bold;">{item['bureau']}</td>
                    <td style="padding:10px; border-bottom:1px solid #eee;">
                        <a href="{item['link']}" style="color:#0056b3; text-decoration:none; font-weight:bold;">{item['title']}</a><br>
                        <div style="margin-top:5px;">{kw_html}</div>
                    </td>
                    <td style="padding:10px; border-bottom:1px solid #eee; color:#666;">{item['time']}</td>
                </tr>"""
            html += "</table>"
        else:
            html += "<p style='text-align:center; color:#666; padding:20px;'>本次執行未發現新的航行警告</p>"
        
        html += f"""
            <div style="margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 20px; font-size: 15px; color: #9ca3af; text-align: center; {font_style}">
                <p style="margin: 0;">Wan Hai Lines Ltd. | Marine Technology Division</p>
                <p style="margin: 0;color: blue;">Present by Fleet Risk Department</p>
                <p style="margin: 0 0 0 0;">Multi-Source Maritime Warning System | Automated Monitoring</p>
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
            "new_warnings": self.all_captured_data
        }
        
        return json_data, html


# ==================== 5. 主程式進入點 ====================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🌊 多源海事警告監控系統")
    print("="*60 + "\n")
    
    # 從環境變數讀取設定
    TEAMS_WEBHOOK = os.getenv('TEAMS_WEBHOOK_URL')
    MAIL_USER = os.getenv('MAIL_USER')
    MAIL_PASS = os.getenv('MAIL_PASSWORD')
    TARGET_EMAIL = os.getenv('TARGET_EMAIL')
    
    # 檢查設定
    if not TEAMS_WEBHOOK:
        print("⚠️ 警告: 未設定 TEAMS_WEBHOOK_URL")
    if not MAIL_USER or not MAIL_PASS:
        print("⚠️ 警告: 未設定 Email 帳號密碼")
    if not TARGET_EMAIL:
        print("⚠️ 警告: 未設定 TARGET_EMAIL")
    
    print()
    
    # 初始化統一監控系統
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
