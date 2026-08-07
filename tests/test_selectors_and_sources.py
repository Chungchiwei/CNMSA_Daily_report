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


def test_provincial_403_response_is_blocked_not_connection_error(monkeypatch, tmp_path):
    """海南海事局實機回報 403：應歸類為 BLOCKED（疑似反爬封鎖），而不是 CONNECTION_ERROR
    （那是給真正連不上網路的情況用的），claude.md 十四要求需可區分兩者。"""
    from cn_sources.base import SourceBlockedError

    cfg = {
        "source_id": "cn_hainan", "source_name": "海南海事局", "source_type": "CN_MSA",
        "source_country": "CN", "base_url": "https://www.hn.msa.gov.cn",
        "list_url": "https://www.hn.msa.gov.cn/hsfw_1_1/index.jhtml",
        "enabled": True,
        "selectors": {"list_container": [".list_1"], "item": ["li a"]},
    }
    source = ProvincialMSASource(
        "cn_hainan", cfg, save_debug=True, debug_dir=str(tmp_path),
    )

    class FakeResponse:
        status_code = 403
        headers = {"Server": "nginx"}
        text = "<html>Forbidden</html>"

    def fake_get(self, url, timeout=None, verify=None):
        return FakeResponse()

    monkeypatch.setattr(source._session, "get", fake_get.__get__(source._session))

    try:
        source.fetch_list()
        assert False, "應該要拋出 SourceBlockedError"
    except SourceBlockedError as exc:
        assert exc.status_code == 403

    result = source.run()
    assert result.report.final_status == SourceHealthStatus.BLOCKED
    # 403 應該也要保存 debug 快照，方便判斷是否為反爬封鎖
    debug_files = list(tmp_path.glob("*blocked*"))
    assert len(debug_files) >= 1


def test_provincial_empty_parse_saves_debug_snapshot(monkeypatch, tmp_path):
    """HTTP 200 但解析不到任何項目時（selector 可能已失效），需保存快照方便之後調整
    selector（claude.md 四：不得把「解析不到資料」誤判成無法診斷的黑盒子）。"""
    cfg = {
        "source_id": "cn_shanghai", "source_name": "上海海事局", "source_type": "CN_MSA",
        "source_country": "CN", "base_url": "https://www.sh.msa.gov.cn",
        "list_url": "https://www.sh.msa.gov.cn/hxtjgsj/index.jhtml",
        "enabled": True,
        "selectors": {"list_container": [".list_1"], "item": ["li a"]},
    }
    source = ProvincialMSASource(
        "cn_shanghai", cfg, save_debug=True, debug_dir=str(tmp_path),
    )

    class FakeResponse:
        status_code = 200
        headers = {}
        text = "<html><body><div class='totally-different-structure'>沒有符合的內容</div></body></html>"

        def raise_for_status(self):
            return None

    def fake_get(self, url, timeout=None, verify=None):
        return FakeResponse()

    monkeypatch.setattr(source._session, "get", fake_get.__get__(source._session))

    items = source.fetch_list()
    assert items == []
    debug_files = list(tmp_path.glob("*empty_list*"))
    assert len(debug_files) >= 1


def test_central_nav_button_not_found_is_parse_error_not_connection_error(monkeypatch, tmp_path):
    """中央入口找不到「航行警告」導覽按鈕時（可能改版），應歸類為 PARSE_ERROR，
    不得誤判為 CONNECTION_ERROR（那個錯誤訊息文字本身就是在講選擇器找不到，不是連線失敗）。"""
    from unittest.mock import MagicMock, patch
    from cn_sources.central import CentralMSASource

    cfg = {
        "source_id": "cn_central", "source_name": "中國海事局（中央入口）",
        "source_type": "CN_MSA", "source_country": "CN",
        "base_url": "https://www.msa.gov.cn",
        "list_url": "https://www.msa.gov.cn/page/outter/weather.jsp",
        "enabled": True,
        "selectors": {"nav_trigger_text": ["航行警告"]},
    }
    source = CentralMSASource(
        "cn_central", cfg, save_debug=True, debug_dir=str(tmp_path),
    )

    fake_driver = MagicMock()
    fake_driver.page_source = "<html>改版後的頁面，找不到候選文字</html>"

    with patch.object(source, "_init_driver", return_value=fake_driver), \
         patch("cn_sources.central.time.sleep", return_value=None), \
         patch("selenium.webdriver.support.ui.WebDriverWait") as MockWait:
        MockWait.return_value.until.side_effect = Exception("timeout: 找不到候選文字")
        result = source.run()

    assert result.report.final_status == SourceHealthStatus.PARSE_ERROR
    assert "航行警告" in result.report.error_summary or "PARSE_ERROR" in result.report.error_summary
    debug_files = list(tmp_path.glob("*nav_click_failed*"))
    assert len(debug_files) >= 1


def test_real_left_nav_fragment_matches_aria_label_strategy():
    """2026-08-06 使用者實機回報：「航行警告」導覽項目其實是一個 role="select" 的自訂
    ARIA 下拉元件，不是單純文字節點。這裡用逐字保留的真實片段確認：
    (1) 舊版純文字比對邏輯（span 含文字「航行警告」）理論上仍能命中這個元素，
    (2) 新增的 aria-label 比對策略也確實命中，且比對到的就是同一個元素，
    避免日後修改 selector 時對這個假設有誤解。"""
    from bs4 import BeautifulSoup

    html = (FIXTURES / "cn_central_left_nav_real_snippet.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    span = soup.find("span", attrs={"role": "select"})
    assert span is not None
    assert "航行警告" in span.get("aria-label", "")
    assert "航行警告" in span.get_text()
    assert span.get("aria-owns") == ".left_nav > ul > li:nth-child(2) > ul"

    # 確認 .left_nav 容器策略也能定位到同一層級
    left_nav = soup.select_one(".left_nav")
    assert left_nav is not None
    assert span in left_nav.find_all("span")


def test_central_fetch_list_tries_aria_label_xpath_strategy_first(monkeypatch, tmp_path):
    """驗證 fetch_list() 真的會依序嘗試新加入的 aria-label 策略，而不是只有加了候選字串
    但程式邏輯沒有真正呼叫到。用 MagicMock 記錄實際呼叫的 XPath，確認第一個成功的
    策略字串包含 aria-label（對應使用者回報的真實元件結構）。"""
    from unittest.mock import MagicMock, patch
    from cn_sources.central import CentralMSASource

    cfg = {
        "source_id": "cn_central", "source_name": "中國海事局（中央入口）",
        "source_type": "CN_MSA", "source_country": "CN",
        "base_url": "https://www.msa.gov.cn",
        "list_url": "https://www.msa.gov.cn/html/cnmsa/hxaq/aqxx/index.html",
        "enabled": True,
        "selectors": {"nav_trigger_text": ["航行警告"], "bureau_menu": [".left_nav li a"]},
    }
    source = CentralMSASource("cn_central", cfg, save_debug=True, debug_dir=str(tmp_path))

    fake_driver = MagicMock()
    fake_driver.page_source = "<html></html>"
    fake_driver.find_elements.return_value = []  # 沒有 bureau，走 FALLBACK_BUREAUS，不影響本測試重點

    used_xpaths = []

    def fake_ec_element_to_be_clickable(locator):
        # locator 是 (By.XPATH, xpath) tuple；直接把 xpath 字串當成「condition」回傳，
        # 這樣底下 WebDriverWait.until(condition) 收到的就是 xpath 字串本身，
        # 不需要猜 Selenium 內部 condition 物件的屬性名稱，更穩定。
        xpath = locator[1]
        used_xpaths.append(xpath)
        return xpath

    def fake_until(condition_xpath):
        if "aria-label" in condition_xpath:
            return MagicMock()  # 第一個策略（aria-label）成功
        raise Exception("not found")

    with patch.object(source, "_init_driver", return_value=fake_driver), \
         patch("cn_sources.central.time.sleep", return_value=None), \
         patch("selenium.webdriver.support.expected_conditions.element_to_be_clickable",
               side_effect=fake_ec_element_to_be_clickable), \
         patch("selenium.webdriver.support.ui.WebDriverWait") as MockWait:
        MockWait.return_value.until.side_effect = fake_until
        result = source.run()

    assert used_xpaths, "應該至少嘗試過一種 XPath 策略"
    assert "aria-label" in used_xpaths[0], f"應優先嘗試 aria-label 策略，實際嘗試順序：{used_xpaths}"
    assert result.report.final_status in (
        SourceHealthStatus.EMPTY, SourceHealthStatus.PARSE_ERROR, SourceHealthStatus.HEALTHY, SourceHealthStatus.PARTIAL,
    )


def test_real_bureau_menu_extracts_all_16_bureaus():
    """2026-08-06 使用者實機回報：展開後的省級選單，確認 .nav_lv2_text 選擇器（既有的
    第一/第二候選）在真實頁面上依然有效，且能取得完整 16 個海事局名稱。"""
    html = (FIXTURES / "cn_central_real_bureau_menu_and_list.html").read_text(encoding="utf-8")
    names = parse_bureau_menu_html(html, [".nav_lv2_list .nav_lv2_text"])
    assert len(names) == 16
    assert "上海海事局" in names
    assert "江西省地方海事局" in names


def test_real_list_html_parses_via_main_list_ul_container():
    """2026-08-06 使用者實機回報的上海海事局警告列表真實 HTML：確認新加入的
    .main_list_ul 容器＋span.name 標題擷取邏輯，能正確解析出標題／日期／連結。"""
    html = (FIXTURES / "cn_central_real_bureau_menu_and_list.html").read_text(encoding="utf-8")
    items = parse_warning_list_html(html, [".main_list_ul"])
    assert len(items) == 3
    assert items[0]["title"] == "拖带作业—沪航警606/26"
    assert items[0]["publish_time"] == "2026-08-04"
    assert items[0]["link"] == (
        "https://www.msa.gov.cn/html/cnmsa/hxaq/article/2026/"
        "9319c673f32541b396b4d835ba7c59f8.html?hav=1jWj6r2hy3yfaITPii5X992DSQ"
    )
    assert items[2]["title"] == "LNG受注作业—沪航警604/26"
    assert items[2]["publish_time"] == "2026-08-03"


def test_real_list_html_parses_via_default_container_selectors():
    """確認 DEFAULT_LIST_CONTAINER_SELECTORS（未指定候選清單時的預設值）已經把
    .main_list_ul 排在第一位，不需要外部手動指定也能解析出真實資料。"""
    html = (FIXTURES / "cn_central_real_bureau_menu_and_list.html").read_text(encoding="utf-8")
    items = parse_warning_list_html(html)  # 用預設候選清單
    assert len(items) == 3


def test_fallback_bureaus_matches_real_confirmed_list():
    """FALLBACK_BUREAUS 應該是使用者實機回報的真實清單，而不是先前的猜測清單
    （先前清單裡的「黑龍江海事局」在真實選單中並未出現）。"""
    from cn_sources.central import FALLBACK_BUREAUS

    assert "黑龙江海事局" not in FALLBACK_BUREAUS
    assert "江苏省地方海事局" in FALLBACK_BUREAUS
    assert "江西省地方海事局" in FALLBACK_BUREAUS
    assert len(FALLBACK_BUREAUS) == 16
