"""Notionページ書き込み: ブロック単位のエラーハンドリング付き"""

from __future__ import annotations

import logging
from typing import Any

from .notion_api import NotionImporter

logger = logging.getLogger(__name__)


def create_empty_page(
    api: NotionImporter,
    parent_id: str,
    title: str,
) -> str:
    """空のページを作成する (Pass 1: 構造作成)"""
    page_data: dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "properties": {
            "title": [{"type": "text", "text": {"content": title}}]
        },
    }
    result = api._retry_api_call(api.client.pages.create, **page_data)
    page_id = result["id"]
    logger.info("ページ作成 (空): %s (ID: %s)", title, page_id)
    return page_id


def write_page_content(
    api: NotionImporter,
    page_id: str,
    blocks: list[dict[str, Any]],
    title: str = "",
) -> tuple[int, int, list[str]]:
    """ページにブロックを書き込む (Pass 2: コンテンツ流し込み)。

    ブロック単位でエラーハンドリングし、失敗ブロックがあっても
    残りの書き込みを続行する。

    Returns:
        (success_count, fail_count, warnings)
    """
    if not blocks:
        return 0, 0, []

    success = 0
    failed = 0
    warnings: list[str] = []

    # 100ブロックずつバッチ処理
    BATCH = 100
    for i in range(0, len(blocks), BATCH):
        batch = blocks[i:i + BATCH]
        try:
            api._retry_api_call(
                api.client.blocks.children.append,
                block_id=page_id,
                children=batch,
            )
            success += len(batch)
        except Exception as e:
            # バッチ全体が失敗 → 1ブロックずつリトライ
            logger.warning(
                "バッチ書き込み失敗 (%s, batch %d-%d): %s → 個別リトライ",
                title, i, i + len(batch), e,
            )
            for j, block in enumerate(batch):
                try:
                    api._retry_api_call(
                        api.client.blocks.children.append,
                        block_id=page_id,
                        children=[block],
                    )
                    success += 1
                except Exception as e2:
                    failed += 1
                    block_type = block.get("type", "unknown")
                    warn = (
                        f"ブロック書き込み失敗 (page={title}, "
                        f"index={i + j}, type={block_type}): {e2}"
                    )
                    warnings.append(warn)
                    logger.warning(warn)

    return success, failed, warnings
