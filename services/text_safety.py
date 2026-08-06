#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
URL 驗證與 Markdown 逸出（Teams Adaptive Card TextBlock 支援有限 Markdown，
若不逸出，惡意標題如 "[點我](javascript:...)" 會在卡片中變成可點擊連結）。
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

_MD_SPECIAL_RE = re.compile(r"([\[\]\(\)\*_`~])")


def safe_url(url: Optional[str]) -> Optional[str]:
    """只允許 http/https 且具備 netloc 的網址，否則回傳 None（呼叫端應據此隱藏按鈕）。"""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url.strip()
    return None


def escape_markdown(text: Optional[str]) -> str:
    """逸出 Adaptive Card TextBlock 會解讀的 Markdown 特殊字元，避免文字被渲染成連結/格式。"""
    if not text:
        return ""
    return _MD_SPECIAL_RE.sub(r"\\\1", str(text))


def truncate(text: Optional[str], max_len: int) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def google_maps_url(lat: float, lon: float) -> str:
    return f"https://maps.google.com/maps?q={lat:.6f},{lon:.6f}"
