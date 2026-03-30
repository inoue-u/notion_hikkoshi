"""Notionデータベース書き込み: スキーマ作成・行投入"""

from __future__ import annotations

import logging
from typing import Any

from .csv_parser import DatabaseSchema, infer_schema, read_csv_rows
from .node_registry import Node
from .notion_api import NotionImporter

logger = logging.getLogger(__name__)


def create_empty_database(
    api: NotionImporter,
    parent_id: str,
    title: str,
    schema: DatabaseSchema,
) -> str:
    """空のデータベースを作成する (Pass 1: 構造作成)"""
    notion_schema = schema.to_notion_schema()
    db_data: dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": notion_schema,
    }
    result = api._retry_api_call(api.client.databases.create, **db_data)
    db_id = result["id"]
    logger.info(
        "データベース作成: %s (ID: %s, title列=%r, %d列)",
        title, db_id, schema.title_column, len(schema.columns),
    )
    return db_id


def insert_database_rows(
    api: NotionImporter,
    db_node: Node,
    schema: DatabaseSchema,
) -> tuple[int, int, list[str]]:
    """データベースに行を投入する (Pass 2: データ流し込み)。

    Returns:
        (success_count, fail_count, warnings)
    """
    rows = read_csv_rows(db_node.file_path)
    success = 0
    failed = 0
    warnings: list[str] = []

    logger.info(
        "DB行投入開始: %s (%d行, DB_ID=%s)",
        db_node.title, len(rows), db_node.notion_id,
    )

    for row_idx, row in enumerate(rows):
        try:
            properties = schema.row_to_properties(row)
            api._retry_api_call(
                api.client.pages.create,
                parent={"type": "database_id", "database_id": db_node.notion_id},
                properties=properties,
            )
            success += 1
        except Exception as e:
            failed += 1
            warn = (
                f"DB '{db_node.title}' 行#{row_idx + 1} 追加失敗: {e}\n"
                f"  送信プロパティ名: {list(properties.keys()) if 'properties' in dir() else '?'}\n"
                f"  DB列名: {schema.normalized_names}"
            )
            warnings.append(warn)
            logger.warning(warn)

    logger.info(
        "DB行投入完了: %s (成功=%d, 失敗=%d)",
        db_node.title, success, failed,
    )
    return success, failed, warnings
