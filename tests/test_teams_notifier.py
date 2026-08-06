import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notifications.teams_notifier import (
    build_adaptive_card_payload,
    build_system_anomaly_card,
    TeamsNotifier,
    MAX_CARDS_PER_BATCH,
)


def _w(**overrides):
    base = {
        "title": "浙航警0020/26 实弹射击",
        "bureau": "浙江海事局",
        "time": "2026-08-06",
        "link": "https://www.zj.msa.gov.cn/notice/1.html",
        "coordinates": [(29.1, 122.2)],
        "risk_level": "HIGH",
        "summary_zh_tw": "浙江舟山海域將進行實彈射擊。",
        "recommended_action": "建議繞航。",
        "status": "ACTIVE",
        "affected_waters": "浙江舟山海域",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- payload schema

def test_payload_has_valid_adaptive_card_schema():
    payload = build_adaptive_card_payload([_w()], "測試批次")
    assert payload["type"] == "message"
    card = payload["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert isinstance(card["body"], list) and len(card["body"]) > 0


def test_no_warnings_returns_none_payload():
    assert build_adaptive_card_payload([], "空批次") is None


def test_long_text_is_truncated():
    long_summary = "危" * 1000
    payload = build_adaptive_card_payload([_w(summary_zh_tw=long_summary)], "截斷測試")
    card_text = str(payload)
    assert "危" * 1000 not in card_text
    assert len(card_text) < 5000  # 遠低於未截斷時的長度


def test_more_than_max_batch_shows_remaining_count():
    many = [_w(title=f"公告{i}", risk_level="LOW") for i in range(MAX_CARDS_PER_BATCH + 5)]
    payload = build_adaptive_card_payload(many, "批次上限測試")
    card_text = str(payload)
    assert "另有 5 筆" in card_text
    # 只應該有 MAX_CARDS_PER_BATCH 筆完整卡片（用標題數量概估）
    assert card_text.count("查看原文") <= MAX_CARDS_PER_BATCH


def test_no_coordinates_no_map_button():
    payload = build_adaptive_card_payload([_w(coordinates=[])], "無座標測試")
    card = payload["attachments"][0]["content"]
    action_titles = [a.get("title", "") for a in card.get("actions", [])]
    assert not any("地圖" in t for t in action_titles)


def test_with_coordinates_has_map_button():
    payload = build_adaptive_card_payload([_w()], "有座標測試")
    card = payload["attachments"][0]["content"]
    action_titles = [a.get("title", "") for a in card.get("actions", [])]
    assert any("地圖" in t for t in action_titles)


def test_unsafe_url_removes_button_not_replaced_with_guess():
    payload = build_adaptive_card_payload([_w(link="javascript:alert(1)")], "不安全URL測試")
    card = payload["attachments"][0]["content"]
    for action in card.get("actions", []):
        assert not action["url"].startswith("javascript:")
    # 不應該有「查看原文」按鈕，因為連結不安全
    action_titles = [a.get("title", "") for a in card.get("actions", [])]
    assert not any("查看原文" in t for t in action_titles)


def test_markdown_injection_in_title_is_escaped():
    payload = build_adaptive_card_payload([_w(title="[點我](javascript:alert(1))")], "注入測試")
    card_text = str(payload)
    assert "[點我](javascript:alert(1))" not in card_text
    assert "\\[" in card_text or "\\(" in card_text


def test_risk_levels_sorted_critical_first():
    low = _w(title="低風險", risk_level="LOW")
    critical = _w(title="危急事件", risk_level="CRITICAL")
    payload = build_adaptive_card_payload([low, critical], "排序測試")
    card_text = str(payload)
    assert card_text.index("危急事件") < card_text.index("低風險")


def test_cancelled_status_shown_as_cancelled_not_original_risk_level():
    payload = build_adaptive_card_payload([_w(risk_level="HIGH", status="CANCELLED")], "取消測試")
    card_text = str(payload)
    assert "[CANCELLED]" in card_text


def test_system_anomaly_card_schema():
    card = build_system_anomaly_card("中國海事局來源異常", ["浙江海事局: CONNECTION_ERROR", "上海海事局: EMPTY"])
    assert card["type"] == "message"
    assert "中國海事局來源異常" in str(card)


# ---------------------------------------------------------------- TeamsNotifier (mocked HTTP)

def test_dry_run_never_calls_requests_post():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x")
    with patch("notifications.teams_notifier.requests.post") as mock_post:
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=True)
    mock_post.assert_not_called()
    assert result.success is False
    assert result.skipped is True


def test_no_webhook_configured_skips_without_calling_requests():
    notifier = TeamsNotifier("")
    with patch("notifications.teams_notifier.requests.post") as mock_post:
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    mock_post.assert_not_called()
    assert result.skipped is True


def test_successful_send_returns_success():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x")
    mock_response = MagicMock(status_code=200, text="")
    with patch("notifications.teams_notifier.requests.post", return_value=mock_response) as mock_post:
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    mock_post.assert_called_once()
    assert result.success is True
    assert result.http_status == 200


def test_http_400_is_failure_no_retry():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x", max_retries=1)
    mock_response = MagicMock(status_code=400, text="bad request")
    with patch("notifications.teams_notifier.requests.post", return_value=mock_response) as mock_post:
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    assert mock_post.call_count == 1  # 400 不重試
    assert result.success is False
    assert result.http_status == 400


def test_http_429_retries_then_succeeds():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x", max_retries=1)
    responses = [MagicMock(status_code=429, text="rate limited"), MagicMock(status_code=200, text="")]
    with patch("notifications.teams_notifier.requests.post", side_effect=responses) as mock_post, \
         patch("notifications.teams_notifier.time.sleep"):
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    assert mock_post.call_count == 2
    assert result.success is True


def test_http_500_exhausts_retries_and_fails():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x", max_retries=1)
    mock_response = MagicMock(status_code=500, text="server error")
    with patch("notifications.teams_notifier.requests.post", return_value=mock_response) as mock_post, \
         patch("notifications.teams_notifier.time.sleep"):
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    assert mock_post.call_count == 2  # 初次 + 1 次重試
    assert result.success is False


def test_timeout_is_handled_gracefully():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x", max_retries=0)
    with patch("notifications.teams_notifier.requests.post", side_effect=requests_timeout_exc()) as mock_post:
        result = notifier.send_batch([_w()], "CN_MSA", is_today=True, dry_run=False)
    assert result.success is False
    assert "逾時" in result.error


def requests_timeout_exc():
    import requests
    return requests.exceptions.Timeout("timed out")


def test_ssl_verify_uses_shared_config_not_disabled():
    notifier = TeamsNotifier("https://fake.webhook.office.com/x")
    assert notifier._ssl_verify is True or isinstance(notifier._ssl_verify, str)
    assert notifier._ssl_verify is not False
