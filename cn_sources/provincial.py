#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地方海事局來源的通用 requests + BeautifulSoup adapter。

設計原則（claude.md 三）：
  - 不寫死單一 class name，改用設定檔提供的候選 selector 清單，逐一嘗試。
  - 優先 requests/BeautifulSoup；Selenium 僅作為 JS 頁面備援（本檔預設不使用 Selenium，
    若日後確認某地方海事局為純 JS 渲染頁面，可在 registry 中改用 Selenium adapter）。
  - 任何步驟失敗都回傳空結果並記錄原因，不得整批中止（由 base.run() 統一包裹）。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from cn_sources.base import BaseMaritimeSource, SourceBlockedError
from services.content_cleaner import clean_html, truncate
from services.ssl_config import resolve_ssl_verify

_DEFAULT_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
_DEFAULT_UA = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

_DATE_PATTERNS = [
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"),
]


def _normalize_url_for_compare(url: str) -> str:
    """比較兩個網址是否指向同一頁面時使用：去掉 query string/fragment 與結尾斜線。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _is_safe_url(url: str) -> bool:
    """只允許 http/https，避免 javascript:/data: 等被當成連結使用。"""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https")


class ProvincialMSASource(BaseMaritimeSource):
    """單一地方海事局的通用 adapter，行為完全由 config 驅動。"""

    def __init__(
        self,
        source_id: str,
        config: Dict,
        coordinate_extractor: Optional[Callable[[str], List]] = None,
        session: Optional[requests.Session] = None,
        save_debug: bool = False,
        debug_dir: str = "debug",
    ):
        super().__init__(source_id, config)
        self.selectors = config.get("selectors", {})
        self.needs_verification = bool(config.get("needs_verification", False))
        self._coordinate_extractor = coordinate_extractor or (lambda text: [])
        self._session = session or requests.Session()
        # 補上完整的瀏覽器慣用 headers（不只 User-Agent）。許多政府網站的反爬機制會
        # 檢查是否具備一般瀏覽器都會送出的 Accept/Accept-Language/Referer 等欄位，
        # 缺少時直接視為爬蟲並回 403，這與是否使用 Selenium 無關，屬於一般 HTTP 禮貌性欄位。
        self._session.headers.update({
            "User-Agent": _DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": config.get("base_url") or config.get("list_url") or "",
        })
        self._ssl_verify = resolve_ssl_verify()
        self._last_selector_strategy = ""
        self.save_debug = save_debug
        self.debug_dir = debug_dir

    # ------------------------------------------------------------------
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

    @staticmethod
    def _fix_response_encoding(resp: requests.Response) -> None:
        """
        修正中文亂碼問題（claude.md 二之7：Email/Teams 缺少內文＝亂碼也算此類問題）。

        requests 依 RFC 2616，若 HTTP 回應標頭的 Content-Type 沒有明確標出
        charset（許多中國海事局頁面只回 "text/html"，charset 寫在 <meta> 標籤
        裡而非 HTTP 標頭），requests 會把 response.encoding 預設猜成
        ISO-8859-1，之後 .text 用這個錯誤編碼解碼 UTF-8/GBK 位元組，就會產生
        像「è¾¾é¥è¾341」這種亂碼。改用 requests 內建的 apparent_encoding
        （以回應內容位元組本身做編碼偵測，charset-normalizer/chardet），
        只在 HTTP 標頭沒有明確 charset 時才覆蓋，避免蓋掉真的有標頭的情況。
        """
        content_type = resp.headers.get("Content-Type", "")
        if "charset=" in content_type.lower():
            return
        try:
            detected = resp.apparent_encoding
        except Exception:
            detected = None
        if detected:
            resp.encoding = detected

    def _get(self, url: str) -> requests.Response:
        resp = self._session.get(url, timeout=_DEFAULT_TIMEOUT, verify=self._ssl_verify)
        self._fix_response_encoding(resp)
        if resp.status_code in (401, 403, 429, 451):
            # 站台有回應但拒絕存取，很可能是 WAF／反爬封鎖（也可能是來源 IP 本身被
            # 政府網站封鎖，例如雲端/機房 IP 段），與單純連不上網路的意義不同，
            # 獨立分類成 BLOCKED 以利判讀（claude.md 十四）。
            self._save_debug_snapshot(
                f"blocked_{resp.status_code}",
                f"URL: {url}\nStatus: {resp.status_code}\nHeaders: {dict(resp.headers)}\n\n{resp.text[:5000]}",
            )
            raise SourceBlockedError(
                f"{self.source_name} 回應 HTTP {resp.status_code}，疑似遭反爬蟲機制封鎖: {url}",
                status_code=resp.status_code,
            )
        resp.raise_for_status()
        return resp

    def fetch_list(self) -> List[Dict]:
        candidate_urls = [self.list_url] + list(self.config.get("alt_list_urls", []))
        last_exc = None
        last_blocked: Optional[SourceBlockedError] = None
        for url in candidate_urls:
            if not url:
                continue
            try:
                resp = self._get(url)
            except SourceBlockedError as exc:
                last_blocked = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            items = self.parse_list(resp.text, page_url=getattr(resp, "url", "") or url)
            if items:
                return items
            # HTTP 成功但解析不到任何項目：保存快照方便之後調整 selector
            # （claude.md 四：HTTP 200 但 0 筆不得視為成功，需可診斷）
            self._save_debug_snapshot("empty_list", resp.text)
        if last_blocked is not None:
            raise last_blocked
        if last_exc is not None:
            raise ConnectionError(f"{self.source_name} 所有候選列表網址均無法連線: {last_exc}")
        return []

    def parse_list(self, raw: str, page_url: str = "") -> List[Dict]:
        if not raw:
            return []
        soup = BeautifulSoup(raw, "html.parser")

        # 2026-08-07 使用者實機回報部分連結會連到海事局主頁而非公告詳細頁。
        # 原本 urljoin 一律用 self.base_url（網域根目錄，不含路徑），若列表頁本身
        # 的 <a href> 是相對於「目前頁面所在資料夾」而非網域根目錄（例如列表頁在
        # /8e10ea74.../index.jhtml 底下，公告連結是 4bc12xxx/detail.jhtml 這種
        # 不含開頭斜線的相對路徑），用網域根目錄當基準會把路徑前綴解析掉，變成
        # 錯誤網址。改用「實際被抓取的頁面網址」（含完整路徑）當 urljoin 基準，
        # 對本來就是絕對路徑或含開頭斜線的 href 沒有影響，只修正真正的相對路徑。
        resolve_base = page_url or self.list_url or self.base_url

        # 同一頁面若沒有真的公告清單容器，selector fallback 可能誤選到「相關連結／
        # 本局首頁」之類的區塊，裡面的 <a> 全部指回列表頁本身。這類項目一律視為
        # 雜訊丟棄，不當成公告（真正的公告連結一定會指向與列表頁不同的詳細頁）。
        self_url_norm = _normalize_url_for_compare(resolve_base)

        container_candidates = self.selectors.get("list_container", []) or [None]
        item_candidates = self.selectors.get("item", ["a"])
        date_candidates = self.selectors.get("date", [])

        for container_sel in container_candidates:
            container = soup.select_one(container_sel) if container_sel else soup
            if container is None:
                continue

            for item_sel in item_candidates:
                try:
                    anchors = container.select(item_sel)
                except Exception:
                    anchors = []
                # item_sel 可能選到非 <a> 容器，這裡再往下找 <a>
                resolved_anchors = []
                for el in anchors:
                    if el.name == "a":
                        resolved_anchors.append(el)
                    else:
                        found = el.find("a")
                        if found:
                            resolved_anchors.append(found)

                if not resolved_anchors:
                    continue

                items = []
                for a_tag in resolved_anchors:
                    # 中央入口與部分地方海事局（msa.gov.cn 主網域下的頁面）實際結構是
                    # <a><span class="name">標題</span><span class="time">日期</span></a>，
                    # 優先用 span.name 取標題，避免直接取整個 <a> 文字把日期黏在標題後面
                    # （central.py 也用同一套邏輯，見該檔案的說明）。
                    name_span = a_tag.find(class_="name")
                    if name_span and name_span.get_text(strip=True):
                        title = name_span.get_text(strip=True)
                    else:
                        title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
                    href = a_tag.get("href", "")
                    if not title or not href:
                        continue
                    full_url = urljoin(resolve_base, href)
                    if not _is_safe_url(full_url):
                        continue
                    if _normalize_url_for_compare(full_url) == self_url_norm:
                        # 連結指回列表頁本身（很可能是選單/首頁連結被誤判成公告項目），跳過
                        continue

                    publish_time = self._extract_date(a_tag, date_candidates)

                    items.append({
                        "title": title,
                        "link": full_url,
                        "publish_time": publish_time,
                    })

                if items:
                    self._last_selector_strategy = f"container={container_sel!r} item={item_sel!r}"
                    return items

        return []

    def _extract_date(self, a_tag, date_candidates: List[str]) -> str:
        for date_sel in date_candidates:
            try:
                sibling_container = a_tag.parent
                if sibling_container is not None:
                    date_el = sibling_container.select_one(date_sel)
                    if date_el:
                        text = date_el.get_text(strip=True)
                        if text:
                            return text
            except Exception:
                continue

        # fallback：在整個 <a> 或其父層文字中用正規表示式找日期
        search_text = a_tag.get_text(" ", strip=True)
        parent = a_tag.parent
        if parent is not None:
            search_text += " " + parent.get_text(" ", strip=True)
        for pattern in _DATE_PATTERNS:
            m = pattern.search(search_text)
            if m:
                return m.group()
        return ""

    def fetch_detail(self, item: Dict) -> str:
        link = item.get("link", "")
        if not _is_safe_url(link):
            return ""
        resp = self._get(link)
        return resp.text

    def parse_detail(self, item: Dict, raw_detail: str) -> Dict:
        selectors = self.selectors.get("detail_container", [])
        cleaned = clean_html(raw_detail, content_selectors=selectors)
        return {
            "raw_content": truncate(raw_detail, 20000),
            "cleaned_content": truncate(cleaned, 6000),
        }

    def enrich_item(self, raw_item: Dict) -> Optional[Dict]:
        title = raw_item.get("title", "")
        link = raw_item.get("link", "")
        publish_time = raw_item.get("publish_time", "")

        detail_html = ""
        cleaned_content = ""
        try:
            detail_html = self.fetch_detail(raw_item)
        except Exception:
            detail_html = ""

        if detail_html:
            parsed_detail = self.parse_detail(raw_item, detail_html)
            cleaned_content = parsed_detail.get("cleaned_content", "")
        else:
            parsed_detail = {"raw_content": "", "cleaned_content": ""}

        combined_text_for_coords = f"{title}\n{cleaned_content}"
        coordinates = self._coordinate_extractor(combined_text_for_coords)

        return {
            "title": title,
            "link": link,
            "publish_time": publish_time,
            "bureau": self.config.get("bureau", self.source_name),
            "source_type": self.source_type,
            "source_country": self.source_country,
            "source_name": self.source_name,
            "raw_content": parsed_detail.get("raw_content", ""),
            "cleaned_content": cleaned_content,
            "coordinates": coordinates,
            "parser_strategy": self._last_selector_strategy,
            "needs_verification": self.needs_verification,
        }

    def normalize_item(self, item: Dict):
        raise NotImplementedError("由 registry 統一 normalize，provincial adapter 只負責抓取與清理")
