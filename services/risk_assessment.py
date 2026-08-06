#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
規則式（deterministic）相關性與風險評分服務。

不依賴外部 AI，所有分數皆可由 scoring_reasons 逐項解釋，方便日後除錯與稽核。
評分邏輯讀取 keywords_config.json 內既有的 categories / priority_keywords /
exclusion_patterns（先前這些欄位已存在於設定檔，但程式從未真正使用）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from services.content_cleaner import normalize_text

RISK_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")

# 類別權重：越接近「立即威脅商船安全」的類別權重越高
_CATEGORY_WEIGHTS = {
    "武器發射": 30,
    "軍事演習": 25,
    "危險作業": 20,
    "區域管制": 18,
    "船艦類型": 12,
    "海事通告": 6,
    "航空器": 8,
    "偵測設備": 6,
    "台灣特有": 10,
    "中國特有": 8,
}
_DEFAULT_CATEGORY_WEIGHT = 8

_PRIORITY_WEIGHTS = {"high": 25, "medium": 12, "low": 5}


@dataclass
class RiskAssessmentResult:
    relevance_score: int = 0
    risk_score: int = 0
    risk_level: str = "INFO"
    matched_categories: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)
    scoring_reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0
    action_required: bool = False
    is_excluded: bool = False

    def to_dict(self) -> Dict:
        return {
            "relevance_score": self.relevance_score,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "matched_categories": self.matched_categories,
            "matched_keywords": self.matched_keywords,
            "scoring_reasons": self.scoring_reasons,
            "confidence": self.confidence,
            "action_required": self.action_required,
            "is_excluded": self.is_excluded,
        }


class RiskAssessmentService:
    """依 keywords_config.json 的分類/優先權/排除詞計算風險分數。"""

    def __init__(self, keywords_config_path: str = "keywords_config.json"):
        self.keywords_config_path = keywords_config_path
        self.categories: Dict[str, List[str]] = {}
        self.priority_keywords: Dict[str, List[str]] = {}
        self.exclusion_patterns: List[str] = []
        self._keyword_to_categories: Dict[str, List[str]] = {}
        self._keyword_to_priority: Dict[str, str] = {}
        self._load()

    def _load(self):
        try:
            with open(self.keywords_config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        self.categories = data.get("categories", {}) or {}
        self.priority_keywords = data.get("priority_keywords", {}) or {}
        self.exclusion_patterns = data.get("exclusion_patterns", []) or []

        self._keyword_to_categories = {}
        for category, keywords in self.categories.items():
            for kw in keywords:
                norm = normalize_text(kw)
                self._keyword_to_categories.setdefault(norm, []).append(category)

        self._keyword_to_priority = {}
        for level in ("high", "medium", "low"):
            for kw in self.priority_keywords.get(level, []):
                self._keyword_to_priority[normalize_text(kw)] = level

    def reload(self):
        self._load()

    def _find_hits(self, text: str, keywords: List[str]) -> List[str]:
        if not text:
            return []
        norm_text = normalize_text(text)
        hits = []
        for kw in keywords:
            norm_kw = normalize_text(kw)
            if norm_kw and norm_kw in norm_text:
                hits.append(kw)
        return hits

    def _check_exclusion(self, combined_text: str) -> Optional[str]:
        norm_text = normalize_text(combined_text)
        for pattern in self.exclusion_patterns:
            norm_pattern = normalize_text(pattern)
            if norm_pattern and norm_pattern in norm_text:
                return pattern
        return None

    def assess(
        self,
        *,
        title: str = "",
        content: str = "",
        source_keywords: Optional[List[str]] = None,
        has_coordinates: bool = False,
        status: str = "UNKNOWN",
    ) -> RiskAssessmentResult:
        """
        計算單筆公告的相關性與風險分數。

        Args:
            title: 公告標題
            content: 已清理的詳細內文（含摘要等）
            source_keywords: 該來源適用的關鍵字清單（例如 KeywordManager.get_keywords_by_source）
                若未提供，則使用設定檔中全部分類的關鍵字。
            has_coordinates: 是否解析出經緯度
            status: 公告狀態（ACTIVE/UPCOMING/EXPIRED/CANCELLED/UNKNOWN），
                CANCELLED 會大幅降低風險分數。
        """
        result = RiskAssessmentResult()
        reasons: List[str] = []

        all_candidate_keywords = source_keywords if source_keywords else [
            kw for kws in self.categories.values() for kw in kws
        ]

        title_hits = self._find_hits(title, all_candidate_keywords)
        content_hits = self._find_hits(content, all_candidate_keywords)

        matched_keywords = sorted(set(title_hits) | set(content_hits))
        result.matched_keywords = matched_keywords

        if not matched_keywords:
            result.confidence = 0.3 if content else 0.15
            reasons.append("標題與內文均未命中任何關鍵字")
            result.scoring_reasons = reasons
            result.risk_level = "INFO"
            return result

        # ---- 類別命中 ----
        matched_categories = set()
        for kw in matched_keywords:
            for cat in self._keyword_to_categories.get(normalize_text(kw), []):
                matched_categories.add(cat)
        result.matched_categories = sorted(matched_categories)

        score = 0.0

        # 標題命中權重較高
        for kw in title_hits:
            score += 15
            reasons.append(f"標題命中關鍵字「{kw}」(+15)")
        # 內文命中權重較低，但仍計分（claude.md 五之核心需求：內文命中也要保留）
        content_only_hits = [k for k in content_hits if k not in title_hits]
        for kw in content_only_hits:
            score += 8
            reasons.append(f"內文命中關鍵字「{kw}」(+8)")

        # 類別權重（每個命中類別只計一次，避免關鍵字爆量洗分數）
        for cat in matched_categories:
            weight = _CATEGORY_WEIGHTS.get(cat, _DEFAULT_CATEGORY_WEIGHT)
            score += weight
            reasons.append(f"命中「{cat}」類別 (+{weight})")

        # 優先權關鍵字加權
        for kw in matched_keywords:
            priority = self._keyword_to_priority.get(normalize_text(kw))
            if priority:
                bonus = _PRIORITY_WEIGHTS.get(priority, 0)
                score += bonus
                reasons.append(f"「{kw}」為{priority.upper()}優先關鍵字 (+{bonus})")

        # 座標存在代表有具體危險區域，風險應提高
        if has_coordinates:
            score += 10
            reasons.append("公告含具體經緯度座標 (+10)")

        # 類別多樣性加分（同時涉及多種風險類型）
        diversity_bonus = min(20, max(0, (len(matched_categories) - 1) * 5))
        if diversity_bonus:
            score += diversity_bonus
            reasons.append(f"命中 {len(matched_categories)} 個風險類別，複合風險加分 (+{diversity_bonus})")

        relevance_score = int(min(100, round(score)))
        risk_score = relevance_score

        # 排除詞：可能是誤報（測試、演習結束、取消等）
        combined_text = f"{title}\n{content}"
        excluded_pattern = self._check_exclusion(combined_text)
        if excluded_pattern:
            result.is_excluded = True
            risk_score = int(risk_score * 0.2)
            reasons.append(f"命中排除詞「{excluded_pattern}」，風險等級大幅下修")

        if status == "CANCELLED":
            risk_score = int(risk_score * 0.3)
            reasons.append("公告狀態為已撤銷/取消，風險下修")
        elif status == "EXPIRED":
            risk_score = int(risk_score * 0.5)
            reasons.append("公告已逾有效期間，風險下修")

        risk_score = max(0, min(100, risk_score))

        if risk_score >= 85:
            risk_level = "CRITICAL"
        elif risk_score >= 65:
            risk_level = "HIGH"
        elif risk_score >= 40:
            risk_level = "MEDIUM"
        elif risk_score >= 15:
            risk_level = "LOW"
        else:
            risk_level = "INFO"

        confidence = 0.5
        if content:
            confidence += 0.3
        if len(matched_categories) >= 2:
            confidence += 0.1
        if has_coordinates:
            confidence += 0.1
        confidence = round(min(1.0, confidence), 2)

        result.relevance_score = relevance_score
        result.risk_score = risk_score
        result.risk_level = risk_level
        result.confidence = confidence
        result.action_required = risk_level in ("CRITICAL", "HIGH")
        result.scoring_reasons = reasons
        return result
