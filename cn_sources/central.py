#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中國海事局中央入口來源（weather.jsp）。

與舊版 n8n_msa_monitor.CNMSANavigationWarningsScraper 的差異：
  1. 純解析邏輯（parse_bureau_menu_html / parse_warning_list_html）拆成模組層級函式，
     不依賴 Selenium，可用 fixture HTML 直接單元測試。
  2. selector 改為候選清單，逐一嘗試，而不是只認定 .right_main / .nav_lv2_text。
  3. 任一海事局或整個中央入口失敗都不會拋出例外中止整個 CN 爬取流程
     （由 BaseMaritimeSource.run() 統一包裹例外並回報健康狀態）。
  4. 關鍵字判斷延後到抓取完詳細內文之後才進行（實際比對邏輯在 registry 層，
     這裡只負責回傳 raw items + cleaned_content，不在此篩選）。

注意：本檔仍需要在有網路且可連線 msa.gov.cn 的環境下用 Selenium 實際驗證，
目前沙箱環境無法連線中國官方網站（見交付文件的「已知限制」章節）。
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable, Dict, List, Optional

from bs4 import BeautifulSoup

from cn_sources.base import BaseMaritimeSource
from services.content_cleaner import clean_html, truncate

DEFAULT_LIST_CONTAINER_SELECTORS = [".right_main", "#right_main", ".conMain", ".list_1"]
DEFAULT_BUREAU_MENU_SELECTORS = [".nav_lv2_list .nav_lv2_text", ".nav_lv2_text", ".leftMenu li a"]
DEFAULT_NAV_TRIGGER_TEXTS = ["航行警告", "航行通警告", "航行通告"]

FALLBACK_BUREAUS = [
    "天津海事局", "河北海事局", "辽宁海事局", "山东海事局",
    "上海海事局", "江苏海事局", "浙江海事局", "福建海事局",
    "广东海事局", "广西海事局", "海南海事局", "长江海事局",
    "黑龙江海事局", "连云港海事局", "深圳海事局",
]


def parse_warning_list_html(html: str, container_selectors: Optional[List[str]] = None) -> List[Dict]:
    """
    從中央入口單一海事局的列表頁 HTML 解析出 [{title, link, publish_time}, ...]。
    純函式，不依賴 Selenium，供單元測試使用。
    """
    if not html:
        return []
    container_selectors = container_selectors or DEFAULT_LIST_CONTAINER_SELECTORS
    soup = BeautifulSoup(html, "html.parser")

    container = None
    for sel in container_selectors:
        container = soup.select_one(sel)
        if container is not None:
            break
    if container is None:
        return []

    items = []
    for a_tag in container.find_all("a"):
        title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
        title = re.sub(r"\s*\d{4}-\d{2}-\d{2}\s*$", "", title).strip()
        if not title:
            continue

        href = a_tag.get("href", "")
        if href.startswith("/"):
            href = f"https://www.msa.gov.cn{href}"
        elif not href.startswith("http"):
            href = ""

        publish_time = ""
        time_span = a_tag.find(class_="time")
        if time_span:
            publish_time = time_span.get_text(strip=True)
        else:
            parent = a_tag.parent
            for _ in range(3):
                if parent is None:
                    break
                spans = parent.find_all(["span", "em", "i"])
                for sp in spans:
                    sp_text = sp.get_text(strip=True)
                    if re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", sp_text):
                        publish_time = sp_text
                        break
                if publish_time:
                    break
                parent = parent.parent

        if not publish_time:
            m = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", a_tag.get_text())
            if m:
                publish_time = m.group()

        items.append({"title": title, "link": href, "publish_time": publish_time})

    return items


def parse_bureau_menu_html(html: str, menu_selectors: Optional[List[str]] = None) -> List[str]:
    """從中央入口首頁解析出海事局選單名稱清單。"""
    if not html:
        return []
    menu_selectors = menu_selectors or DEFAULT_BUREAU_MENU_SELECTORS
    soup = BeautifulSoup(html, "html.parser")

    for sel in menu_selectors:
        elements = soup.select(sel)
        names = [e.get_text(strip=True) for e in elements if e.get_text(strip=True)]
        if names:
            return names
    return []


class CentralMSASource(BaseMaritimeSource):
    """
    中央入口來源。使用 Selenium（JS 動態選單），但所有選擇器改為候選清單，
    且任何步驟失敗都會被 BaseMaritimeSource.run() 攔截、記錄健康狀態，不會中止其他來源。
    """

    def __init__(self, source_id: str, config: Dict, coordinate_extractor: Optional[Callable] = None,
                 headless: bool = True, days: int = 7, save_debug: bool = False,
                 debug_dir: str = "debug"):
        super().__init__(source_id, config)
        self.selectors = config.get("selectors", {})
        self._coordinate_extractor = coordinate_extractor or (lambda text: [])
        self.headless = headless
        self.days = days
        self.save_debug = save_debug
        self.debug_dir = debug_dir
        self.driver = None
        self._last_selector_strategy = ""

    # ------------------------------------------------------------------
    def _init_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(120)
        return driver

    def _save_debug_snapshot(self, name: str, html: str):
        if not self.save_debug:
            return
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            path = os.path.join(self.debug_dir, f"{self.source_id}_{name}_{int(time.time())}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html or "")
        except Exception:
            pass

    def fetch_list(self) -> List[Dict]:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self.driver = self._init_driver()
        wait = WebDriverWait(self.driver, 20)

        nav_texts = self.selectors.get("nav_trigger_text", DEFAULT_NAV_TRIGGER_TEXTS)
        menu_selectors = self.selectors.get("bureau_menu", DEFAULT_BUREAU_MENU_SELECTORS)
        container_selectors = self.selectors.get("list_container", DEFAULT_LIST_CONTAINER_SELECTORS)

        self.driver.get(self.list_url)
        time.sleep(5)

        clicked = False
        for nav_text in nav_texts:
            try:
                nav_btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, f"//span[contains(text(), '{nav_text}')]"))
                )
                self.driver.execute_script("arguments[0].click();", nav_btn)
                time.sleep(3)
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            self._save_debug_snapshot("nav_click_failed", self.driver.page_source)
            raise ConnectionError("找不到「航行警告」導覽按鈕，候選文字皆未命中，可能頁面結構已變更")

        bureaus: List[str] = []
        for menu_sel in menu_selectors:
            elements = self.driver.find_elements(By.CSS_SELECTOR, menu_sel)
            names = [e.text.strip() for e in elements if e.text.strip()]
            if names:
                bureaus = names
                self._last_selector_strategy = f"menu={menu_sel!r}"
                break

        if not bureaus:
            bureaus = FALLBACK_BUREAUS

        all_items: List[Dict] = []
        for bureau_name in bureaus:
            try:
                elem = self.driver.find_element(
                    By.XPATH, f"//div[@class='nav_lv2_text' and contains(text(), '{bureau_name}')]"
                )
                self.driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", elem)
                time.sleep(2)

                html = self.driver.page_source
                items = []
                for container_sel in container_selectors:
                    items = parse_warning_list_html(html, [container_sel])
                    if items:
                        self._last_selector_strategy += f" list={container_sel!r}"
                        break

                if not items:
                    self._save_debug_snapshot(f"empty_{bureau_name}", html)

                for item in items:
                    item["bureau"] = bureau_name
                all_items.extend(items)
            except Exception:
                # 單一海事局失敗不影響其他海事局
                continue

        return all_items

    def parse_list(self, raw) -> List[Dict]:
        return parse_warning_list_html(raw, self.selectors.get("list_container"))

    def fetch_detail(self, item: Dict) -> str:
        link = item.get("link", "")
        if not link or link.startswith("javascript"):
            return ""
        try:
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.set_page_load_timeout(15)
            self.driver.get(link)
            time.sleep(1.5)
            html = self.driver.page_source
            return html
        finally:
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
                    self.driver.set_page_load_timeout(120)
            except Exception:
                pass

    def parse_detail(self, item: Dict, raw_detail: str) -> Dict:
        cleaned = clean_html(raw_detail, content_selectors=self.selectors.get("detail_container", []))
        return {"raw_content": truncate(raw_detail, 20000), "cleaned_content": truncate(cleaned, 6000)}

    def enrich_item(self, raw_item: Dict) -> Optional[Dict]:
        title = raw_item.get("title", "")
        link = raw_item.get("link", "")
        publish_time = raw_item.get("publish_time", "")
        bureau = raw_item.get("bureau", self.source_name)

        detail_html = ""
        try:
            detail_html = self.fetch_detail(raw_item)
        except Exception:
            detail_html = ""

        if detail_html:
            parsed = self.parse_detail(raw_item, detail_html)
        else:
            parsed = {"raw_content": "", "cleaned_content": ""}

        combined_for_coords = f"{title}\n{parsed.get('cleaned_content', '')}"
        coordinates = self._coordinate_extractor(combined_for_coords)

        return {
            "title": title,
            "link": link,
            "publish_time": publish_time,
            "bureau": bureau,
            "source_type": self.source_type,
            "source_country": self.source_country,
            "source_name": self.source_name,
            "raw_content": parsed.get("raw_content", ""),
            "cleaned_content": parsed.get("cleaned_content", ""),
            "coordinates": coordinates,
            "parser_strategy": self._last_selector_strategy,
        }

    def normalize_item(self, item: Dict):
        raise NotImplementedError("由 registry 統一 normalize")

    def close(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
