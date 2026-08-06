import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cn_sources.base import BaseMaritimeSource, SourceHealthStatus, SourceRunResult
from cn_sources.central import parse_warning_list_html, parse_bureau_menu_html
from cn_sources.provincial import ProvincialMSASource
from cn_sources.registry import CNSourceRegistry, parse_date

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_central_parses_old_structure():
    html = (FIXTURES / "cn_central_list_old.html").read_text(encoding="utf-8")
    items = parse_warning_list_html(html)
    assert len(items) == 2
    assert items[0]["title"] == "浙航警0012/26"
    assert items[0]["publish_time"] == "2026-08-05"


def test_central_selector_fallback_handles_new_structure():
    """網站改版後 .right_main 消失、改用 .conMain，selector 候選清單應可自動 fallback。"""
    html = (FIXTURES / "cn_central_list_new_structure.html").read_text(encoding="utf-8")
    items = parse_warning_list_html(html)  # 使用預設候選清單（含 .conMain）
    assert len(items) == 1
    assert items[0]["title"] == "浙航警0013/26"


def test_central_returns_empty_list_when_no_selector_matches():
    items = parse_warning_list_html("<html><body><p>完全不同的結構</p></body></html>")
    assert items == []


def test_provincial_parse_list_from_fixture():
    cfg = {
        "source_id": "cn_zhejiang", "source_name": "浙江海事局", "source_type": "CN_MSA",
        "source_country": "CN", "base_url": "https://www.zj.msa.gov.cn",
        "list_url": "https://www.zj.msa.gov.cn/",
        "selectors": {
            "list_container": [".list_1"],
            "item": ["li a"],
            "date": [".time"],
            "detail_container": [".conMain"],
        },
    }
    source = ProvincialMSASource("cn_zhejiang", cfg)
    html = (FIXTURES / "cn_provincial_zhejiang_list.html").read_text(encoding="utf-8")
    items = source.parse_list(html)
    assert len(items) == 2
    assert "实弹射击" in items[0]["title"]
    assert items[1]["publish_time"] == "2026-08-05"


def test_provincial_detail_content_cleaned_and_nav_footer_removed():
    cfg = {
        "source_id": "cn_zhejiang", "source_name": "浙江海事局", "source_type": "CN_MSA",
        "source_country": "CN", "base_url": "https://www.zj.msa.gov.cn",
        "list_url": "https://www.zj.msa.gov.cn/",
        "selectors": {"detail_container": [".conMain"]},
    }
    source = ProvincialMSASource("cn_zhejiang", cfg)
    html = (FIXTURES / "cn_provincial_detail_no_title_keyword.html").read_text(encoding="utf-8")
    parsed = source.parse_detail({}, html)
    assert "实弹射击" in parsed["cleaned_content"]
    assert "版权所有" not in parsed["cleaned_content"]  # 頁尾應被移除
    assert "首頁" not in parsed["cleaned_content"]        # 導覽列應被移除


def test_title_without_keyword_but_content_has_keyword_is_kept_by_registry():
    """claude.md 五、test-case 5：標題沒有關鍵字，但正文包含「實彈射擊」時仍會保留。"""

    class FakeKeywordManager:
        def get_keywords_by_source(self, source_type):
            return ["实弹射击", "禁航区", "潜水作业"]

    registry = CNSourceRegistry(
        config_path=str(FIXTURES / "does_not_exist.json"),  # registry 不需要真的抓取，只測 _process_item
        keyword_manager=FakeKeywordManager(),
    )
    raw_item = {
        "title": "浙航警0019/26",  # 標題無關鍵字
        "cleaned_content": "浙江海事局关于在某海域进行实弹射击的通知，自8月10日起禁止船舶驶入。",
        "bureau": "浙江海事局",
        "coordinates": [],
    }
    processed = registry._process_item(raw_item, ["实弹射击", "禁航区", "潜水作业"])
    assert processed is not None
    assert "实弹射击" in processed["keywords_matched"]
    assert processed["risk_level"] != "INFO"


def test_title_and_content_both_without_keyword_is_dropped():
    class FakeKeywordManager:
        def get_keywords_by_source(self, source_type):
            return ["实弹射击"]

    registry = CNSourceRegistry(
        config_path=str(FIXTURES / "does_not_exist.json"),
        keyword_manager=FakeKeywordManager(),
    )
    raw_item = {"title": "一般通告", "cleaned_content": "無相關內容", "bureau": "浙江海事局"}
    processed = registry._process_item(raw_item, ["实弹射击"])
    assert processed is None


def test_http_200_but_zero_items_is_empty_not_healthy():
    class ZeroItemSource(BaseMaritimeSource):
        def fetch_list(self):
            return []  # 模擬 HTTP 200 但解析到 0 筆

        def enrich_item(self, raw_item):
            return raw_item

    source = ZeroItemSource("zero_test", {"source_name": "測試來源", "enabled": True})
    result = source.run()
    assert result.report.final_status == SourceHealthStatus.EMPTY
    assert result.items == []


def test_parse_error_when_items_exist_but_all_enrichment_fails():
    class AllFailSource(BaseMaritimeSource):
        def fetch_list(self):
            return [{"title": "a"}, {"title": "b"}]

        def enrich_item(self, raw_item):
            raise ValueError("解析失敗")

    source = AllFailSource("fail_test", {"source_name": "測試來源2", "enabled": True})
    result = source.run()
    assert result.report.final_status == SourceHealthStatus.PARSE_ERROR


def test_single_source_failure_does_not_affect_other_sources():
    class BrokenSource(BaseMaritimeSource):
        def fetch_list(self):
            raise ConnectionError("模擬連線失敗")

        def enrich_item(self, raw_item):
            return raw_item

    class WorkingSource(BaseMaritimeSource):
        def fetch_list(self):
            return [{"title": "正常項目", "publish_time": "2026-08-06"}]

        def enrich_item(self, raw_item):
            return raw_item

    broken = BrokenSource("broken", {"source_name": "壞掉的來源", "enabled": True})
    working = WorkingSource("working", {"source_name": "正常來源", "enabled": True})

    broken_result = broken.run()
    working_result = working.run()

    assert broken_result.report.final_status == SourceHealthStatus.CONNECTION_ERROR
    assert working_result.report.final_status == SourceHealthStatus.HEALTHY
    assert len(working_result.items) == 1


def test_disabled_source_reports_disabled_status():
    class AnySource(BaseMaritimeSource):
        def fetch_list(self):
            return [{"title": "不應被呼叫"}]

        def enrich_item(self, raw_item):
            return raw_item

    source = AnySource("disabled_test", {"source_name": "停用來源", "enabled": False})
    result = source.run()
    assert result.report.final_status == SourceHealthStatus.DISABLED
    assert result.items == []


def test_parse_date_formats():
    assert parse_date("2026-08-06").strftime("%Y-%m-%d") == "2026-08-06"
    assert parse_date("2026/08/06").strftime("%Y-%m-%d") == "2026-08-06"
    assert parse_date("2026年8月6日").strftime("%Y-%m-%d") == "2026-08-06"
    assert parse_date("") is None
    assert parse_date("不是日期") is None
