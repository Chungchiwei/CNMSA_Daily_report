#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Microsoft Teams Adaptive Card 通知（第二階段重構版）。

設計原則：
  - build_adaptive_card_payload() 為純函式，不連網路，方便單元測試。
  - TeamsNotifier.send_batch() 才會實際打 HTTP，測試時一律 mock/不使用真實 webhook。
  - 依風險等級排序、最多顯示 MAX_CARDS_PER_BATCH 筆，其餘顯示「另有 N 筆」。
  - 所有 URL 皆驗證 http/https；不安全的 URL 直接不產生按鈕（而不是導向猜測網址）。
  - 標題/摘要等外部文字一律做 Markdown 逸出，避免 TextBlock 被注入連結/格式。
  - 單筆過長不得造成整批失敗：逐欄位截斷。
  - HTTP 429/5xx 允許重試一次（含短暫退避），其餘狀態碼視為失敗但不拋出例外。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from services.risk_presentation import risk_level_of, sort_by_risk
from services.text_safety import escape_markdown, google_maps_url, safe_url, truncate
from services.ssl_config import resolve_ssl_verify

MAX_CARDS_PER_BATCH = 8
TITLE_MAX_LEN = 120
SUMMARY_MAX_LEN = 220
ACTION_MAX_LEN = 150

RISK_ADAPTIVE_COLOR = {
    "CRITICAL": "Attention",
    "HIGH": "Attention",
    "MEDIUM": "Warning",
    "LOW": "Accent",
    "INFO": "Default",
    "CANCELLED": "Default",
    "EXPIRED": "Default",
}

RISK_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "⚪",
    "CANCELLED": "⚫",
    "EXPIRED": "⚫",
}

STATUS_LABELS = {
    "ACTIVE": "生效中",
    "UPCOMING": "即將生效",
    "EXPIRED": "已逾期",
    "CANCELLED": "已撤銷/取消",
    "UNKNOWN": "狀態未知",
}

SOURCE_LABELS = {
    "CN_MSA": ("🇨🇳", "中國海事局", "https://www.msa.gov.cn/page/outter/weather.jsp"),
    "TW_MPB": ("🇹🇼", "台灣航港局", "https://www.motcmpb.gov.tw/Information/Notice?SiteId=1&NodeId=483"),
    "UKMTO":  ("🇬🇧", "UKMTO", "https://www.ukmto.org/recent-incidents"),
}


def _text_block(text: str, **kwargs) -> Dict:
    block = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(kwargs)
    return block


def _warning_card_elements(w: Dict, idx: int) -> (List[Dict], List[Dict]):
    level = risk_level_of(w)
    color = RISK_ADAPTIVE_COLOR.get(level, "Default")
    emoji = RISK_EMOJI.get(level, "⚪")

    title = escape_markdown(truncate(w.get("title", "N/A"), TITLE_MAX_LEN))
    bureau = escape_markdown(w.get("bureau", w.get("issuing_bureau", "N/A")))
    publish_time = escape_markdown(w.get("time", w.get("publish_time", "N/A")))
    status_label = STATUS_LABELS.get(w.get("status", "UNKNOWN"), "狀態未知")

    effective_start = w.get("effective_start", "")
    effective_end = w.get("effective_end", "")
    effective_text = (
        f"{effective_start or '未知'} ~ {effective_end or '未知'}"
        if (effective_start or effective_end) else "未提供明確有效期間"
    )

    affected_area = escape_markdown(w.get("affected_waters", "") or w.get("bureau", ""))
    summary = escape_markdown(truncate(w.get("summary_zh_tw") or w.get("cleaned_content", ""), SUMMARY_MAX_LEN)) \
        or "本則公告未提供詳細摘要，請點選查看原文。"
    action = escape_markdown(truncate(w.get("recommended_action", ""), ACTION_MAX_LEN)) or "請依原始公告內容評估行動。"

    elements = [
        _text_block(
            f"{emoji} **[{level}]** {idx}. {title}",
            weight="Bolder", size="Medium", color=color, spacing="Medium",
        ),
        _text_block(
            f"來源：{bureau} ｜ 發布：{publish_time} ｜ 狀態：{status_label}",
            size="Small", isSubtle=True,
        ),
        _text_block(f"有效期間：{effective_text}｜影響海域：{affected_area}", size="Small", isSubtle=True),
        _text_block(summary, size="Small"),
        _text_block(f"建議行動：{action}", size="Small", weight="Bolder", color="Good"),
    ]

    actions = []
    link = safe_url(w.get("link"))
    if link:
        actions.append({"type": "Action.OpenUrl", "title": f"📄 查看原文 {idx}", "url": link})

    coords = w.get("coordinates") or []
    if coords:
        try:
            lat, lon = float(coords[0][0]), float(coords[0][1])
            actions.append({
                "type": "Action.OpenUrl",
                "title": f"🗺️ 查看地圖 {idx}",
                "url": google_maps_url(lat, lon),
            })
        except Exception:
            pass  # 座標格式異常時不顯示地圖按鈕，不得因此中止整張卡片

    return elements, actions


def build_adaptive_card_payload(
    warnings: List[Dict],
    batch_title: str,
    source_home_url: str = "",
) -> Optional[Dict]:
    """
    純函式：組出 Adaptive Card 訊息 payload。不連網路，方便測試。
    warnings 應已依風險排序；本函式仍會再排序一次以確保呼叫端疏漏時仍正確。
    回傳 None 代表沒有可顯示的內容（呼叫端應視為不需發送）。
    """
    if not warnings:
        return None

    sorted_warnings = sort_by_risk(warnings)
    shown = sorted_warnings[:MAX_CARDS_PER_BATCH]
    remaining = len(sorted_warnings) - len(shown)

    highest_level = risk_level_of(shown[0])
    header_color = RISK_ADAPTIVE_COLOR.get(highest_level, "Default")

    body = [
        _text_block(
            escape_markdown(batch_title),
            weight="Bolder", size="Large", color=header_color,
        ),
        _text_block(f"共 {len(warnings)} 筆警告", size="Medium", isSubtle=True),
    ]
    all_actions: List[Dict] = []

    for idx, w in enumerate(shown, 1):
        elements, actions = _warning_card_elements(w, idx)
        body.extend(elements)
        all_actions.extend(actions[:2])  # 每筆最多兩個按鈕（原文/地圖），避免 actions 總數爆量

    if remaining > 0:
        body.append(_text_block(f"…另有 {remaining} 筆未顯示，請至系統查看完整列表", isSubtle=True))

    if source_home_url:
        home_link = safe_url(source_home_url)
        if home_link:
            all_actions.append({"type": "Action.OpenUrl", "title": "🏠 來源首頁", "url": home_link})

    card_content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if all_actions:
        # Teams 對單一 Adaptive Card 的 actions 數量建議不超過 6 個，避免版面爆版
        card_content["actions"] = all_actions[:6]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content,
            }
        ],
    }


def build_system_anomaly_card(title: str, detail_lines: List[str]) -> Dict:
    """來源健康異常的系統級卡片（與一般航警通知分開發送，claude.md 第二階段第七節）。"""
    body = [
        _text_block(f"⚠️ {escape_markdown(title)}", weight="Bolder", size="Large", color="Attention"),
    ]
    for line in detail_lines:
        body.append(_text_block(escape_markdown(truncate(line, 300)), size="Small"))

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


@dataclass
class TeamsSendResult:
    success: bool
    http_status: Optional[int] = None
    error: str = ""
    skipped: bool = False  # 例如 payload 為 None（無內容可送）或 dry-run


class TeamsNotifier:
    def __init__(self, webhook_url: str, timeout: int = 30, max_retries: int = 1):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.enabled = bool(webhook_url)
        self._ssl_verify = resolve_ssl_verify()

    def send_payload(self, payload: Optional[Dict], dry_run: bool = False) -> TeamsSendResult:
        if payload is None:
            return TeamsSendResult(success=False, skipped=True, error="無內容可發送")

        if not self.enabled:
            return TeamsSendResult(success=False, skipped=True, error="Teams webhook 未設定")

        if dry_run:
            # Dry-run 絕不可連線真實 webhook（claude.md 第二階段十四之禁止事項）
            return TeamsSendResult(success=False, skipped=True, error="dry-run：略過實際發送")

        last_error = ""
        last_status = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                    verify=self._ssl_verify,
                )
                last_status = response.status_code
                if response.status_code in (200, 202):
                    return TeamsSendResult(success=True, http_status=response.status_code)

                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                return TeamsSendResult(success=False, http_status=response.status_code, error=last_error)

            except requests.exceptions.SSLError as exc:
                return TeamsSendResult(success=False, error=f"SSL 錯誤: {exc}")
            except requests.exceptions.Timeout as exc:
                last_error = f"連線逾時: {exc}"
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                return TeamsSendResult(success=False, error=last_error)
            except requests.exceptions.ConnectionError as exc:
                return TeamsSendResult(success=False, error=f"連線錯誤: {exc}")
            except Exception as exc:  # noqa: BLE001
                return TeamsSendResult(success=False, error=f"未預期錯誤: {exc}")

        return TeamsSendResult(success=False, http_status=last_status, error=last_error)

    def send_batch(self, warnings: List[Dict], source_type: str, is_today: bool, dry_run: bool = False) -> TeamsSendResult:
        icon, name, home_url = SOURCE_LABELS.get(source_type, ("📍", source_type, ""))
        time_badge = "今日新增" if is_today else "歷史資料"
        batch_title = f"{icon} {name}｜{time_badge}（{len(warnings)} 筆）"
        payload = build_adaptive_card_payload(warnings, batch_title, source_home_url=home_url)
        return self.send_payload(payload, dry_run=dry_run)

    def send_system_anomaly(self, title: str, detail_lines: List[str], dry_run: bool = False) -> TeamsSendResult:
        payload = build_system_anomaly_card(title, detail_lines)
        return self.send_payload(payload, dry_run=dry_run)
