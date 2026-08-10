#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
規則式摘要服務（預設）＋ 選配外部 AI 摘要（透過環境變數啟用，失敗時自動回退規則式）。

規則式摘要盡量回答：
  1. 發生什麼事？ 2. 在哪裡？ 3. 何時開始/結束？ 4. 對商船有何影響？ 5. 建議怎麼做？
不做翻譯（不強制簡轉繁字元），只依內容擷取重點句，保留原始海事術語。
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？;；\n])")

# 句子中出現這些字詞，代表資訊密度高，優先納入摘要
_IMPORTANT_HINTS = (
    "禁止", "禁航", "警戒", "危险", "危險", "实弹", "實彈", "军事", "軍事",
    "演习", "演習", "封锁", "封鎖", "沉船", "沉没", "沉沒", "打捞", "打撈",
    "水下", "施工", "作业", "作業", "有效期", "自", "至", "北纬", "北緯",
    "东经", "東經", "禁止通行", "禁止驶入", "禁止駛入", "限制", "搜救",
)

_RECOMMENDED_ACTION_DEFAULT = "建議船舶航經相關海域前先確認公告內容與有效期間，必要時繞航或提高瞭望警戒。"


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text)]
    return [p for p in parts if p]


def rule_based_summary(title: str, content: str, max_sentences: int = 4) -> str:
    """
    從標題與內文擷取 2~4 句重點，組成繁體中文（保留原始用字）摘要。
    不使用外部 AI，完全規則式、可重現。
    """
    sentences = _split_sentences(content) if content else []

    if not sentences:
        # 沒有內文時，至少用標題組出一句話
        return title.strip() if title else "本則公告未提供詳細內文，請至原始公告頁面確認詳情。"

    scored = []
    for idx, sent in enumerate(sentences):
        score = 0
        for hint in _IMPORTANT_HINTS:
            if hint in sent:
                score += 1
        # 越前面的句子通常越重要（公告慣例：起手先講事由與範圍）
        position_bonus = max(0, 3 - idx) * 0.5
        score += position_bonus
        # 太短的句子（如純標點或編號）不利於摘要
        if len(sent) < 4:
            score -= 5
        scored.append((score, idx, sent))

    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[: max(2, min(max_sentences, len(scored)))]
    # 依原文順序輸出，保持閱讀邏輯
    top_sorted = sorted(top, key=lambda x: x[1])

    summary = " ".join(s[2] for s in top_sorted)
    return summary.strip()


def recommended_action(risk_level: str) -> str:
    mapping = {
        "CRITICAL": "立即評估航線是否需繞航，通知船長與運務部PIC，密切追蹤公告更新。",
        "HIGH": "建議調整航線或提高警戒，安排船舶避開公告區域，持續關注後續公告。",
        "MEDIUM": "留意公告區域與有效期間，通過時提高瞭望與 AIS 報告頻率。",
        "LOW": "維持常規監控，通過前再次確認公告是否仍然有效。",
        "INFO": "僅供備查，暫無需特別行動。",
    }
    return mapping.get(risk_level, _RECOMMENDED_ACTION_DEFAULT)


def summarize(
    title: str,
    content: str,
    risk_level: str = "INFO",
    ai_client: Optional[object] = None,
) -> str:
    """
    對外統一入口。預設使用規則式摘要；若 ENABLE_AI_SUMMARY=true 且提供 ai_client，
    會嘗試呼叫 ai_client.summarize(title, content)，失敗則自動回退規則式摘要。
    """
    enable_ai = os.getenv("ENABLE_AI_SUMMARY", "false").lower() == "true"

    if enable_ai and ai_client is not None:
        try:
            ai_summary = ai_client.summarize(title, content)
            if ai_summary:
                return ai_summary.strip()
        except Exception:
            # AI 摘要失敗，回退規則式摘要，不得中斷流程
            pass

    return rule_based_summary(title, content)
