#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
風險等級排序/顯示共用邏輯。

Email（templates/email_report.py）與 Teams（notifications/teams_notifier.py）
都需要「依風險等級排序」與「判斷有效風險等級」，抽出成共用模組避免兩邊各自維護
一份可能不同步的邏輯（claude.md 第二階段第三節之要求：排除重複程式）。
"""

from __future__ import annotations

from typing import Dict, List

RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4, "CANCELLED": 5, "EXPIRED": 5}

VALID_LEVELS = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


def risk_level_of(warning: Dict) -> str:
    """回傳警告的「有效呈現風險等級」：撤銷/取消一律視為 CANCELLED，優先於原始 risk_level。"""
    if warning.get("status") in ("CANCELLED",):
        return "CANCELLED"
    if warning.get("status") == "EXPIRED":
        return "EXPIRED"
    level = (warning.get("risk_level") or "INFO").upper()
    return level if level in VALID_LEVELS else "INFO"


def sort_by_risk(warnings: List[Dict]) -> List[Dict]:
    return sorted(warnings, key=lambda w: (RISK_ORDER.get(risk_level_of(w), 9), w.get("title", "")))
