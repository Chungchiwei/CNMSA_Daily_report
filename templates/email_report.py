#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海事警告 Email 報告樣板（重新設計版）。

目標（claude.md 九）：
  - 主管能在數秒內看出最高風險、影響區域、建議行動
  - 風險等級以顏色與排序區分，不再整封信都用同一種紅色
  - 所有外部文字一律 html.escape，URL 一律驗證 http/https
  - table layout + inline CSS，最大寬度 ~700px，無 JavaScript，無外部 CSS
  - 同時提供純文字 MIME alternative
  - 歷史資料不全部展開，只顯示統計與少量高風險項目
  - 航行警告依來源（中國海事局／台灣航港局／UKMTO）分組顯示，UKMTO 一律以
    純英文卡片呈現，避免中英文混雜在同一段落造成閱讀困難（2026-08-13 使用者反映）
"""

from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlparse

from services.risk_presentation import RISK_ORDER, risk_level_of as _risk_level_of, sort_by_risk as _sort_by_risk

# (文字色, 背景色, 中文標籤, 英文標籤)
RISK_STYLE = {
    "CRITICAL": ("#FFFFFF", "#B71C1C", "危急 CRITICAL", "CRITICAL"),
    "HIGH":     ("#FFFFFF", "#E65100", "高 HIGH", "HIGH"),
    "MEDIUM":   ("#212121", "#F9A825", "中 MEDIUM", "MEDIUM"),
    "LOW":      ("#FFFFFF", "#1565C0", "低 LOW", "LOW"),
    "INFO":     ("#FFFFFF", "#607D8B", "資訊 INFO", "INFO"),
    "CANCELLED": ("#FFFFFF", "#9E9E9E", "已撤銷/取消", "CANCELLED"),
    "EXPIRED":  ("#FFFFFF", "#78909C", "已逾期", "EXPIRED"),
}

SOURCE_LABELS = {
    "CN_MSA": ("🇨🇳", "中國海事局"),
    "TW_MPB": ("🇹🇼", "台灣航港局"),
    "UKMTO":  ("🇬🇧", "UKMTO"),
}

# 卡片分組顯示順序；不在此清單中的來源仍會被顯示，只是排在最後（安全網，避免漏掉資料）
SOURCE_ORDER = ["CN_MSA", "TW_MPB", "UKMTO"]

# 依來源決定卡片語言：目前只有 UKMTO 的原始內容是英文，其餘來源維持中文卡片
_EN_SOURCES = {"UKMTO"}

STATUS_LABELS = {
    "ACTIVE": "生效中",
    "UPCOMING": "即將生效",
    "EXPIRED": "已逾期",
    "CANCELLED": "已撤銷/取消",
    "UNKNOWN": "狀態未知",
}

STATUS_LABELS_EN = {
    "ACTIVE": "Active",
    "UPCOMING": "Upcoming",
    "EXPIRED": "Expired",
    "CANCELLED": "Cancelled",
    "UNKNOWN": "Unknown",
}

# 卡片內固定文字（依語言）；UKMTO 卡片一律使用 en，其餘來源使用 zh，
# 避免像過往那樣「英文標題／內文」配上「中文標籤／中文建議行動」混雜難讀。
_LABELS = {
    "zh": {
        "source": "來源",
        "notice_no": "公告編號",
        "published": "發布時間",
        "valid_period": "有效期間",
        "status": "狀態",
        "affected_area": "影響海域",
        "impact": "對商船/船隊可能影響：",
        "action": "建議行動：",
        "keywords": "命中關鍵字：",
        "reasons": "判斷依據：",
        "coords_title": "座標資訊",
        "map": "🗺️ 地圖",
        "view_original": "🔗 查看原始官方公告 →",
        "confidence": "解析可信度：",
        "no_summary": "本則公告未提供詳細內文摘要，請點選原始公告查看詳情。",
        "no_period": "未提供明確有效期間，請以原始公告為準",
        "default_impact": "可能影響鄰近海域商船航行安全，詳情請參閱原始公告。",
        "default_action": "建議通過前確認公告有效性並提高警戒。",
        "verification": "⚠️ 此來源之解析規則尚待於實際網路環境驗證，內容可能不完整",
        "unassessed": "未評估",
        "none": "-",
        "list_sep": "、",
        "reason_sep": " ／ ",
        "no_keyword_reason": "標題與內文均未命中任何關鍵字",
        "unknown_source": "未知來源",
    },
    "en": {
        "source": "Source",
        "notice_no": "Notice No.",
        "published": "Published",
        "valid_period": "Valid Period",
        "status": "Status",
        "affected_area": "Affected Area",
        "impact": "Potential Impact on Vessels:",
        "action": "Recommended Action:",
        "keywords": "Matched Keywords:",
        "reasons": "Scoring Basis:",
        "coords_title": "Coordinates",
        "map": "🗺️ Map",
        "view_original": "🔗 View Original Notice →",
        "confidence": "Parsing Confidence:",
        "no_summary": "No detailed summary provided for this notice; please view the original announcement.",
        "no_period": "No explicit validity period provided; please refer to the original notice.",
        "default_impact": "May affect nearby commercial vessel navigation safety; refer to the original notice for details.",
        "default_action": "Confirm the notice is still valid before transiting and maintain heightened vigilance.",
        "verification": "⚠️ Parsing rules for this source are still pending validation against the live site; content may be incomplete",
        "unassessed": "Not assessed",
        "none": "-",
        "list_sep": ", ",
        "reason_sep": " / ",
        "no_keyword_reason": "No keyword matches in title or content (fallback classification).",
        "unknown_source": "Unknown source",
    },
}

# 英文卡片（UKMTO）用的建議行動對照表；中文來源的建議行動沿用資料庫既有欄位
# （由 services/summarizer.py 產生的中文文字），兩者刻意分開，避免英文卡片裡混入中文句子。
_EN_RECOMMENDED_ACTION = {
    "CRITICAL": "Assess an immediate reroute, notify the master and operations PIC, and monitor closely for updates.",
    "HIGH": "Consider rerouting or increasing vigilance; keep clear of the notice area and monitor follow-up notices.",
    "MEDIUM": "Note the notice area and validity period; increase lookout and AIS reporting frequency when transiting.",
    "LOW": "Maintain routine monitoring; reconfirm the notice is still valid before transiting.",
    "INFO": "For reference only; no specific action required at this time.",
}
_EN_RECOMMENDED_ACTION_DEFAULT = "Confirm the notice is still valid before transiting and maintain heightened vigilance."


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_url(url: Optional[str], fallback: str = "#") -> str:
    if not url:
        return fallback
    try:
        parsed = urlparse(url)
    except Exception:
        return fallback
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return _esc(url)
    return fallback


def _tpe_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _coords_of(w: Dict):
    coords = w.get("coordinates") or []
    normalized = []
    for c in coords:
        try:
            lat, lon = float(c[0]), float(c[1])
            normalized.append((lat, lon))
        except Exception:
            continue
    return normalized


def _lang_of(source_type: str) -> str:
    return "en" if source_type in _EN_SOURCES else "zh"


def build_subject(today_warnings: List[Dict], history_warnings: List[Dict]) -> str:
    """【海事航安警示｜HIGH】今日 3 筆新增－東海實彈射擊／浙江沿海禁航"""
    total_today = len(today_warnings)
    if not today_warnings:
        top_history = _sort_by_risk(history_warnings)
        highest = _risk_level_of(top_history[0]) if top_history else "INFO"
        return f"【海事航安警示｜今日無新增】目前追蹤 {len(history_warnings)} 筆有效警告，最高風險 {highest}"

    sorted_today = _sort_by_risk(today_warnings)
    highest_level = _risk_level_of(sorted_today[0])
    headline_titles = [_sanitize_header_text(w.get("title", "")) for w in sorted_today[:2] if w.get("title")]
    headline = "／".join(t[:20] for t in headline_titles) or "詳見報告內容"
    return f"【海事航安警示｜{highest_level}】今日 {total_today} 筆新增－{headline}"


def _sanitize_header_text(text: str) -> str:
    """避免郵件標頭注入（CRLF injection）並移除控制字元，Subject 非 HTML 環境不需 html.escape。"""
    if not text:
        return ""
    return "".join(ch for ch in text if ch not in ("\r", "\n") and (ch == " " or ch.isprintable())).strip()


def build_executive_summary(
    today_warnings: List[Dict],
    history_warnings: List[Dict],
    health_reports: Optional[List] = None,
) -> str:
    all_active = today_warnings + [w for w in history_warnings if w.get("status") not in ("EXPIRED", "CANCELLED")]
    sorted_all = _sort_by_risk(all_active) if all_active else []
    highest_level = _risk_level_of(sorted_all[0]) if sorted_all else "INFO"
    highest_color_fg, highest_color_bg, highest_label, _highest_label_en = RISK_STYLE[highest_level]

    affected_waters = []
    for w in sorted_today_first(today_warnings):
        area = w.get("affected_waters") or w.get("bureau")
        if area and area not in affected_waters:
            affected_waters.append(area)
        if len(affected_waters) >= 5:
            break
    waters_text = "、".join(affected_waters) if affected_waters else "無特定海域資訊"

    urgent = [w for w in today_warnings if _risk_level_of(w) in ("CRITICAL", "HIGH")]
    urgent_text = "、".join((w.get("title", "")[:24] for w in urgent[:3])) if urgent else "無"

    # 依來源列出今日新增筆數，讓主管一眼看出各來源動態（配合下方卡片改為依來源分組）
    source_counts = OrderedDict((src, 0) for src in SOURCE_ORDER)
    for w in today_warnings:
        src = w.get("source", w.get("source_type", ""))
        source_counts[src] = source_counts.get(src, 0) + 1
    source_breakdown = "、".join(
        f"{SOURCE_LABELS.get(src, ('📍', src))[1]} {cnt}"
        for src, cnt in source_counts.items() if cnt
    ) or "無"

    # 2026-08-07 使用者反映「各資料來源健康狀態」表格讓版面看起來很亂，
    # 且來源異常本來就已經有獨立的「系統異常」通知機制會發信告知
    # （見 services/source_health_alert.py 的 detect_anomaly，與此處完全獨立），
    # 一般報告不需要重複顯示每次都是全綠 HEALTHY 的落落長表格，故不再渲染。
    # health_reports 參數仍保留在函式簽名中以維持呼叫端相容性，只是不再用於畫面輸出。
    del health_reports

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;border:1px solid #E0E0E0;border-radius:4px;">
      <tr><td style="padding:16px 20px;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td><span style="font-family:Arial,sans-serif;font-size:16px;color:#212121;font-weight:bold;">主管摘要</span></td>
        </tr></table>
        <table width="100%" cellpadding="6" cellspacing="0" style="margin-top:8px;">
          <tr>
            <td width="140" style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;vertical-align:top;">今日新增警告</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;word-break:break-word;overflow-wrap:break-word;">{len(today_warnings)} 筆（{_esc(source_breakdown)}）</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;vertical-align:top;">目前追蹤有效警告</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;">{len(all_active)} 筆</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;vertical-align:top;">最高風險等級</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;">
              <span style="background:{highest_color_bg};color:{highest_color_fg};padding:2px 8px;border-radius:3px;font-weight:bold;">{_esc(highest_label)}</span>
            </td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;vertical-align:top;">主要影響海域/單位</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;word-break:break-word;overflow-wrap:break-word;">{_esc(waters_text)}</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;vertical-align:top;">需要立即關注</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#B71C1C;font-weight:bold;word-break:break-word;overflow-wrap:break-word;">{_esc(urgent_text)}</td>
          </tr>
        </table>
      </td></tr>
    </table>
    """


def sorted_today_first(today_warnings):
    return _sort_by_risk(today_warnings)


def _group_by_source(warnings: List[Dict]) -> "OrderedDict[str, List[Dict]]":
    """
    依來源分組，固定依 SOURCE_ORDER（中國海事局／台灣航港局／UKMTO）排序在前；
    若出現清單以外的新來源，仍會保留並附加在最後，避免資料被悄悄漏掉。
    """
    groups: "OrderedDict[str, List[Dict]]" = OrderedDict((src, []) for src in SOURCE_ORDER)
    for w in warnings:
        src = w.get("source", w.get("source_type", "")) or ""
        groups.setdefault(src, []).append(w)
    return groups


def _render_card(w: Dict, is_today: bool, lang: str = "zh") -> str:
    L = _LABELS.get(lang, _LABELS["zh"])
    risk_level = _risk_level_of(w)
    fg, bg, zh_label, en_label = RISK_STYLE[risk_level]
    risk_label = en_label if lang == "en" else zh_label

    source_type = w.get("source", w.get("source_type", ""))
    icon, source_name = SOURCE_LABELS.get(source_type, ("📍", source_type or L["unknown_source"]))

    title = _esc(w.get("title", "N/A"))
    bureau = _esc(w.get("bureau", w.get("issuing_bureau", "N/A")))
    notice_number = _esc(w.get("notice_number", "") or "-")
    publish_time = _esc(w.get("time", w.get("publish_time", "N/A")))
    status = w.get("status", "UNKNOWN")
    status_map = STATUS_LABELS_EN if lang == "en" else STATUS_LABELS
    status_label = _esc(status_map.get(status, status or status_map["UNKNOWN"]))

    effective_start = w.get("effective_start", "")
    effective_end = w.get("effective_end", "")
    if effective_start or effective_end:
        unknown_word = "unknown" if lang == "en" else "未知"
        effective_text = f"{_esc(effective_start or unknown_word)} ~ {_esc(effective_end or unknown_word)}"
    else:
        effective_text = L["no_period"]

    affected_area = _esc(w.get("affected_waters", "") or bureau)

    if lang == "en":
        # UKMTO 原始內容（title/details）本就是英文，不套用中文摘要邏輯
        content_text = w.get("details") or w.get("summary_zh_tw") or w.get("cleaned_content") or ""
    else:
        content_text = w.get("summary_zh_tw") or w.get("cleaned_content") or ""
    content_text = _esc(content_text[:400]) if content_text else L["no_summary"]

    operational_impact = _esc(w.get("operational_impact") or L["default_impact"])

    if lang == "en":
        # 資料庫既有的 recommended_action 一律是中文（services/summarizer.py 產生），
        # 英文卡片改用獨立的英文對照表，避免中英文混在同一句建議行動裡。
        recommended_action = _esc(_EN_RECOMMENDED_ACTION.get(risk_level, _EN_RECOMMENDED_ACTION_DEFAULT))
    else:
        recommended_action = _esc(w.get("recommended_action") or L["default_action"])

    kw = w.get("matched_keywords") or w.get("keywords") or w.get("keywords_matched", [])
    if isinstance(kw, str):
        kw_list = [k.strip() for k in kw.split(",") if k.strip()]
    else:
        kw_list = list(kw or [])
    kw_str = _esc(L["list_sep"].join(kw_list)) if kw_list else L["none"]

    reasons = w.get("scoring_reasons", [])
    if lang == "en" and (not reasons or list(reasons) == ["標題與內文均未命中任何關鍵字"]):
        # 風險評分引擎的判斷依據文字固定是中文（依 keywords_config.json 的中文分類/關鍵字產生），
        # 英文來源（UKMTO）幾乎不會命中中文關鍵字，這裡把最常見的「未命中」訊息換成英文，
        # 其餘極少數情況（例如剛好命中中文關鍵字）維持原文，避免過度翻譯造成失真。
        reasons_str = L["no_keyword_reason"]
    else:
        reasons_str = _esc(L["reason_sep"].join(reasons[:4])) if reasons else L["none"]

    link = _safe_url(w.get("link"))
    confidence = w.get("confidence")
    confidence_text = f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else L["unassessed"]

    coords = _coords_of(w)
    coord_html = ""
    if coords:
        rows = []
        for i, (lat, lon) in enumerate(coords[:5], 1):
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            maps_url = f"https://maps.google.com/maps?q={lat:.6f},{lon:.6f}"
            rows.append(
                f'<span style="font-family:\'Courier New\',monospace;font-size:12px;color:#333;word-break:break-word;">'
                f"{i}. {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}</span> "
                f'<a href="{_esc(maps_url)}" target="_blank" style="font-size:12px;color:#1565C0;">{L["map"]}</a><br>'
            )
        # 注意：此區塊必須自成一個 <table>，不可只留一組孤立的 <tr><td>（舊版 bug）——
        # 孤立的 <tr> 不在任何 <table> 底下時，瀏覽器/信箱客戶端的 HTML 解析器會把它
        # 「foster parenting」搬到外層 table 之前，導致座標區塊之後的版面整個跑掉、
        # 順序錯亂，這正是使用者反映「後面表格會跑掉」的根本原因。
        coord_html = f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
        <tr><td style="padding:8px 0;">
          <div style="background:#F0F7FF;border-radius:3px;padding:8px 10px;word-break:break-word;overflow-wrap:break-word;">
            <span style="font-family:Arial,sans-serif;font-size:12px;color:#0056B3;font-weight:bold;">{L["coords_title"]}</span><br>
            {''.join(rows)}
          </div>
        </td></tr>
        </table>"""

    new_badge = (
        '<span style="background:#FFD54F;color:#212121;font-size:10px;font-weight:bold;'
        'padding:2px 6px;border-radius:3px;margin-left:6px;">NEW</span>'
    ) if is_today else ""

    verification_note = ""
    if w.get("needs_verification"):
        verification_note = (
            '<div style="margin-top:6px;font-family:Arial,sans-serif;font-size:11px;color:#E65100;'
            'word-break:break-word;overflow-wrap:break-word;">'
            f"{L['verification']}"
            "</div>"
        )

    # 中繼資料改成單欄「標籤：內容」逐行排列（原本是 33%/33%/34% 三欄擠在一起），
    # 較長的機關名稱/影響海域文字在窄版面（如手機、信件預覽窗）不會再擠壓變形。
    meta_fields = [
        (L["source"], f"{icon} {_esc(source_name)} / {bureau}"),
        (L["notice_no"], notice_number),
        (L["published"], publish_time),
        (L["valid_period"], effective_text),
        (L["status"], status_label),
        (L["affected_area"], affected_area),
    ]
    label_style = ("font-family:Arial,sans-serif;font-size:12px;color:#78909C;"
                   "padding:3px 10px 3px 0;vertical-align:top;white-space:nowrap;width:100px;")
    value_style = ("font-family:Arial,sans-serif;font-size:12px;color:#455A64;"
                   "padding:3px 0;vertical-align:top;word-break:break-word;overflow-wrap:break-word;")
    meta_rows_html = "".join(
        f'<tr><td style="{label_style}">{lbl}</td><td style="{value_style}">{val}</td></tr>'
        for lbl, val in meta_fields
    )

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;border:1px solid #E0E0E0;border-radius:4px;overflow:hidden;">
      <tr>
        <td style="background:{bg};padding:10px 14px;">
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td>
              <span style="font-family:Arial,sans-serif;font-size:11px;color:{fg};background:rgba(0,0,0,0.15);padding:2px 8px;border-radius:3px;font-weight:bold;">{_esc(risk_label)}</span>
              {new_badge}
            </td>
          </tr></table>
          <div style="font-family:Arial,sans-serif;font-size:15px;color:{fg};font-weight:bold;margin-top:6px;word-break:break-word;overflow-wrap:break-word;">{icon} {title}</div>
        </td>
      </tr>
      <tr><td style="padding:12px 14px;background:#FFFFFF;">
        <table width="100%" cellpadding="0" cellspacing="0">
          {meta_rows_html}
        </table>
        <hr style="border:none;border-top:1px solid #EEEEEE;margin:10px 0;">
        <div style="font-family:Arial,sans-serif;font-size:13px;color:#212121;line-height:1.6;word-break:break-word;overflow-wrap:break-word;">{content_text}</div>
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;word-break:break-word;overflow-wrap:break-word;"><b>{L['impact']}</b> {operational_impact}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#1B5E20;padding-top:4px;word-break:break-word;overflow-wrap:break-word;"><b>{L['action']}</b> {recommended_action}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;padding-top:4px;word-break:break-word;overflow-wrap:break-word;"><b>{L['keywords']}</b> {kw_str}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:11px;color:#78909C;padding-top:4px;word-break:break-word;overflow-wrap:break-word;"><b>{L['reasons']}</b> {reasons_str}</td></tr>
        </table>
        {coord_html}
        {verification_note}
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;">
          <tr>
            <td style="background:#E3F2FD;border-radius:3px;padding:8px 10px;">
              <a href="{link}" target="_blank" style="font-family:Arial,sans-serif;font-size:12px;color:#1565C0;font-weight:bold;text-decoration:none;">{L['view_original']}</a>
              <span style="float:right;font-family:Arial,sans-serif;font-size:11px;color:#78909C;">{L['confidence']} {confidence_text}</span>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
    """


def _render_source_section(warnings: List[Dict], source_type: str, is_today: bool) -> str:
    """單一來源（中國海事局／台灣航港局／UKMTO／其他）的子標題 + 該來源底下所有卡片。"""
    if not warnings:
        return ""
    icon, name = SOURCE_LABELS.get(source_type, ("📍", source_type or "未知來源"))
    lang = _lang_of(source_type)
    count_label = f" ({len(warnings)})" if lang == "en" else f"（{len(warnings)} 筆）"
    cards = "".join(_render_card(w, is_today=is_today, lang=lang) for w in _sort_by_risk(warnings))
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">
      <tr><td style="background:#CFD8DC;padding:8px 12px;border-radius:4px;">
        <span style="font-family:Arial,sans-serif;font-size:13px;color:#263238;font-weight:bold;">{icon} {_esc(name)}{count_label}</span>
      </td></tr>
    </table>
    {cards}
    """


def _render_grouped_sections(warnings: List[Dict], is_today: bool) -> str:
    groups = _group_by_source(warnings)
    return "".join(_render_source_section(items, src, is_today) for src, items in groups.items())


def _render_history_stats(history_warnings: List[Dict], max_full_cards: int = 5) -> str:
    if not history_warnings:
        return ""
    sorted_hist = _sort_by_risk(history_warnings)
    top = [w for w in sorted_hist if _risk_level_of(w) in ("CRITICAL", "HIGH")][:max_full_cards]
    remaining = len(history_warnings) - len(top)

    level_counts = {}
    for w in history_warnings:
        lvl = _risk_level_of(w)
        level_counts[lvl] = level_counts.get(lvl, 0) + 1

    stats_row = " ｜ ".join(
        f"{RISK_STYLE[lvl][2]}: {cnt}" for lvl, cnt in sorted(level_counts.items(), key=lambda x: RISK_ORDER.get(x[0], 9))
    )

    # 僅列出的高風險項目一樣依來源分組顯示（與今日新增區塊一致）
    cards_html = _render_grouped_sections(top, is_today=False)
    note = f"（僅列出風險最高的 {len(top)} 筆，其餘 {remaining} 筆請至系統或匯出報表查看）" if remaining > 0 else ""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">
      <tr><td style="background:#ECEFF1;padding:10px 14px;border-radius:4px;">
        <span style="font-family:Arial,sans-serif;font-size:14px;color:#37474F;font-weight:bold;">過往仍追蹤中的歷史資料（{len(history_warnings)} 筆）</span><br>
        <span style="font-family:Arial,sans-serif;font-size:12px;color:#607D8B;">{_esc(stats_row)}</span>
      </td></tr>
    </table>
    <div style="font-family:Arial,sans-serif;font-size:11px;color:#78909C;margin-top:4px;">{_esc(note)}</div>
    {cards_html}
    """


def build_html_report(
    today_warnings: List[Dict],
    history_warnings: List[Dict],
    health_reports: Optional[List] = None,
    source_anomaly: bool = False,
    generated_at: Optional[str] = None,
) -> str:
    generated_at = generated_at or _tpe_now_str()

    if source_anomaly:
        banner = """
        <tr><td style="background:#B71C1C;padding:16px;">
          <span style="font-family:Arial,sans-serif;font-size:16px;color:#FFFFFF;font-weight:bold;">⚠️ 資料來源異常：本次執行未能正常取得任何來源資料</span><br>
          <span style="font-family:Arial,sans-serif;font-size:12px;color:#FFCDD2;">請勿將「今日無新增」與「來源異常」混淆，詳見下方各來源健康狀態。</span>
        </td></tr>"""
    elif today_warnings:
        banner = f"""
        <tr><td style="background:#D32F2F;padding:16px;">
          <span style="font-family:Arial,sans-serif;font-size:16px;color:#FFFFFF;font-weight:bold;">🚨 今日新增 {len(today_warnings)} 筆航行警告</span>
        </td></tr>"""
    else:
        banner = """
        <tr><td style="background:#2E7D32;padding:16px;">
          <span style="font-family:Arial,sans-serif;font-size:14px;color:#FFFFFF;font-weight:bold;">✅ 今日無新增航行警告（來源檢查正常）</span>
        </td></tr>"""

    today_sections = _render_grouped_sections(today_warnings, is_today=True)
    history_section = _render_history_stats(history_warnings)
    exec_summary = build_executive_summary(today_warnings, history_warnings, health_reports)

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>海事航安警示報告</title>
</head>
<body style="margin:0;padding:0;background:#F4F6F8;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;">
  <tr><td align="center">
    <table width="700" cellpadding="0" cellspacing="0" style="background:#FFFFFF;max-width:700px;width:100%;">
      <tr>
        <td style="background:#0A1628;padding:24px;">
          <span style="font-family:Arial,sans-serif;font-size:20px;color:#FFFFFF;font-weight:bold;">海事航安警示監控報告</span><br><br>
          <span style="font-family:Arial,sans-serif;font-size:12px;color:#8FA3B8;">報告時間：{_esc(generated_at)} (TPE)</span>
        </td>
      </tr>
      {banner}
      <tr><td style="padding:20px;">
        {exec_summary}
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:15px;color:#212121;font-weight:bold;border-bottom:2px solid #E0E0E0;padding-bottom:6px;">
            今日新增詳情（依來源分組，組內依風險等級排序）
          </td></tr>
        </table>
        {today_sections if today_sections.strip() else '<div style="font-family:Arial,sans-serif;font-size:13px;color:#78909C;padding:12px 0;">今日無新增警告</div>'}
        {history_section}
      </td></tr>
      <tr>
        <td style="background:#E9ECEF;padding:16px;text-align:center;">
          <span style="font-family:Arial,sans-serif;font-size:11px;color:#6C757D;">
            此為自動發送的郵件，請勿直接回覆。海事航安警示監控系統。
          </span>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def build_plain_text_report(
    today_warnings: List[Dict],
    history_warnings: List[Dict],
    source_anomaly: bool = False,
    generated_at: Optional[str] = None,
) -> str:
    """純文字 MIME alternative，供不支援 HTML 的信箱客戶端顯示。"""
    generated_at = generated_at or _tpe_now_str()
    lines = [f"海事航安警示監控報告 — {generated_at} (TPE)", "=" * 50, ""]

    if source_anomaly:
        lines.append("⚠ 資料來源異常：本次執行未能正常取得任何來源資料，請勿視為「今日無新增」。")
        lines.append("")

    lines.append(f"今日新增：{len(today_warnings)} 筆")
    today_groups = _group_by_source(today_warnings)
    counter = 0
    for src, items in today_groups.items():
        if not items:
            continue
        icon, name = SOURCE_LABELS.get(src, ("📍", src or "未知來源"))
        lines.append(f"\n[{name}]（{len(items)} 筆）")
        for w in _sort_by_risk(items):
            counter += 1
            level = _risk_level_of(w)
            lines.append(f"\n  [{counter}] ({level}) {w.get('title','N/A')}")
            lines.append(f"      來源: {w.get('bureau', w.get('issuing_bureau',''))} | 時間: {w.get('time', w.get('publish_time',''))}")
            summary = w.get("summary_zh_tw") or w.get("cleaned_content") or w.get("details") or ""
            if summary:
                lines.append(f"      摘要: {summary[:200]}")
            action = w.get("recommended_action") or ""
            if action:
                lines.append(f"      建議行動: {action}")
            link = w.get("link") or ""
            if link.startswith("http://") or link.startswith("https://"):
                lines.append(f"      原始公告: {link}")

    lines.append(f"\n過往追蹤中歷史資料：{len(history_warnings)} 筆（詳情請見 HTML 版本或系統匯出報表）")
    lines.append("\n此為自動發送信件，請勿直接回覆。")
    return "\n".join(lines)


# ==================== 系統異常通知（與航警通知分開，claude.md 第二階段第七節） ====================

def build_system_anomaly_subject(anomaly) -> str:
    return "【系統異常】中國海事警告資料來源無法正常取得"


def build_system_anomaly_html(anomaly, generated_at: Optional[str] = None) -> str:
    generated_at = generated_at or _tpe_now_str()

    def _rows(sources, label):
        if not sources:
            return ""
        trs = ""
        for s in sources:
            trs += (
                f'<tr><td style="padding:4px 8px;border-bottom:1px solid #4E342E;">{_esc(s.get("source_name",""))}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #4E342E;color:#FFCDD2;">{_esc(s.get("status",""))}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #4E342E;">{_esc(s.get("newest_publish_date") or "-")}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #4E342E;font-size:11px;">{_esc(s.get("error_summary") or "-")}</td></tr>'
            )
        return f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;">{_esc(label)}</td></tr>
        </table>
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid #E0E0E0;margin-top:4px;">
          <tr style="background:#ECEFF1;">
            <td style="padding:4px 8px;font-family:Arial,sans-serif;font-size:12px;"><b>來源</b></td>
            <td style="padding:4px 8px;font-family:Arial,sans-serif;font-size:12px;"><b>狀態</b></td>
            <td style="padding:4px 8px;font-family:Arial,sans-serif;font-size:12px;"><b>最新公告日期</b></td>
            <td style="padding:4px 8px;font-family:Arial,sans-serif;font-size:12px;"><b>錯誤摘要</b></td>
          </tr>
          {trs}
        </table>"""

    actions_html = "".join(
        f'<li style="font-family:Arial,sans-serif;font-size:13px;color:#212121;padding:2px 0;">{_esc(a)}</li>'
        for a in anomaly.suggested_actions
    )

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F4F6F8;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;">
  <tr><td align="center">
    <table width="700" cellpadding="0" cellspacing="0" style="background:#FFFFFF;max-width:700px;">
      <tr><td style="background:#3E2723;padding:24px;">
        <span style="font-family:Arial,sans-serif;font-size:20px;color:#FFFFFF;font-weight:bold;">⚠️ 系統異常：中國海事警告資料來源無法正常取得</span><br><br>
        <span style="font-family:Arial,sans-serif;font-size:12px;color:#D7CCC8;">偵測時間：{_esc(anomaly.detected_at)}（TPE：{_esc(generated_at)}）</span>
      </td></tr>
      <tr><td style="padding:20px;">
        <div style="font-family:Arial,sans-serif;font-size:14px;color:#B71C1C;font-weight:bold;">{_esc(anomaly.reason)}</div>
        <div style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;margin-top:6px;">
          本通知代表「抓取失敗／資料異常」，並非「今日沒有新警告」，請勿混淆判讀。
        </div>
        {_rows(anomaly.failed_sources, "失敗來源")}
        {_rows(anomaly.healthy_sources, "仍可運作的來源" + ("（備援生效）" if anomaly.fallback_used else ""))}
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;">建議檢查動作</td></tr>
        </table>
        <ul style="margin:4px 0 0 20px;padding:0;">{actions_html}</ul>
      </td></tr>
      <tr><td style="background:#E9ECEF;padding:16px;text-align:center;">
        <span style="font-family:Arial,sans-serif;font-size:11px;color:#6C757D;">此為自動發送的系統異常通知，請勿直接回覆。</span>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def build_system_anomaly_plain_text(anomaly, generated_at: Optional[str] = None) -> str:
    generated_at = generated_at or _tpe_now_str()
    lines = [
        "【系統異常】中國海事警告資料來源無法正常取得",
        "=" * 50,
        f"偵測時間: {anomaly.detected_at} (TPE: {generated_at})",
        "",
        anomaly.reason,
        "本通知代表「抓取失敗／資料異常」，並非「今日沒有新警告」，請勿混淆判讀。",
        "",
        "失敗來源:",
    ]
    for s in anomaly.failed_sources:
        lines.append(f"  - {s.get('source_name')}: {s.get('status')} | 最新公告: {s.get('newest_publish_date') or '-'} | {s.get('error_summary') or '-'}")
    if anomaly.healthy_sources:
        lines.append("\n仍可運作的來源:")
        for s in anomaly.healthy_sources:
            lines.append(f"  - {s.get('source_name')}: {s.get('status')} | 最新公告: {s.get('newest_publish_date') or '-'}")
    lines.append("\n建議檢查動作:")
    for a in anomaly.suggested_actions:
        lines.append(f"  - {a}")
    lines.append("\n此為自動發送信件，請勿直接回覆。")
    return "\n".join(lines)
