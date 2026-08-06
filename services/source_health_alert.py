#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
來源健康異常偵測（claude.md 第二階段第七節）。

「抓取失敗」與「今日沒有新警告」是兩件不同的事，必須明確區分並各自產生對應通知，
不得把抓取失敗顯示成「今日沒有新警告」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

FAILURE_STATUSES = {"EMPTY", "BLOCKED", "PARSE_ERROR", "CONNECTION_ERROR"}
STALE_THRESHOLD_DAYS = int(os.getenv("SOURCE_STALE_THRESHOLD_DAYS", "14"))


@dataclass
class SourceAnomaly:
    reason: str
    failed_sources: List[Dict] = field(default_factory=list)
    healthy_sources: List[Dict] = field(default_factory=list)
    newest_publish_date: Optional[str] = None
    fallback_used: bool = False
    suggested_actions: List[str] = field(default_factory=list)
    detected_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def detect_anomaly(health_reports: List, threshold_days: int = STALE_THRESHOLD_DAYS) -> Optional[SourceAnomaly]:
    """
    health_reports: cn_sources.base.SourceHealthReport 物件列表（來自 CNSourceRegistry.run()）。
    回傳 SourceAnomaly 若判定異常，否則回傳 None。
    """
    if not health_reports:
        return None

    active_reports = [r for r in health_reports if r.final_status.value != "DISABLED"]
    if not active_reports:
        return None

    failed = [r for r in active_reports if r.final_status.value in FAILURE_STATUSES]
    healthy = [r for r in active_reports if r.final_status.value in ("HEALTHY", "PARTIAL")]

    # 情況一：所有已啟用來源皆失敗
    if len(failed) == len(active_reports):
        return SourceAnomaly(
            reason="所有中國海事局來源本次執行均無法正常取得資料",
            failed_sources=[_report_to_dict(r) for r in failed],
            healthy_sources=[],
            fallback_used=False,
            suggested_actions=[
                "檢查網路連線與 DNS 是否可解析 msa.gov.cn 系列網域",
                "確認官方網站是否封鎖來源 IP 或需要驗證碼",
                "以 --source cn --save-debug 重新執行並檢查 debug/ 目錄快照",
                "檢查 config/maritime_sources.json 的 selectors 是否仍符合官方網站結構",
            ],
        )

    # 情況二：部分來源失敗但仍有健康來源（備援來源生效）——記錄但不視為系統級異常，
    # 除非失敗來源比例過半，值得留意
    if failed and len(failed) >= len(active_reports) / 2:
        return SourceAnomaly(
            reason=f"中國海事局 {len(failed)}/{len(active_reports)} 個來源異常，其餘來源仍可運作（備援生效）",
            failed_sources=[_report_to_dict(r) for r in failed],
            healthy_sources=[_report_to_dict(r) for r in healthy],
            fallback_used=bool(healthy),
            suggested_actions=[
                "檢查失敗來源的 debug 快照，確認是否為官方網站改版",
                "確認失敗來源是否被暫時封鎖（BLOCKED）",
            ],
        )

    # 情況三：資料明顯過舊（即使狀態顯示 HEALTHY，但抓到的最新公告日期異常久遠）
    newest_dates = [r.newest_publish_date for r in healthy if r.newest_publish_date]
    if newest_dates:
        try:
            newest = max(datetime.strptime(d[:10], "%Y-%m-%d") for d in newest_dates)
            age_days = (datetime.now() - newest).days
            if age_days > threshold_days:
                return SourceAnomaly(
                    reason=f"中國海事局來源最新公告日期為 {newest.strftime('%Y-%m-%d')}，"
                           f"已超過 {threshold_days} 天未見更新，可能為解析失效或官方公告確實停更",
                    failed_sources=[],
                    healthy_sources=[_report_to_dict(r) for r in healthy],
                    newest_publish_date=newest.strftime("%Y-%m-%d"),
                    fallback_used=False,
                    suggested_actions=[
                        "確認官方網站是否確實長期無新公告（屬正常情況）",
                        "以 --source cn --save-debug 重新執行，確認 selector 是否仍正確擷取最新公告",
                    ],
                )
        except Exception:
            pass

    return None


def _report_to_dict(report) -> Dict:
    row = report.to_row()
    return {
        "source_name": row.get("來源"),
        "status": row.get("狀態"),
        "error_summary": row.get("錯誤"),
        "newest_publish_date": row.get("最新公告日期"),
    }
