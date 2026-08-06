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
from datetime import datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from cn_sources.base import BaseMaritimeSource
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
    ):
        super().__init__(source_id, config)
        self.selectors = config.get("selectors", {})
        self.needs_verification = bool(config.get("needs_verification", False))
        self._coordinate_extractor = coordinate_extractor or (lambda text: [])
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": _DEFAULT_UA})
        self._ssl_verify = resolve_ssl_verify()
        self._last_selector_strategy = ""

    # ------------------------------------------------------------------
    def _get(self, url: str) -> requests.Response:
        resp = self._session.get(url, timeout=_DEFAULT_TIMEOUT, verify=self._ssl_verify)
        resp.raise_for_status()
        return resp

    def fetch_list(self) -> List[Dict]:
        candidate_urls = [self.list_url] + list(self.config.get("alt_list_urls", []))
        last_exc = None
        for url in candidate_urls:
            if not url:
                continue
            try:
                resp = self._get(url)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            items = self.parse_list(resp.text)
            if items:
                return items
        if last_exc is not None:
            raise ConnectionError(f"{self.source_name} 所有候選列表網址均無法連線: {last_exc}")
        return []

    def parse_list(self, raw: str) -> List[Dict]:
        if not raw:
            return []
        soup = BeautifulSoup(raw, "html.parser")

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
                    title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
                    href = a_tag.get("href", "")
                    if not title or not href:
                        continue
                    full_url = urljoin(self.base_url or self.list_url, href)
                    if not _is_safe_url(full_url):
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
