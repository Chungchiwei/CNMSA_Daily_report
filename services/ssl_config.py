#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共用 SSL 驗證設定。預設一律驗證 HTTPS 憑證；若公司網路使用自訂 CA，
透過 CA_BUNDLE_PATH 環境變數指定 CA bundle 路徑。

先前 provincial.py、n8n_msa_monitor.py 各自維護一份幾乎相同的邏輯，
此處統一為單一來源，避免兩邊行為不同步（例如其中一邊忘記更新）。
"""

from __future__ import annotations

import os


def resolve_ssl_verify():
    """回傳可直接傳給 requests(verify=...) 的值：True 或 CA bundle 路徑字串。"""
    ca_bundle = os.getenv("CA_BUNDLE_PATH", "").strip()
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle
    return True
