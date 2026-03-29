"""Notion APIラッパー"""

from __future__ import annotations

import logging
import time
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)

# Notion APIの制限
BLOCKS_PER_REQUEST = 100
REQUEST_INTERVAL = 0.35  # 秒（レートリミット対策）
MAX_RETRIES = 3


class NotionImporter:
    """Notion APIを使ってページやデータベースを作成するラッパー"""

    def __init__(self, token: str) -> None:
        self.client = Client(auth=token)
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        """レートリミットを守るためにリクエスト間隔を制御する"""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _retry_api_call(self, func, *args, **kwargs) -> Any:
        """API呼び出しをリトライ付きで実行する"""
        for attempt in range(MAX_RETRIES):
            try:
                self._throttle()
                return func(*args, **kwargs)
            except APIResponseError as e:
                if e.status == 429:
                    # レートリミット: 指数バックオフ
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "レートリミット到達。%d秒後にリトライ (試行 %d/%d)",
                        wait,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(wait)
                elif attempt < MAX_RETRIES - 1 and e.status >= 500:
                    # サーバーエラー: リトライ
                    wait = 2 ** attempt
                    logger.warning(
                        "サーバーエラー (%d)。%d秒後にリトライ", e.status, wait
                    )
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"API呼び出しが{MAX_RETRIES}回失敗しました")

    def create_page(
        self,
        parent_id: str,
        title: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> str:
        """ページを作成する。

        Args:
            parent_id: 親ページのID
            title: ページタイトル
            blocks: ページコンテンツのブロックリスト

        Returns:
            作成されたページのID
        """
        page_data: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_id},
            "properties": {
                "title": [{"type": "text", "text": {"content": title}}]
            },
        }

        # 最初の100ブロックまではページ作成時に含める
        if blocks:
            page_data["children"] = blocks[:BLOCKS_PER_REQUEST]

        result = self._retry_api_call(self.client.pages.create, **page_data)
        page_id = result["id"]
        logger.info("ページ作成: %s (ID: %s)", title, page_id)

        # 残りのブロックを追加
        if blocks and len(blocks) > BLOCKS_PER_REQUEST:
            self.append_blocks(page_id, blocks[BLOCKS_PER_REQUEST:])

        return page_id

    def append_blocks(
        self, page_id: str, blocks: list[dict[str, Any]]
    ) -> None:
        """ページにブロックを追加する（100ブロックずつバッチ処理）"""
        for i in range(0, len(blocks), BLOCKS_PER_REQUEST):
            batch = blocks[i : i + BLOCKS_PER_REQUEST]
            self._retry_api_call(
                self.client.blocks.children.append,
                block_id=page_id,
                children=batch,
            )
            logger.debug(
                "ブロック追加: %d/%d (ページ %s)",
                min(i + BLOCKS_PER_REQUEST, len(blocks)),
                len(blocks),
                page_id,
            )

    def create_database(
        self,
        parent_id: str,
        title: str,
        schema: dict[str, Any],
    ) -> str:
        """データベースを作成する。

        Args:
            parent_id: 親ページのID
            title: データベースタイトル
            schema: プロパティスキーマ

        Returns:
            作成されたデータベースのID
        """
        db_data: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": schema,
        }

        result = self._retry_api_call(self.client.databases.create, **db_data)
        db_id = result["id"]
        logger.info("データベース作成: %s (ID: %s)", title, db_id)
        return db_id

    def create_database_row(
        self,
        database_id: str,
        properties: dict[str, Any],
    ) -> str:
        """データベースに行を追加する。

        Args:
            database_id: データベースのID
            properties: 行のプロパティ

        Returns:
            作成されたページ（行）のID
        """
        page_data: dict[str, Any] = {
            "parent": {"type": "database_id", "database_id": database_id},
            "properties": properties,
        }

        result = self._retry_api_call(self.client.pages.create, **page_data)
        return result["id"]
