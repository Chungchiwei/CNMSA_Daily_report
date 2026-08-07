#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中國海事局多來源 registry：讀取 config/maritime_sources.json，逐一執行各來源，
在「抓取完詳細內文之後」才進行關鍵字判斷與風險評分（claude.md 五、六），
且任一來源失敗都不影響其他來源（claude.md 三）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from cn_sources.base import SourceHealthReport, SourceHealthStatus
from cn_sources.central import CentralMSASource
from cn_sources.provincial import ProvincialMSASource
from services.content_cleaner import combine_for_matching, normalize_text, infer_status
from services.risk_assessment import RiskAssessmentService

_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"]


def parse_date(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt)
        except Exception:
            continue
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


@dataclass
class RegistryRunResult:
    today: List[Dict] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    health_reports: List[SourceHealthReport] = field(default_factory=list)

    @property
    def all_sources_failed(self) -> bool:
        """所有已啟用來源皆非 HEALTHY/PARTIAL，代表資料來源整體異常，
        而不是單純沒有新公告（claude.md 十四之核心要求）。"""
        active_reports = [r for r in self.health_reports if r.final_status != SourceHealthStatus.DISABLED]
        if not active_reports:
            return False
        return all(
            r.final_status in (
                SourceHealthStatus.EMPTY,
                SourceHealthStatus.BLOCKED,
                SourceHealthStatus.PARSE_ERROR,
                SourceHealthStatus.CONNECTION_ERROR,
            )
            for r in active_reports
        )


def _build_source(source_id: str, cfg: Dict, coordinate_extractor, headless: bool, save_debug: bool, debug_dir: str):
    adapter = cfg.get("adapter", "provincial_requests")
    if adapter == "central_selenium":
        return CentralMSASource(
            source_id, cfg, coordinate_extractor=coordinate_extractor,
            headless=headless, save_debug=save_debug, debug_dir=debug_dir,
        )
    return ProvincialMSASource(
        source_id, cfg, coordinate_extractor=coordinate_extractor,
        save_debug=save_debug, debug_dir=debug_dir,
    )


class CNSourceRegistry:
    def __init__(
        self,
        config_path: str,
        keyword_manager,
        coordinate_extractor: Optional[Callable] = None,
        risk_service: Optional[RiskAssessmentService] = None,
        headless: bool = True,
        save_debug: bool = False,
        debug_dir: str = "debug",
        days: int = 7,
    ):
        self.config_path = config_path
        self.keyword_manager = keyword_manager
        self.coordinate_extractor = coordinate_extractor or (lambda text: [])
        self.risk_service = risk_service or RiskAssessmentService()
        self.headless = headless
        self.save_debug = save_debug
        self.debug_dir = debug_dir
        self.days = days
        self.cutoff_date = datetime.now() - timedelta(days=days)
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self._raw_config = self._load_config()

    def _load_config(self) -> Dict:
        if not os.path.exists(self.config_path):
            return {"sources": []}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"sources": []}

    def list_sources(self) -> List[Dict]:
        return self._raw_config.get("sources", [])

    def run(self, only_source_ids: Optional[List[str]] = None) -> RegistryRunResult:
        result = RegistryRunResult()
        source_keywords = self.keyword_manager.get_keywords_by_source("CN_MSA")

        for cfg in self.list_sources():
            source_id = cfg.get("source_id")
            if only_source_ids and source_id not in only_source_ids:
                continue

            adapter = _build_source(
                source_id, cfg, self.coordinate_extractor, self.headless, self.save_debug, self.debug_dir
            )
            run_result = adapter.run()
            result.health_reports.append(run_result.report)

            for raw_item in run_result.items:
                normalized = self._process_item(raw_item, source_keywords)
                if normalized is None:
                    continue
                p_date = parse_date(normalized.get("publish_time", ""))
                if p_date and p_date < self.cutoff_date:
                    continue
                if p_date and p_date >= self.today_start:
                    result.today.append(normalized)
                else:
                    result.history.append(normalized)

        return result

    def _process_item(self, raw_item: Dict, source_keywords: List[str]) -> Optional[Dict]:
        title = raw_item.get("title", "")
        cleaned_content = raw_item.get("cleaned_content", "")
        bureau = raw_item.get("bureau", "")

        # claude.md 五：組合多欄位文字後再判斷關鍵字，標題無關鍵字但內文有仍須保留
        combined_text = combine_for_matching(
            title=title, bureau=bureau, full_content=cleaned_content
        )
        norm_combined = normalize_text(combined_text)
        matched_keywords = [kw for kw in source_keywords if normalize_text(kw) and normalize_text(kw) in norm_combined]

        if not matched_keywords:
            return None

        status = infer_status(combined_text)

        assessment = self.risk_service.assess(
            title=title,
            content=cleaned_content,
            source_keywords=source_keywords,
            has_coordinates=bool(raw_item.get("coordinates")),
            status=status,
        )

        if assessment.is_excluded and assessment.risk_level == "INFO" and status != "CANCELLED":
            # 命中排除詞且風險極低（例如純測試貼文），不視為新警訊。
            # 撤銷／作廢公告則例外保留（撤銷本身就是需要通知的事件，見 claude.md 八）。
            return None

        merged = dict(raw_item)
        merged["keywords"] = matched_keywords
        merged["keywords_matched"] = matched_keywords
        merged["status"] = status
        merged.update(assessment.to_dict())
        merged["status"] = status  # assessment.to_dict() 不含 status，確保不被覆蓋為預設值
        return merged
