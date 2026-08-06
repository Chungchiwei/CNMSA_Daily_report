#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detail 頁內容清理與正規化工具。

負責把海事局公告詳細頁的原始 HTML，轉成乾淨、可用於關鍵字比對與摘要的純文字，
並提供 Unicode / 全形半形 / 簡繁 正規化工具，供 KeywordManager 與風險評分服務共用。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from bs4 import BeautifulSoup

# 常見導覽列 / 頁尾 / 無關區塊的 class 或 id 關鍵字，清理時會整段移除
_NOISE_HINTS = (
    "nav", "menu", "footer", "header", "breadcrumb", "sidebar",
    "banner", "copyright", "share", "print", "backtop", "adv",
    "pagination", "page-turn", "friendlink",
)

_NOISE_TAGS = ("script", "style", "noscript", "iframe", "svg")


def clean_html(html: str, content_selectors: Iterable[str] = ()) -> str:
    """
    將公告詳細頁 HTML 轉成清理後的純文字。

    Args:
        html: 原始 HTML
        content_selectors: 候選的內文容器 CSS selector（依序嘗試），
            若都找不到則退回整個 <body> 並嘗試剔除導覽/頁尾雜訊。

    Returns:
        清理後的純文字（保留換行以利閱讀，但已移除 script/style/導覽等雜訊）
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    content_node = None
    for selector in content_selectors:
        try:
            found = soup.select_one(selector)
        except Exception:
            found = None
        if found is not None:
            content_node = found
            break

    if content_node is None:
        content_node = soup.body or soup

    # 移除疑似導覽/頁尾/廣告等雜訊區塊（依 class/id 關鍵字比對）
    for tag in list(content_node.find_all(True)):
        attrs_text = " ".join(
            str(tag.get(attr, "")) for attr in ("class", "id")
        ).lower()
        if any(hint in attrs_text for hint in _NOISE_HINTS):
            tag.decompose()

    text = content_node.get_text("\n", strip=True)
    # 壓縮連續空白行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def normalize_text(text: str) -> str:
    """
    正規化文字供關鍵字比對使用：
    - Unicode NFKC 正規化（含全形/半形統一）
    - 大小寫正規化（轉小寫比對，呼叫端保留原文顯示）
    - 去除多餘空白
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.lower()


def combine_for_matching(
    *,
    title: str = "",
    notice_number: str = "",
    bureau: str = "",
    summary: str = "",
    full_content: str = "",
    affected_area: str = "",
    operation_type: str = "",
) -> str:
    """
    依 claude.md 五之要求，組合多欄位文字後再進行關鍵字判斷，
    避免僅比對列表標題就放棄公告。
    """
    parts = [title, notice_number, bureau, summary, full_content, affected_area, operation_type]
    return "\n".join(p for p in parts if p)


def truncate(text: str, max_len: int = 4000) -> str:
    """避免內容過長造成通知/資料庫欄位過大。"""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


# 撤銷／作廢關鍵詞：命中時應標記 status=CANCELLED，但仍須保留該筆公告
# （撤銷本身就是需要通知的事件，不得因排除詞機制被整筆丟棄）。
_CANCELLATION_HINTS = (
    "撤销", "撤銷", "作废", "作廢", "废止", "廢止", "取消本公告", "取消該公告", "取消该公告",
    "本航警取消", "本航警作废", "予以取消", "予以撤销", "予以撤銷",
)

# 展延／延期：語意上仍是「有效公告」，只是效期或內容被更新，不改變 status，
# 但可用於未來擴充 category/operation_type 判斷。
_EXTENSION_HINTS = ("展延", "展期", "順延", "顺延", "延期", "延长有效期", "延長有效期")


def infer_status(text: str) -> str:
    """
    規則式狀態判斷：只判斷「是否為撤銷／作廢」，其餘一律回傳 ACTIVE
    （UPCOMING/EXPIRED 需要比對生效日期與現在時間，屬於未來加強項目，
    目前資料來源多數未提供結構化生效日期，先以 ACTIVE 為預設不影響既有通知邏輯）。
    """
    if not text:
        return "ACTIVE"
    norm = normalize_text(text)
    for hint in _CANCELLATION_HINTS:
        if normalize_text(hint) in norm:
            return "CANCELLED"
    return "ACTIVE"
