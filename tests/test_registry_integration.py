"""
從 Orchestrator（CNSourceRegistry.run()）入口出發的整合測試，而不是只呼叫
單一 Service（例如只測 RiskAssessmentService.assess() 或只測 _process_item）。

透過 monkeypatch cn_sources.registry._build_source，以可控的 FakeSource
取代真正需要網路的 CentralMSASource / ProvincialMSASource，藉此驗證：
  - registry.run() 的完整流程（fetch_list -> enrich_item -> 關鍵字判斷 -> 風險評分 -> 分桶）
  - 標題無關鍵字但內文命中時仍保留（claude.md 五）
  - 撤銷／展延／更正／排除規則／日期無法解析／詳情失敗但標題高風險 等邊界案例
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cn_sources.base import BaseMaritimeSource
from cn_sources.registry import CNSourceRegistry

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_CONFIG = str(FIXTURES / "fake_registry_config.json")


class FakeKeywordManager:
    """
    模擬 KeywordManager.get_keywords_by_source()。真實 keywords_config.json 對簡繁體
    是各自收錄獨立詞條（而非自動簡繁轉換），這裡比照真實設定同時提供簡體與繁體變體，
    避免測試因用詞的簡繁差異而失真。
    """

    def get_keywords_by_source(self, source_type):
        return [
            "实弹射击", "實彈射擊",
            "禁航区", "禁航區",
            "潜水作业", "潛水作業",
            "军事演习", "軍事演習",
        ]


class FakeSource(BaseMaritimeSource):
    """以固定 raw_items 模擬一個真實來源，不連網路，但走完整 BaseMaritimeSource.run() 流程。"""

    def __init__(self, source_id, config, raw_items=None, fail_enrich_for=None):
        super().__init__(source_id, config)
        self._raw_items = raw_items or []
        self._fail_enrich_for = fail_enrich_for or set()

    def fetch_list(self):
        return list(self._raw_items)

    def enrich_item(self, raw_item):
        if raw_item.get("title") in self._fail_enrich_for:
            # 模擬詳情頁抓取失敗：仍需回傳至少 title，讓標題本身的高風險字詞有機會被判斷
            return {**raw_item, "cleaned_content": "", "bureau": "測試海事局", "coordinates": []}
        return {**raw_item, "bureau": "測試海事局"}


def _make_registry(raw_items, fail_enrich_for=None):
    registry = CNSourceRegistry(config_path=FAKE_CONFIG, keyword_manager=FakeKeywordManager())

    def _fake_build_source(source_id, cfg, coordinate_extractor, headless, save_debug, debug_dir):
        return FakeSource(source_id, cfg, raw_items=raw_items, fail_enrich_for=fail_enrich_for)

    return registry, _fake_build_source


def _run_with_items(raw_items, fail_enrich_for=None):
    registry, fake_builder = _make_registry(raw_items, fail_enrich_for)
    with patch("cn_sources.registry._build_source", side_effect=fake_builder):
        return registry.run()


TODAY = __import__("datetime").datetime.now().strftime("%Y-%m-%d")


def test_title_without_keyword_content_has_keyword_kept_end_to_end():
    """claude.md 五、核心測試案例：標題「浙航警 128/26」無關鍵字，內文含「實彈射擊」。"""
    result = _run_with_items([{
        "title": "浙航警 128/26",
        "link": "https://example.com/128",
        "publish_time": TODAY,
        "cleaned_content": "2026年8月6日0800時至1800時，在下列四點連線水域範圍內進行實彈射擊，禁止船舶駛入。",
        "coordinates": [(29.0, 122.0)],
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1
    item = all_items[0]
    assert "实弹射击" in item["keywords_matched"] or "實彈射擊" in str(item["keywords_matched"])
    assert item["risk_level"] != "INFO"
    assert item["status"] == "ACTIVE"
    assert item.get("coordinates")


def test_revoked_notice_kept_with_cancelled_status():
    result = _run_with_items([{
        "title": "浙航警 099/26",
        "link": "https://example.com/099",
        "publish_time": TODAY,
        "cleaned_content": "浙航警099/26关于禁航区实弹射击的公告予以撤销，恢复正常通航。",
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1  # 撤銷公告仍須保留，不得整筆丟棄
    assert all_items[0]["status"] == "CANCELLED"


def test_extended_notice_stays_active_and_matches_keywords():
    result = _run_with_items([{
        "title": "浙航警 100/26",
        "link": "https://example.com/100",
        "publish_time": TODAY,
        "cleaned_content": "浙航警100/26禁航区实弹射击公告有效期展延至8月20日，其余内容不变。",
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1
    assert all_items[0]["status"] == "ACTIVE"


def test_corrected_notice_matches_and_is_kept():
    result = _run_with_items([{
        "title": "浙航警 101/26（更正）",
        "link": "https://example.com/101",
        "publish_time": TODAY,
        "cleaned_content": "更正：浙航警101/26禁航区坐标更正如下，实弹射击时间不变。",
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1


def test_content_only_coordinates_title_has_event_word():
    """內文只有座標、標題有事件詞：仍應被保留（標題已足以命中關鍵字）。"""
    result = _run_with_items([{
        "title": "禁航区公告",
        "link": "https://example.com/coords-only",
        "publish_time": TODAY,
        "cleaned_content": "北纬29-10.5 东经122-20.3 北纬29-15.0 东经122-25.0",
        "coordinates": [(29.175, 122.333), (29.25, 122.417)],
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1


def test_completely_unrelated_content_is_dropped():
    result = _run_with_items([{
        "title": "港口作息時間調整通知",
        "link": "https://example.com/unrelated",
        "publish_time": TODAY,
        "cleaned_content": "自即日起，港口辦公室作息時間調整為上午九點至下午五點。",
    }])
    assert result.today == [] and result.history == []


def test_exclusion_pattern_hit_is_dropped_when_not_cancellation():
    """命中排除規則（例如純測試貼文）且非撤銷公告時應被丟棄。"""
    result = _run_with_items([{
        "title": "TEST 军事演习公告",
        "link": "https://example.com/test-post",
        "publish_time": TODAY,
        "cleaned_content": "本則為系統 TEST 測試貼文，非正式公告。",
    }])
    assert result.today == [] and result.history == []


def test_unparseable_date_falls_into_history_bucket_not_crash():
    """日期無法解析時不得造成例外，且不應被誤判為今日。"""
    result = _run_with_items([{
        "title": "浙航警 102/26 实弹射击",
        "link": "https://example.com/102",
        "publish_time": "不明日期格式",
        "cleaned_content": "本海域进行实弹射击演习。",
    }])
    all_items = result.today + result.history
    assert len(all_items) == 1
    assert all_items[0] in result.history  # 無法判斷日期時歸入 history，不誤判為今日新增


def test_detail_fetch_failed_but_high_risk_title_still_captured():
    """詳細頁失敗但標題本身即為高風險事件時，仍應以標題內容評估並保留。"""
    result = _run_with_items(
        [{
            "title": "东海军事演习禁航区实弹射击公告",
            "link": "https://example.com/detail-fail",
            "publish_time": TODAY,
        }],
        fail_enrich_for={"东海军事演习禁航区实弹射击公告"},
    )
    all_items = result.today + result.history
    assert len(all_items) == 1
    assert all_items[0]["risk_level"] != "INFO"


def test_multiple_items_single_batch_mixed_outcomes_no_crash():
    """單一批次中同時包含應保留與應丟棄的項目，確認彼此互不影響（orchestrator 層級隔離）。"""
    result = _run_with_items([
        {"title": "浙航警 A", "link": "https://example.com/a", "publish_time": TODAY,
         "cleaned_content": "实弹射击禁航区公告"},
        {"title": "無關公告", "link": "https://example.com/b", "publish_time": TODAY,
         "cleaned_content": "辦公室搬遷通知"},
        {"title": "浙航警 C 予以撤销", "link": "https://example.com/c", "publish_time": TODAY,
         "cleaned_content": "军事演习公告予以撤销"},
    ])
    all_items = result.today + result.history
    titles = {item["title"] for item in all_items}
    assert "浙航警 A" in titles
    assert "無關公告" not in titles
    assert "浙航警 C 予以撤销" in titles
    cancelled = [i for i in all_items if i["title"] == "浙航警 C 予以撤销"]
    assert cancelled[0]["status"] == "CANCELLED"
