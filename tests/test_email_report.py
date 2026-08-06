import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from templates import email_report as tpl


def _sample_warning(**overrides):
    base = {
        "title": "浙航警0020/26 实弹射击",
        "bureau": "浙江海事局",
        "time": "2026-08-06",
        "source": "CN_MSA",
        "link": "https://www.zj.msa.gov.cn/notice/1.html",
        "coordinates": [(29.1, 122.2)],
        "risk_level": "HIGH",
        "relevance_score": 80,
        "risk_score": 80,
        "confidence": 0.85,
        "keywords": ["实弹射击"],
        "scoring_reasons": ["標題命中關鍵字「实弹射击」(+15)"],
        "summary_zh_tw": "浙江舟山海域將進行實彈射擊，禁止船舶駛入。",
        "operational_impact": "影響鄰近商船航線。",
        "recommended_action": "建議繞航。",
        "status": "ACTIVE",
        "affected_waters": "浙江舟山海域",
    }
    base.update(overrides)
    return base


def test_html_escapes_malicious_title_and_blocks_js_url():
    warning = _sample_warning(
        title="<script>alert(1)</script>惡意標題",
        link="javascript:alert(1)",
    )
    html_out = tpl.build_html_report([warning], [])
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "javascript:alert" not in html_out


def test_only_http_https_urls_allowed_in_href():
    warning = _sample_warning(link="ftp://example.com/x")
    html_out = tpl.build_html_report([warning], [])
    assert 'href="ftp://example.com/x"' not in html_out


def test_valid_https_url_preserved():
    warning = _sample_warning(link="https://www.zj.msa.gov.cn/notice/1.html")
    html_out = tpl.build_html_report([warning], [])
    assert "https://www.zj.msa.gov.cn/notice/1.html" in html_out


def test_email_contains_required_fields():
    warning = _sample_warning()
    html_out = tpl.build_html_report([warning], [])
    assert "有效期間" in html_out
    assert "影響海域" in html_out
    assert "建議行動" in html_out
    assert warning["summary_zh_tw"] in html_out
    assert "危急" in html_out or "高 HIGH" in html_out  # 風險等級標籤


def test_risk_levels_get_different_colors():
    critical = _sample_warning(title="危急公告", risk_level="CRITICAL")
    low = _sample_warning(title="低風險公告", risk_level="LOW")
    html_out = tpl.build_html_report([critical, low], [])
    assert "#B71C1C" in html_out  # CRITICAL 紅
    assert "#1565C0" in html_out  # LOW 藍


def test_today_sorted_by_risk_level_critical_first():
    low = _sample_warning(title="低風險", risk_level="LOW")
    critical = _sample_warning(title="危急事件", risk_level="CRITICAL")
    html_out = tpl.build_html_report([low, critical], [])
    assert html_out.index("危急事件") < html_out.index("低風險")


def test_history_not_fully_expanded():
    many_history = [_sample_warning(title=f"歷史公告{i}", risk_level="LOW") for i in range(20)]
    html_out = tpl.build_html_report([], many_history)
    # 只應完整展開 CRITICAL/HIGH（此處全為 LOW，故完整卡片數應為 0，但統計應顯示 20 筆）
    assert "20 筆" in html_out
    assert html_out.count('查看原始官方公告') <= 5


def test_history_high_risk_capped_at_five_full_cards():
    many_high_history = [_sample_warning(title=f"高風險歷史{i}", risk_level="HIGH") for i in range(12)]
    html_out = tpl.build_html_report([], many_high_history)
    assert html_out.count("查看原始官方公告") == 5
    assert "其餘 7 筆" in html_out


def test_subject_includes_risk_level_and_count():
    warning = _sample_warning(risk_level="CRITICAL")
    subject = tpl.build_subject([warning], [])
    assert "CRITICAL" in subject
    assert "今日 1 筆新增" in subject


def test_subject_sanitizes_newlines_header_injection():
    warning = _sample_warning(title="惡意標題\r\nBcc:attacker@evil.com")
    subject = tpl.build_subject([warning], [])
    assert "\r" not in subject
    assert "\n" not in subject


def test_plain_text_alternative_generated():
    warning = _sample_warning()
    plain = tpl.build_plain_text_report([warning], [])
    assert "浙航警0020" in plain
    assert "建議行動" in plain


def test_source_anomaly_banner_shown_when_flagged():
    html_out = tpl.build_html_report([], [], source_anomaly=True)
    assert "資料來源異常" in html_out
    assert "今日無新增航行警告" not in html_out
