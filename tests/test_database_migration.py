import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database_manager import DatabaseManager, resolve_source_country, compute_content_hash


def _tmp_db_path():
    return tempfile.mktemp(suffix=".db")


def test_country_mapping_correct_for_all_sources():
    assert resolve_source_country("CN_MSA") == "CN"
    assert resolve_source_country("TW_MPB") == "TW"
    assert resolve_source_country("UKMTO") == "GB"
    assert resolve_source_country("SOMETHING_ELSE") == "UNKNOWN"


def test_upsert_insert_then_dedupe_same_content():
    db = DatabaseManager(db_name=_tmp_db_path())
    item = {"bureau": "浙江海事局", "title": "浙航警0001/26", "link": "https://x/1",
            "cleaned_content": "内容A", "publish_time": "2026-08-06"}

    is_new, is_changed, wid = db.upsert_rich_warning(item, source_type="CN_MSA")
    assert is_new and not is_changed and wid

    is_new2, is_changed2, wid2 = db.upsert_rich_warning(item, source_type="CN_MSA")
    assert not is_new2 and not is_changed2
    assert wid2 == wid

    df = db.get_all_warnings()
    assert len(df) == 1  # 不得因重複呼叫而產生重複資料列


def test_upsert_updates_on_content_change_and_records_last_changed_at():
    db = DatabaseManager(db_name=_tmp_db_path())
    item = {"bureau": "浙江海事局", "title": "浙航警0002/26", "link": "https://x/2",
            "cleaned_content": "原始內容", "publish_time": "2026-08-06"}
    _, _, wid = db.upsert_rich_warning(item, source_type="CN_MSA")

    changed_item = dict(item, cleaned_content="原始內容（已更新，展延至8月底）")
    is_new, is_changed, wid2 = db.upsert_rich_warning(changed_item, source_type="CN_MSA")
    assert not is_new and is_changed and wid2 == wid

    conn = sqlite3.connect(db.db_name)
    row = conn.execute("SELECT last_changed_at FROM warnings WHERE id=?", (wid,)).fetchone()
    assert row[0] is not None


def test_ukmto_saved_with_gb_country_code_not_cn():
    db = DatabaseManager(db_name=_tmp_db_path())
    item = {"bureau": "UKMTO", "title": "Incident report", "link": "https://ukmto.org/x",
            "cleaned_content": "incident", "publish_time": "2026-08-06"}
    _, _, wid = db.upsert_rich_warning(item, source_type="UKMTO")

    conn = sqlite3.connect(db.db_name)
    row = conn.execute("SELECT source_country FROM warnings WHERE id=?", (wid,)).fetchone()
    assert row[0] == "GB"


def test_legacy_save_warning_also_uses_correct_country_map():
    db = DatabaseManager(db_name=_tmp_db_path())
    data = ("UKMTO", "Legacy incident", "https://ukmto.org/legacy", "2026-08-06", "", "2026-08-06 00:00:00", [])
    is_new, wid = db.save_warning(data, source_type="UKMTO")
    assert is_new
    conn = sqlite3.connect(db.db_name)
    row = conn.execute("SELECT source_country FROM warnings WHERE id=?", (wid,)).fetchone()
    assert row[0] == "GB"


def test_migration_on_existing_data_is_non_destructive(tmp_path):
    """在既有資料庫（模擬舊版 schema，只有基本欄位）上執行遷移，資料筆數不得改變。"""
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            maritime_bureau TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT,
            publish_time TEXT,
            keywords_matched TEXT,
            scrape_time TEXT NOT NULL,
            is_notified INTEGER DEFAULT 0,
            notified_time TEXT,
            UNIQUE(maritime_bureau, title, publish_time)
        )
    """)
    conn.execute(
        "INSERT INTO warnings (maritime_bureau, title, link, publish_time, keywords_matched, scrape_time) "
        "VALUES ('山东海事局', '舊資料公告', 'https://x', '2026-01-01', '', '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    db = DatabaseManager(db_name=db_path)  # 觸發遷移

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM warnings").fetchone()[0]
    assert count == 1
    cols = [r[1] for r in conn.execute("PRAGMA table_info(warnings)").fetchall()]
    for expected_col in ("content_hash", "risk_level", "first_seen_at", "last_seen_at", "source_country"):
        assert expected_col in cols


def test_notification_delivery_channels_independent():
    db = DatabaseManager(db_name=_tmp_db_path())
    item = {"bureau": "浙江海事局", "title": "浙航警0003/26", "link": "https://x/3",
            "cleaned_content": "内容", "publish_time": "2026-08-06"}
    _, _, wid = db.upsert_rich_warning(item, source_type="CN_MSA")

    db.record_notification_attempt(wid, "EMAIL", "a@b.com", "SUCCESS")
    db.record_notification_attempt(wid, "TEAMS", "webhook", "FAILED", error="timeout")

    assert db.has_successful_delivery(wid, "EMAIL") is True
    assert db.has_successful_delivery(wid, "TEAMS") is False  # TEAMS 失敗不應被 EMAIL 成功覆蓋


def test_dry_run_style_usage_does_not_mark_notification_success():
    """模擬 --dry-run：不呼叫 upsert / record_notification_attempt，資料庫應保持空白。"""
    db = DatabaseManager(db_name=_tmp_db_path())
    df = db.get_all_warnings()
    assert len(df) == 0
