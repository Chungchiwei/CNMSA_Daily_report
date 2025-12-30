import os
import sys
import logging
import warnings
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import re

# ==================== 日誌和警告抑制 ====================
warnings.filterwarnings('ignore')
logging.getLogger('selenium').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

# 在 Windows 上過濾 Chrome 錯誤訊息
if os.name == 'nt':
    class ErrorFilter:
        def __init__(self, stream):
            self.stream = stream
            
        def write(self, text):
            # 過濾掉 Chrome 的內部錯誤訊息
            if any(keyword in text for keyword in [
                'ERROR:net\\socket\\ssl_client_socket_impl.cc',
                'ERROR:google_apis\\gcm\\engine',
                'Failed to connect to MCS endpoint',
                'DEPRECATED_ENDPOINT',
                'handshake failed',
                'Registration response error'
            ]):
                return
            self.stream.write(text)
            
        def flush(self):
            self.stream.flush()
    
    sys.stderr = ErrorFilter(sys.stderr)

# 設定環境變數
os.environ['WDM_LOG_LEVEL'] = '0'
os.environ['WDM_PRINT_FIRST_LINE'] = 'False'

# ==================== 導入其他模組 ====================
from database_manager import DatabaseManager
from teams_notifier import TeamsNotifier
from keyword_manager import KeywordManager

class MSANavigationWarningsScraper:
    def __init__(self, webhook_url=None, enable_teams=True, send_mode='batch', headless=True):
        """
        初始化爬蟲
        webhook_url: Teams Webhook URL
        enable_teams: 是否啟用 Teams 通知
        send_mode: 'individual' (逐個發送) 或 'batch' (批量發送)
        headless: 是否使用無頭模式
        """
        print("初始化海事局航行警告爬蟲...")
        
        # 載入關鍵字
        self.keyword_manager = KeywordManager()
        self.keywords = self.keyword_manager.get_keywords()
        print(f"✓ 已載入 {len(self.keywords)} 個關鍵字")
        
        # 初始化資料庫管理器
        self.db_manager = DatabaseManager()
        
        # 初始化 Teams 通知器
        self.enable_teams = enable_teams and webhook_url
        self.send_mode = send_mode
        if self.enable_teams:
            self.teams_notifier = TeamsNotifier(webhook_url)
            print("✓ Teams 通知已啟用")
        else:
            self.teams_notifier = None
            print("✗ Teams 通知未啟用")
        
        # 初始化瀏覽器選項
        options = webdriver.ChromeOptions()
        
        # 基本選項
        if headless:
            options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # 抑制錯誤訊息
        options.add_argument('--disable-logging')
        options.add_argument('--log-level=3')
        options.add_argument('--silent')
        
        # 禁用不需要的功能
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-background-networking')
        options.add_argument('--disable-sync')
        options.add_argument('--disable-translate')
        options.add_argument('--disable-default-apps')
        options.add_argument('--disable-infobars')
        
        # 設定視窗大小
        options.add_argument('--window-size=1920,1080')
        
        # 設定 User Agent
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 實驗性選項
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        # 設定偏好
        prefs = {
            'profile.default_content_setting_values': {
                'notifications': 2,
                'geolocation': 2,
            }
        }
        options.add_experimental_option('prefs', prefs)
        
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 10)
        
        # 計算三天前的日期
        self.three_days_ago = datetime.now() - timedelta(days=3)
        
        # 儲存新發現的警告 ID
        self.new_warnings = []
        
        print("✓ 瀏覽器初始化完成")
    
    def check_keywords(self, text):
        """檢查文字是否包含關鍵字"""
        matched = []
        for keyword in self.keywords:
            if keyword.lower() in text.lower():
                matched.append(keyword)
        return matched
    
    def parse_date(self, date_str):
        """解析日期字串"""
        try:
            date_formats = [
                '%Y-%m-%d',
                '%Y/%m/%d',
                '%Y年%m月%d日',
                '%Y-%m-%d %H:%M:%S',
                '%Y/%m/%d %H:%M:%S',
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except:
                    continue
                    
            date_match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', date_str)
            if date_match:
                year, month, day = date_match.groups()
                return datetime(int(year), int(month), int(day))
                
        except Exception as e:
            pass
        
        return None
    
    def scrape_bureau_warnings(self, bureau_name, bureau_element):
        """抓取特定海事局的警告"""
        print(f"\n正在抓取: {bureau_name}")
        
        try:
            # 滾動到元素可見
            self.driver.execute_script("arguments[0].scrollIntoView(true);", bureau_element)
            time.sleep(0.5)
            
            # 使用 JavaScript 點擊
            self.driver.execute_script("arguments[0].click();", bureau_element)
            time.sleep(2)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "right_main")))
            time.sleep(1)
            
            try:
                # 直接查找所有 <a> 標籤
                warning_items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a[href*='article.do']")
                
                # 如果沒找到，嘗試其他選擇器
                if not warning_items:
                    warning_items = self.driver.find_elements(By.CSS_SELECTOR, ".right_main a")
                
                print(f"找到 {len(warning_items)} 個項目")
                
                for item in warning_items:
                    try:
                        # 獲取標題 - 優先從 span[title] 獲取
                        title = ''
                        try:
                            title_span = item.find_element(By.CSS_SELECTOR, "span[title]")
                            title = title_span.get_attribute('title').strip()
                        except:
                            pass
                        
                        # 如果沒有 title 屬性，從文字內容獲取
                        if not title:
                            title = item.text.strip()
                        
                        # 移除日期部分
                        title = re.sub(r'\s*\d{4}-\d{2}-\d{2}\s*$', '', title)
                        
                        if not title:
                            continue
                        
                        # 檢查關鍵字
                        matched_keywords = self.check_keywords(title)
                        
                        if not matched_keywords:
                            continue
                        
                        # 獲取連結
                        link = item.get_attribute('href') or ''
                        
                        # 修正相對路徑
                        if link:
                            if link.startswith('/'):
                                link = f"https://www.msa.gov.cn{link}"
                            elif not link.startswith('http'):
                                if link.startswith('javascript:') or link.startswith('#'):
                                    link = "https://www.msa.gov.cn/page/outter/weather.jsp"
                                else:
                                    link = f"https://www.msa.gov.cn/{link}"
                        else:
                            link = "https://www.msa.gov.cn/page/outter/weather.jsp"
                        
                        # 獲取發布時間
                        publish_time = ''
                        try:
                            time_span = item.find_element(By.CSS_SELECTOR, ".time, span.time")
                            publish_time = time_span.text.strip()
                        except:
                            # 如果找不到 time span，從標題中提取日期
                            date_match = re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', item.text)
                            if date_match:
                                publish_time = date_match.group()
                        
                        # 檢查日期是否在三天內
                        if publish_time:
                            parsed_date = self.parse_date(publish_time)
                            if parsed_date and parsed_date < self.three_days_ago:
                                continue
                        
                        # 儲存到資料庫
                        data = (
                            bureau_name,
                            title,
                            link,
                            publish_time,
                            ', '.join(matched_keywords),
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        )
                        
                        is_new, warning_id = self.db_manager.save_warning(data)
                        
                        if is_new and warning_id:
                            self.new_warnings.append(warning_id)
                            print(f"  ✓ 發現新警告 (ID: {warning_id}): {title[:50]}")
                            
                            # 如果啟用 Teams 且為逐個發送模式
                            if self.enable_teams and self.send_mode == 'individual':
                                warning_data = (warning_id,) + data
                                success = self.teams_notifier.send_warning_notification(warning_data)
                                if success:
                                    self.db_manager.mark_as_notified(warning_id)
                                time.sleep(1)
                        
                    except Exception as e:
                        print(f"  處理項目時出錯: {e}")
                        continue
                        
            except Exception as e:
                print(f"查找警告列表時出錯: {e}")
                
        except Exception as e:
            print(f"抓取 {bureau_name} 時出錯: {e}")
    
    def send_batch_notifications(self):
        """批量發送 Teams 通知 (只發送新警告)"""
        if not self.enable_teams or not self.new_warnings:
            print("\n沒有新警告需要發送通知")
            return
        
        print(f"\n準備批量發送 {len(self.new_warnings)} 個新警告通知...")
        
        # 獲取新警告的詳細資訊
        warnings_to_send = []
        for warning_id in self.new_warnings:
            unnotified = self.db_manager.get_unnotified_warnings()
            for warning in unnotified:
                if warning[0] == warning_id:
                    warnings_to_send.append(warning)
                    break
        
        if warnings_to_send:
            success = self.teams_notifier.send_batch_notification(warnings_to_send)
            
            if success:
                # 標記所有為已通知
                for warning_id in self.new_warnings:
                    self.db_manager.mark_as_notified(warning_id)
                print(f"✓ 已發送 {len(warnings_to_send)} 個新警告通知")
            else:
                print("✗ 批量通知發送失敗")
    
    def run(self):
        """執行主程式"""
        start_time = datetime.now()
        
        try:
            print("\n" + "=" * 60)
            print("中國海事局航行警告自動抓取程式")
            print("=" * 60)
            print(f"執行時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"監控關鍵字: {len(self.keywords)} 個")
            print(f"通知模式: {'批量發送' if self.send_mode == 'batch' else '逐個發送'}")
            print("=" * 60)
            
            print("\n正在訪問海事局網站...")
            self.driver.get('https://www.msa.gov.cn/page/outter/weather.jsp')
            time.sleep(3)
            
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "left_nav")))
            
            print("尋找航行警告選單...")
            nav_warning = self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(), '航行警告')]"))
            )
            
            # 使用 JavaScript 點擊
            self.driver.execute_script("arguments[0].click();", nav_warning)
            time.sleep(2)
            
            bureaus = self.driver.find_elements(By.CSS_SELECTOR, ".nav_lv2_list .nav_lv2_text")
            print(f"找到 {len(bureaus)} 個海事局")
            
            bureau_list = []
            for bureau in bureaus:
                bureau_name = bureau.text.strip()
                if bureau_name:
                    bureau_list.append(bureau_name)
            
            for bureau_name in bureau_list:
                try:
                    # 重新獲取元素（避免 stale element）
                    bureau_element = self.driver.find_element(
                        By.XPATH, 
                        f"//div[@class='nav_lv2_text' and contains(text(), '{bureau_name}')]"
                    )
                    self.scrape_bureau_warnings(bureau_name, bureau_element)
                    time.sleep(1)
                except Exception as e:
                    print(f"處理 {bureau_name} 時出錯: {e}")
                    continue
            
            # 如果是批量發送模式，在這裡統一發送
            if self.send_mode == 'batch':
                self.send_batch_notifications()
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            print("\n" + "=" * 60)
            print("抓取完成")
            print("=" * 60)
            print(f"發現新警告: {len(self.new_warnings)} 個")
            print(f"執行時間: {duration:.2f} 秒")
            print(f"完成時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 60)
            
            # 只在有新警告時匯出 Excel 和發送統計
            if len(self.new_warnings) > 0:
                print("\n正在匯出 Excel 報表...")
                self.db_manager.export_to_excel()
                
                # 發送統計摘要到 Teams
                if self.enable_teams:
                    print("發送統計摘要到 Teams...")
                    stats = self.db_manager.get_statistics()
                    stats['new_warnings'] = len(self.new_warnings)
                    stats['execution_time'] = f"{duration:.2f} 秒"
                    self.teams_notifier.send_summary_notification(stats)
            else:
                print("\n未發現新警告，不發送通知")
            
        except Exception as e:
            print(f"\n✗ 執行時發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            
        finally:
            print("\n關閉瀏覽器...")
            self.driver.quit()


if __name__ == "__main__":
    # ========== 設定區 ==========
    # 請替換成你的 Teams Webhook URL
    TEAMS_WEBHOOK_URL = 'https://default2b20eccf1c1e43ce93400edfe3a226.6f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/f59bfeccf30041d5b8a51cbd4ee617fe/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=zJiQpFVAzZyaag3zbAmzpfy1yXWW3gZ2AcAMQUpOEBQ'
    
    # 是否啟用 Teams 通知
    ENABLE_TEAMS = True
    
    # 發送模式: 'individual' (逐個發送) 或 'batch' (批量發送)
    SEND_MODE = 'batch'
    
    # 是否使用無頭模式 (定時執行建議設為 True)
    HEADLESS = True  # 測試時設為 False 可以看到瀏覽器
    # ===========================
    
    scraper = MSANavigationWarningsScraper(
        webhook_url=TEAMS_WEBHOOK_URL,
        enable_teams=ENABLE_TEAMS,
        send_mode=SEND_MODE,
        headless=HEADLESS
    )
    
    scraper.run()
    
    print("\n程式執行完畢！按 Enter 鍵退出...")
    input()
