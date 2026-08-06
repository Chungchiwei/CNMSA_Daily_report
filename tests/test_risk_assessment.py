import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.risk_assessment import RiskAssessmentService

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "keywords_config.json")


def _service():
    return RiskAssessmentService(keywords_config_path=CONFIG_PATH)


def test_no_keywords_matched_gives_info_level():
    svc = _service()
    result = svc.assess(title="一般公告", content="沒有任何相關字詞")
    assert result.risk_level == "INFO"
    assert result.matched_keywords == []


def test_title_hit_high_priority_keyword_is_critical_or_high():
    svc = _service()
    result = svc.assess(title="东海实弹射击警告", content="")
    assert "实弹射击" in result.matched_keywords
    assert result.risk_level in ("CRITICAL", "HIGH")
    assert result.action_required is True
    assert any("实弹射击" in reason for reason in result.scoring_reasons)


def test_content_only_hit_still_detected_and_scored():
    """claude.md 五之核心要求：標題無關鍵字，但內文命中時仍須保留並評分。"""
    svc = _service()
    result = svc.assess(title="浙航警0019/26", content="本海域将进行实弹射击，禁止船舶驶入")
    assert "实弹射击" in result.matched_keywords
    assert result.risk_score > 0
    assert result.risk_level != "INFO"


def test_multiple_categories_increase_score():
    svc = _service()
    single = svc.assess(title="", content="禁航区")
    multi = svc.assess(title="", content="禁航区 实弹射击 潜水作业")
    assert multi.risk_score >= single.risk_score
    assert len(multi.matched_categories) >= 2


def test_exclusion_pattern_downgrades_risk():
    svc = _service()
    active = svc.assess(title="军事演习警告", content="")
    cancelled = svc.assess(title="军事演习警告", content="本演习已取消")
    assert cancelled.is_excluded is True
    assert cancelled.risk_score < active.risk_score


def test_cancelled_status_reduces_score():
    svc = _service()
    result = svc.assess(title="实弹射击公告", content="", status="CANCELLED")
    assert "已撤銷" in " ".join(result.scoring_reasons) or "取消" in " ".join(result.scoring_reasons)


def test_coordinates_presence_increases_score():
    svc = _service()
    without_coords = svc.assess(title="禁航区公告", content="", has_coordinates=False)
    with_coords = svc.assess(title="禁航区公告", content="", has_coordinates=True)
    assert with_coords.risk_score >= without_coords.risk_score


def test_source_keywords_restricts_matching():
    svc = _service()
    result = svc.assess(title="ROC NAVY exercise", content="", source_keywords=["实弹射击", "禁航区"])
    assert result.matched_keywords == []  # "ROC NAVY" 不在限定清單內
