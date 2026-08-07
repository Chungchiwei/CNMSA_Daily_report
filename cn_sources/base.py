#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多來源海事警告抓取的共用抽象介面與健康狀態模型。

claude.md 第三節要求：中國海事局來源不得只依賴單一中央入口，
需支援「多來源、可設定、可降級」架構，且單一來源失敗不得中止整體爬取工作。
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class SourceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    BLOCKED = "BLOCKED"
    PARSE_ERROR = "PARSE_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    DISABLED = "DISABLED"


class SourceBlockedError(Exception):
    """網站有回應但拒絕存取（HTTP 401/403/429/451 等），代表可能被 WAF／反爬機制封鎖，
    語意上不同於「連不上網路」的 CONNECTION_ERROR，需獨立分類方便判讀（claude.md 十四）。"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SourceHealthReport:
    source_id: str
    source_name: str
    request_url: str = ""
    http_status: Optional[int] = None
    selector_strategy: str = ""
    list_item_count: int = 0
    detail_success_count: int = 0
    newest_publish_date: Optional[str] = None
    elapsed_seconds: float = 0.0
    retry_count: int = 0
    final_status: SourceHealthStatus = SourceHealthStatus.EMPTY
    error_type: str = ""
    error_summary: str = ""

    def to_row(self) -> Dict:
        return {
            "來源": self.source_name,
            "狀態": self.final_status.value,
            "列表筆數": self.list_item_count,
            "詳情成功": self.detail_success_count,
            "最新公告日期": self.newest_publish_date or "-",
            "耗時(秒)": round(self.elapsed_seconds, 1),
            "重試次數": self.retry_count,
            "錯誤": self.error_summary or "-",
        }


@dataclass
class NormalizedWarningItem:
    """對應 claude.md 七之詳細頁資料模型（僅列出目前爬蟲階段會填入的欄位，
    其餘欄位如 risk_score 等由 services.risk_assessment 於後續階段補上）。"""

    source_type: str
    source_country: str
    source_name: str
    issuing_bureau: str
    title: str
    canonical_url: str = ""
    source_item_id: str = ""
    publish_datetime: str = ""
    raw_content: str = ""
    cleaned_content: str = ""
    coordinates: List = field(default_factory=list)
    parser_strategy: str = ""
    status: str = "UNKNOWN"


class BaseMaritimeSource:
    """所有海事警告來源（中央/地方海事局/其他國家）共用的抽象介面。"""

    source_type: str = "CN_MSA"
    source_country: str = "CN"
    source_name: str = "unknown"
    list_url: str = ""
    base_url: str = ""

    def __init__(self, source_id: str, config: Optional[Dict] = None):
        self.source_id = source_id
        self.config = config or {}
        self.source_name = self.config.get("source_name", self.source_name)
        self.source_type = self.config.get("source_type", self.source_type)
        self.source_country = self.config.get("source_country", self.source_country)
        self.list_url = self.config.get("list_url", self.list_url)
        self.base_url = self.config.get("base_url", self.base_url)

    # ---- 子類別需實作 ----
    def fetch_list(self) -> List[Dict]:
        """抓取列表頁，回傳 [{title, link, publish_time, ...}, ...]。"""
        raise NotImplementedError

    def parse_list(self, raw) -> List[Dict]:
        raise NotImplementedError

    def fetch_detail(self, item: Dict) -> str:
        """抓取單筆公告詳細頁 HTML/純文字。"""
        raise NotImplementedError

    def parse_detail(self, item: Dict, raw_detail: str) -> Dict:
        raise NotImplementedError

    def normalize_item(self, item: Dict) -> NormalizedWarningItem:
        raise NotImplementedError

    def health_check(self) -> SourceHealthStatus:
        """快速健康檢查（預設：嘗試抓取列表，依結果回報狀態）。"""
        try:
            items = self.fetch_list()
            if items:
                return SourceHealthStatus.HEALTHY
            return SourceHealthStatus.EMPTY
        except Exception:
            return SourceHealthStatus.CONNECTION_ERROR

    # ---- 共用執行流程：run() 包住所有例外，確保單一來源失敗不影響其他來源 ----
    def run(self) -> "SourceRunResult":
        report = SourceHealthReport(
            source_id=self.source_id,
            source_name=self.source_name,
            request_url=self.list_url,
        )
        started = time.time()
        items: List[Dict] = []

        if not self.config.get("enabled", True):
            report.final_status = SourceHealthStatus.DISABLED
            report.elapsed_seconds = time.time() - started
            return SourceRunResult(items=[], report=report)

        try:
            raw_list = self.fetch_list()
            report.selector_strategy = getattr(self, "_last_selector_strategy", "")
            if raw_list is None:
                raw_list = []

            report.list_item_count = len(raw_list)

            if not raw_list:
                report.final_status = SourceHealthStatus.EMPTY
                report.elapsed_seconds = time.time() - started
                return SourceRunResult(items=[], report=report)

            detail_success = 0
            dates: List[str] = []
            for raw_item in raw_list:
                try:
                    enriched = self.enrich_item(raw_item)
                    if enriched is not None:
                        items.append(enriched)
                        detail_success += 1
                        pub = enriched.get("publish_time")
                        if pub:
                            dates.append(pub)
                except Exception as exc:  # noqa: BLE001 - 單筆失敗不可中止整批
                    report.retry_count += 1
                    continue

            report.detail_success_count = detail_success
            report.newest_publish_date = max(dates) if dates else None

            if detail_success == 0:
                report.final_status = SourceHealthStatus.PARSE_ERROR
                report.error_summary = "HTTP 成功但解析不到任何有效項目"
            elif detail_success < report.list_item_count:
                report.final_status = SourceHealthStatus.PARTIAL
            else:
                report.final_status = SourceHealthStatus.HEALTHY

        except SourceBlockedError as exc:
            report.final_status = SourceHealthStatus.BLOCKED
            report.error_type = type(exc).__name__
            report.error_summary = str(exc)[:200]
        except (ConnectionError, OSError, TimeoutError) as exc:
            report.final_status = SourceHealthStatus.CONNECTION_ERROR
            report.error_type = type(exc).__name__
            report.error_summary = str(exc)[:200]
        except Exception as exc:  # noqa: BLE001 - 保底：任何例外都不得往外拋
            report.final_status = SourceHealthStatus.PARSE_ERROR
            report.error_type = type(exc).__name__
            report.error_summary = str(exc)[:200]
            report.error_summary += " | " + traceback.format_exc(limit=1).strip()[:200]
        finally:
            report.elapsed_seconds = time.time() - started
            try:
                self.close()
            except Exception:
                pass

        return SourceRunResult(items=items, report=report)

    def enrich_item(self, raw_item: Dict) -> Optional[Dict]:
        """預設實作：抓詳細頁 → 清理 → 回傳合併後的 dict。子類別可覆寫。"""
        raise NotImplementedError

    def close(self):
        """釋放資源（例如 Selenium WebDriver）。預設為 no-op，子類別可覆寫。"""
        pass


@dataclass
class SourceRunResult:
    items: List[Dict]
    report: SourceHealthReport
