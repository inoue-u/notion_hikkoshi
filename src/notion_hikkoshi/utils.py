"""ユーティリティ関数"""

from __future__ import annotations

import logging
import re


def strip_notion_uuid(filename: str) -> str:
    """ファイル名からNotionが付与するUUIDサフィックスを除去する。

    例: "My Page a1b2c3d4e5f67890abcdef1234567890.md" -> "My Page"
        "My Page a1b2c3d4e5f67890abcdef1234567890" -> "My Page"
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    # Notion appends a space + 32-char hex UUID
    cleaned = re.sub(r"\s+[0-9a-f]{32}$", "", stem)
    return cleaned.strip() if cleaned else stem.strip()


def setup_logging(verbose: bool = False) -> None:
    """ロギングを設定する"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
