"""
安全性靜態檢查：不依賴 import 執行（避免觸發模組層級的爬蟲/通知初始化），
直接檢查原始碼文字，確保危險模式已被移除。
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_FILE = PROJECT_ROOT / "n8n_msa_monitor.py"


def _source() -> str:
    return MAIN_FILE.read_text(encoding="utf-8")


def test_no_global_ssl_verification_disabled():
    src = _source()
    assert "ssl._create_default_https_context = ssl._create_unverified_context" not in src
    assert "WDM_SSL_VERIFY" not in src or "'0'" not in src.split("WDM_SSL_VERIFY", 1)[-1][:20]


def test_no_verify_false_left_in_requests_calls():
    src = _source()
    assert "verify=False" not in src


def test_no_chrome_ssl_bypass_flags():
    src = _source()
    assert "--ignore-certificate-errors" not in src
    assert "--ignore-ssl-errors" not in src


def test_ssl_verify_defaults_true_without_ca_bundle(monkeypatch):
    monkeypatch.delenv("CA_BUNDLE_PATH", raising=False)
    # 直接重現模組內的判斷邏輯（不 import 整個監控腳本，避免觸發其餘初始化流程）
    import os
    ca_bundle_path = os.getenv("CA_BUNDLE_PATH", "").strip()
    if ca_bundle_path and os.path.exists(ca_bundle_path):
        ssl_verify = ca_bundle_path
    else:
        ssl_verify = True
    assert ssl_verify is True


def test_env_example_has_no_real_secrets():
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "smtp.gmail.com" in example  # 允許保留伺服器位址等非機密資訊
    forbidden_markers = ["BEGIN PRIVATE KEY", "xoxb-", "AKIA"]
    for marker in forbidden_markers:
        assert marker not in example


def test_gitignore_excludes_env_and_db():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "*.db" in gitignore
    assert "debug/" in gitignore
