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


def test_tw_army_training_area_application_is_downgraded_not_critical():
    """實際案例回歸測試：陸軍/空軍的「演訓場域通報申請案／申請單」是行政申請紀錄，
    不是生效中的航行限制，過去只因命中「陸軍」等組織名稱關鍵字就被評為高風險，
    造成主管信箱被大量無關的陸軍行政公告灌爆。"""
    from keyword_manager import KeywordManager

    km = KeywordManager(config_file=CONFIG_PATH)
    tw_keywords = km.get_keywords_by_source("TW_MPB")
    svc = _service()

    noisy_titles = [
        "射擊公告(中區)(修正編號20260495)-陸軍第六軍團指揮部民國115年8月份演訓場域通報單申請案",
        "射擊公告(中區)陸軍第十軍團指揮部民國115年下半年重砲戰術射擊演訓場域通報申請案",
        "射擊公告(南區)空軍115年9月限航區RCR-34演訓場域通報申請單",
    ]
    for title in noisy_titles:
        result = svc.assess(title=title, content="", source_keywords=tw_keywords)
        assert result.risk_level in ("LOW", "INFO"), f"{title} -> {result.risk_level} (應被下修為 LOW/INFO)"
        assert result.is_excluded is True


def test_tw_real_sea_firing_event_stays_high_risk():
    """對照組：真正「已發生／即將發生的對海實彈射擊事件」（報告單/通報，不是申請案）
    仍必須維持高風險，不能因為修掉陸軍雜訊而連真正的海上危險都一起降級。"""
    from keyword_manager import KeywordManager

    km = KeywordManager(config_file=CONFIG_PATH)
    tw_keywords = km.get_keywords_by_source("TW_MPB")
    svc = _service()

    real_event_titles = [
        "射擊公告(中區)陸軍教準部所屬裝訓部民國115年10月份新竹新豐地區實彈射擊報告單",
        "射擊公告(北區)-海洋委員會海巡署艦隊分署北部地區機動海巡隊對海實彈射擊通報。",
    ]
    for title in real_event_titles:
        result = svc.assess(title=title, content="", source_keywords=tw_keywords)
        assert result.risk_level in ("CRITICAL", "HIGH"), f"{title} -> {result.risk_level} (應維持 CRITICAL/HIGH)"
        assert result.is_excluded is False


def test_get_keywords_by_source_tw_excludes_china_specific_category():
    """get_keywords_by_source 改成以分類為準後，台灣航港局不應該拿到「中國特有」
    分類的詞（如 PLA/东部战区），中國海事局不應該拿到「台灣特有」分類的詞。"""
    from keyword_manager import KeywordManager

    km = KeywordManager(config_file=CONFIG_PATH)
    tw_keywords = set(km.get_keywords_by_source("TW_MPB"))
    cn_keywords = set(km.get_keywords_by_source("CN_MSA"))

    # 中國特有分類詞不應出現在台灣航港局的關鍵字清單
    assert "东部战区" not in tw_keywords
    assert "人民解放军" not in tw_keywords
    # 台灣特有分類詞（且未同時存在於其他共用分類）不應出現在中國海事局的關鍵字清單
    assert "ROC NAVY" not in cn_keywords
    assert "TAIWAN STRAIT" not in cn_keywords
    # 共用風險分類（不分國家）雙方都應該拿得到
    assert "实弹射击" in cn_keywords
    assert "實彈射擊" in tw_keywords


def test_army_org_name_alone_no_longer_in_taiwan_category():
    """陸軍/海軍/空軍/國防部/國軍 純組織名稱不應再單獨列為「台灣特有」關鍵字，
    避免光是提到發布單位就被算進風險分數。"""
    import json

    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    tw_category = data["categories"]["台灣特有"]
    for term in ["國防部", "國軍", "海軍", "空軍", "陸軍"]:
        assert term not in tw_category, f"{term} 不應再出現在台灣特有分類"


def test_application_type_notice_excluded_via_exclusion_pattern():
    """「申請案」「申請單」應已加入 exclusion_patterns，作為區分『行政申請』與
    『生效中通知』的判斷依據。"""
    import json

    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "申請案" in data["exclusion_patterns"]
    assert "申請單" in data["exclusion_patterns"]
