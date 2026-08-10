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
"""

from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlparse

from services.risk_presentation import RISK_ORDER, risk_level_of as _risk_level_of, sort_by_risk as _sort_by_risk

# (文字色, 背景色, 標籤)
RISK_STYLE = {
    "CRITICAL": ("#FFFFFF", "#B71C1C", "危急 CRITICAL"),
    "HIGH":     ("#FFFFFF", "#E65100", "高 HIGH"),
    "MEDIUM":   ("#212121", "#F9A825", "中 MEDIUM"),
    "LOW":      ("#FFFFFF", "#1565C0", "低 LOW"),
    "INFO":     ("#FFFFFF", "#607D8B", "資訊 INFO"),
    "CANCELLED": ("#FFFFFF", "#9E9E9E", "已撤銷/取消"),
    "EXPIRED":  ("#FFFFFF", "#78909C", "已逾期"),
}

SOURCE_LABELS = {
    "CN_MSA": ("🇨🇳", "中國海事局"),
    "TW_MPB": ("🇹🇼", "台灣航港局"),
    "UKMTO":  ("🇬🇧", "UKMTO"),
}

STATUS_LABELS = {
    "ACTIVE": "生效中",
    "UPCOMING": "即將生效",
    "EXPIRED": "已逾期",
    "CANCELLED": "已撤銷/取消",
    "UNKNOWN": "狀態未知",
}


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


def build_subject(today_warnings: List[Dict], history_warnings: List[Dict]) -> str:
    """【航行警告監控報告｜HIGH】今日 3 筆新增－東海實彈射擊／浙江沿海禁航"""
    total_today = len(today_warnings)
    if not today_warnings:
        top_history = _sort_by_risk(history_warnings)
        highest = _risk_level_of(top_history[0]) if top_history else "INFO"
        return f"【航行警告監控報告｜今日無新增】目前追蹤 {len(history_warnings)} 筆有效警告，最高風險 {highest}"

    sorted_today = _sort_by_risk(today_warnings)
    highest_level = _risk_level_of(sorted_today[0])
    headline_titles = [_sanitize_header_text(w.get("title", "")) for w in sorted_today[:2] if w.get("title")]
    headline = "／".join(t[:20] for t in headline_titles) or "詳見報告內容"
    return f"【航行警告監控報告｜{highest_level}】今日 {total_today} 筆新增－{headline}"


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
    highest_color_fg, highest_color_bg, highest_label = RISK_STYLE[highest_level]

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
            <td width="50%" style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;">今日新增警告</td>
            <td width="50%" style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;">{len(today_warnings)} 筆</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;">目前追蹤有效警告</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;font-weight:bold;">{len(all_active)} 筆</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;">最高風險等級</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;">
              <span style="background:{highest_color_bg};color:{highest_color_fg};padding:2px 8px;border-radius:3px;font-weight:bold;">{_esc(highest_label)}</span>
            </td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;">主要影響海域/單位</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#212121;">{_esc(waters_text)}</td>
          </tr>
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#455A64;">需要立即關注</td>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#B71C1C;font-weight:bold;">{_esc(urgent_text)}</td>
          </tr>
        </table>
      </td></tr>
    </table>
    """


def sorted_today_first(today_warnings):
    return _sort_by_risk(today_warnings)


def _render_card(w: Dict, is_today: bool) -> str:
    risk_level = _risk_level_of(w)
    fg, bg, label = RISK_STYLE[risk_level]

    source_type = w.get("source", w.get("source_type", ""))
    icon, source_name = SOURCE_LABELS.get(source_type, ("📍", source_type or "未知來源"))

    title = _esc(w.get("title", "N/A"))
    bureau = _esc(w.get("bureau", w.get("issuing_bureau", "N/A")))
    notice_number = _esc(w.get("notice_number", "") or "-")
    publish_time = _esc(w.get("time", w.get("publish_time", "N/A")))
    status = w.get("status", "UNKNOWN")
    status_label = _esc(STATUS_LABELS.get(status, status or "狀態未知"))

    effective_start = w.get("effective_start", "")
    effective_end = w.get("effective_end", "")
    if effective_start or effective_end:
        effective_text = f"{_esc(effective_start or '未知')} ~ {_esc(effective_end or '未知')}"
    else:
        effective_text = "未提供明確有效期間，請以原始公告為準"

    affected_area = _esc(w.get("affected_waters", "") or bureau)

    summary = w.get("summary_zh_tw") or w.get("cleaned_content") or ""
    summary = _esc(summary[:400]) if summary else "本則公告未提供詳細內文摘要，請點選原始公告查看詳情。"

    operational_impact = _esc(w.get("operational_impact", "") or "可能影響鄰近海域商船航行安全，詳情請參閱原始公告。")
    recommended_action = _esc(w.get("recommended_action", "") or "建議通過前確認公告有效性並提高警戒。")

    # 優先使用新版風險評分服務算出的 matched_keywords（跟下面的「判斷依據」
    # scoring_reasons 保證是同一份資料算出來的），舊版 keywords/keywords_matched
    # 欄位只在還沒跑過風險評分（理論上不該發生）時當備援，避免「命中關鍵字」跟
    # 「判斷依據」兩行對不上、讓人以為系統邏輯矛盾。
    kw = w.get("matched_keywords") or w.get("keywords") or w.get("keywords_matched", [])
    if isinstance(kw, str):
        kw_list = [k.strip() for k in kw.split(",") if k.strip()]
    else:
        kw_list = list(kw or [])
    kw_str = _esc("、".join(kw_list)) if kw_list else "-"

    reasons = w.get("scoring_reasons", [])
    reasons_str = _esc(" ／ ".join(reasons[:4])) if reasons else "-"

    link = _safe_url(w.get("link"))
    confidence = w.get("confidence")
    confidence_text = f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else "未評估"

    coords = _coords_of(w)
    coord_html = ""
    if coords:
        rows = []
        for i, (lat, lon) in enumerate(coords[:5], 1):
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            maps_url = f"https://maps.google.com/maps?q={lat:.6f},{lon:.6f}"
            rows.append(
                f'<span style="font-family:\'Courier New\',monospace;font-size:12px;color:#333;">'
                f"{i}. {abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}</span> "
                f'<a href="{_esc(maps_url)}" target="_blank" style="font-size:12px;color:#1565C0;">🗺️ 地圖</a><br>'
            )
        coord_html = f"""
        <tr><td style="padding:8px 0;">
          <div style="background:#F0F7FF;border-radius:3px;padding:8px 10px;">
            <span style="font-family:Arial,sans-serif;font-size:12px;color:#0056B3;font-weight:bold;">座標資訊</span><br>
            {''.join(rows)}
          </div>
        </td></tr>"""

    new_badge = (
        '<span style="background:#FFD54F;color:#212121;font-size:10px;font-weight:bold;'
        'padding:2px 6px;border-radius:3px;margin-left:6px;">NEW</span>'
    ) if is_today else ""

    verification_note = ""
    if w.get("needs_verification"):
        verification_note = (
            '<div style="margin-top:6px;font-family:Arial,sans-serif;font-size:11px;color:#E65100;">'
            "⚠️ 此來源之解析規則尚待於實際網路環境驗證，內容可能不完整"
            "</div>"
        )

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;border:1px solid #E0E0E0;border-radius:4px;overflow:hidden;">
      <tr>
        <td style="background:{bg};padding:10px 14px;">
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td>
              <span style="font-family:Arial,sans-serif;font-size:11px;color:{fg};background:rgba(0,0,0,0.15);padding:2px 8px;border-radius:3px;font-weight:bold;">{_esc(label)}</span>
              {new_badge}
            </td>
          </tr></table>
          <div style="font-family:Arial,sans-serif;font-size:15px;color:{fg};font-weight:bold;margin-top:6px;">{icon} {title}</div>
        </td>
      </tr>
      <tr><td style="padding:12px 14px;background:#FFFFFF;">
        <table width="100%" cellpadding="3" cellspacing="0">
          <tr>
            <td width="33%" style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">來源：{icon} {_esc(source_name)} / {bureau}</td>
            <td width="33%" style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">公告編號：{notice_number}</td>
            <td width="34%" style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">發布時間：{publish_time}</td>
          </tr>
          <tr>
            <td colspan="2" style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">有效期間：{effective_text}</td>
            <td style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">狀態：{status_label}</td>
          </tr>
          <tr>
            <td colspan="3" style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;">影響海域：{affected_area}</td>
          </tr>
        </table>
        <hr style="border:none;border-top:1px solid #EEEEEE;margin:10px 0;">
        <div style="font-family:Arial,sans-serif;font-size:13px;color:#212121;line-height:1.6;">{summary}</div>
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;"><b>對商船/船隊可能影響：</b> {operational_impact}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#1B5E20;padding-top:4px;"><b>建議行動：</b> {recommended_action}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:12px;color:#455A64;padding-top:4px;"><b>命中關鍵字：</b> {kw_str}</td></tr>
          <tr><td style="font-family:Arial,sans-serif;font-size:11px;color:#78909C;padding-top:4px;"><b>判斷依據：</b> {reasons_str}</td></tr>
        </table>
        {coord_html}
        {verification_note}
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;">
          <tr>
            <td style="background:#E3F2FD;border-radius:3px;padding:8px 10px;">
              <a href="{link}" target="_blank" style="font-family:Arial,sans-serif;font-size:12px;color:#1565C0;font-weight:bold;text-decoration:none;">🔗 查看原始官方公告 →</a>
              <span style="float:right;font-family:Arial,sans-serif;font-size:11px;color:#78909C;">解析可信度：{confidence_text}</span>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
    """


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

    cards_html = "".join(_render_card(w, is_today=False) for w in top)
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
    sorted_today = _sort_by_risk(today_warnings)

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

    today_cards = "".join(_render_card(w, is_today=True) for w in sorted_today)
    history_section = _render_history_stats(history_warnings)
    exec_summary = build_executive_summary(today_warnings, history_warnings, health_reports)

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>航行警告監控報告</title>
</head>
<body style="margin:0;padding:0;background:#F4F6F8;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;">
  <tr><td align="center">
    <table width="700" cellpadding="0" cellspacing="0" style="background:#FFFFFF;max-width:700px;">
      <tr>
        <td style="background:#0A1628;padding:24px;">
          <span style="font-family:Arial,sans-serif;font-size:20px;color:#FFFFFF;font-weight:bold;">航行警告監控報告</span><br><br>
          <span style="font-family:Arial,sans-serif;font-size:12px;color:#8FA3B8;">報告時間：{_esc(generated_at)} (TPE)</span>
        </td>
      </tr>
      {banner}
      <tr><td style="padding:20px;">
        {exec_summary}
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
          <tr><td style="font-family:Arial,sans-serif;font-size:15px;color:#212121;font-weight:bold;border-bottom:2px solid #E0E0E0;padding-bottom:6px;">
            今日新增詳情（依風險等級排序）
          </td></tr>
        </table>
        {today_cards if today_cards else '<div style="font-family:Arial,sans-serif;font-size:13px;color:#78909C;padding:12px 0;">今日無新增警告</div>'}
        {history_section}
      </td></tr>
      <tr>
        <td style="background:#E9ECEF;padding:16px;text-align:center;">
          <span style="font-family:Arial,sans-serif;font-size:11px;color:#6C757D;">
            此為自動發送的郵件，請勿直接回覆。航行警告監控系統。
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
    lines = [f"航行警告監控報告 — {generated_at} (TPE)", "=" * 50, ""]

    if source_anomaly:
        lines.append("⚠ 資料來源異常：本次執行未能正常取得任何來源資料，請勿視為「今日無新增」。")
        lines.append("")

    sorted_today = _sort_by_risk(today_warnings)
    lines.append(f"今日新增：{len(today_warnings)} 筆")
    for i, w in enumerate(sorted_today, 1):
        level = _risk_level_of(w)
        lines.append(f"\n[{i}] ({level}) {w.get('title','N/A')}")
        lines.append(f"    來源: {w.get('bureau', w.get('issuing_bureau',''))} | 時間: {w.get('time', w.get('publish_time',''))}")
        summary = w.get("summary_zh_tw") or w.get("cleaned_content") or ""
        if summary:
            lines.append(f"    摘要: {summary[:200]}")
        action = w.get("recommended_action") or ""
        if action:
            lines.append(f"    建議行動: {action}")
        link = w.get("link") or ""
        if link.startswith("http://") or link.startswith("https://"):
            lines.append(f"    原始公告: {link}")

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
    lines.append("\n此為自動發送的系統異常通知，請勿直接回覆。")
    return "\n".join(lines)
