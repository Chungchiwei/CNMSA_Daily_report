import sys
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.source_health_alert import detect_anomaly
from templates.email_report import (
    build_system_anomaly_html, build_system_anomaly_plain_text, build_system_anomaly_subject,
)


class _Status(str, Enum):
    HEALTHY = "HEALTHY"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    DISABLED = "DISABLED"


@dataclass
class _FakeReport:
    source_name: str
    final_status: _Status
    newest_publish_date: str = None
    error_summary: str = ""
    list_item_count: int = 0

    def to_row(self):
        return {
            "來源": self.source_name, "狀態": self.final_status.value,
            "最新公告日期": self.newest_publish_date, "錯誤": self.error_summary or "-",
        }


def test_all_sources_failed_triggers_anomaly():
    reports = [
        _FakeReport("浙江海事局", _Status.CONNECTION_ERROR, error_summary="連線逾時"),
        _FakeReport("江蘇海事局", _Status.EMPTY),
    ]
    anomaly = detect_anomaly(reports)
    assert anomaly is not None
    assert "所有" in anomaly.reason
    assert len(anomaly.failed_sources) == 2


def test_healthy_sources_no_anomaly():
    reports = [
        _FakeReport("浙江海事局", _Status.HEALTHY, newest_publish_date="2026-08-06"),
        _FakeReport("江蘇海事局", _Status.HEALTHY, newest_publish_date="2026-08-05"),
    ]
    anomaly = detect_anomaly(reports)
    assert anomaly is None


def test_no_new_warnings_but_healthy_is_not_anomaly():
    """關鍵區分：HTTP 成功且確實沒有新公告 != 異常。"""
    reports = [_FakeReport("浙江海事局", _Status.HEALTHY, newest_publish_date="2026-08-06", list_item_count=0)]
    anomaly = detect_anomaly(reports)
    assert anomaly is None


def test_disabled_sources_ignored():
    reports = [_FakeReport("福建海事局", _Status.DISABLED)]
    assert detect_anomaly(reports) is None


def test_majority_failed_with_fallback_flags_fallback_used():
    reports = [
        _FakeReport("中央入口", _Status.CONNECTION_ERROR, error_summary="逾時"),
        _FakeReport("浙江海事局", _Status.CONNECTION_ERROR, error_summary="逾時"),
        _FakeReport("上海海事局", _Status.HEALTHY, newest_publish_date="2026-08-06"),
    ]
    anomaly = detect_anomaly(reports)
    assert anomaly is not None
    assert anomaly.fallback_used is True
    assert len(anomaly.healthy_sources) == 1


def test_stale_data_beyond_threshold_triggers_anomaly():
    reports = [_FakeReport("浙江海事局", _Status.HEALTHY, newest_publish_date="2026-01-01")]
    anomaly = detect_anomaly(reports, threshold_days=14)
    assert anomaly is not None
    assert "未見更新" in anomaly.reason


def test_anomaly_email_subject_and_content_distinct_from_regular_report():
    reports = [_FakeReport("浙江海事局", _Status.CONNECTION_ERROR, error_summary="連線逾時")]
    anomaly = detect_anomaly(reports)
    subject = build_system_anomaly_subject(anomaly)
    assert "系統異常" in subject
    assert "無新增" not in subject  # 不得與「今日無新增」混淆

    html_out = build_system_anomaly_html(anomaly)
    assert "抓取失敗" in html_out
    assert "並非" in html_out  # 明確聲明不是「今日沒有新警告」

    plain = build_system_anomaly_plain_text(anomaly)
    assert "系統異常" in plain
    assert "浙江海事局" in plain
