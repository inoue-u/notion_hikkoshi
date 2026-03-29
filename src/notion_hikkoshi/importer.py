"""インポートオーケストレーター"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rich.progress import Progress, SpinnerColumn, TextColumn

from .csv_parser import (
    columns_to_database_schema,
    infer_schema,
    read_csv_rows,
    row_to_page_properties,
)
from .markdown_parser import markdown_to_blocks
from .models import ExportedDatabase, ExportedPage, ExportTree
from .notion_api import NotionImporter

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """インポート結果のサマリー"""

    pages_created: int = 0
    databases_created: int = 0
    rows_created: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def import_tree(
    tree: ExportTree,
    api: NotionImporter,
    parent_page_id: str,
    dry_run: bool = False,
) -> ImportResult:
    """エクスポートツリーをNotionにインポートする。

    Args:
        tree: エクスポートツリー
        api: Notion APIクライアント
        parent_page_id: インポート先の親ページID
        dry_run: Trueの場合、実際のAPI呼び出しを行わない

    Returns:
        ImportResult: インポート結果
    """
    result = ImportResult()
    total = tree.page_count + tree.database_count

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[bold blue]{task.completed}/{task.total}"),
        transient=True,
    ) as progress:
        task = progress.add_task("インポート中...", total=total)

        for item in tree.root_children:
            _import_item(item, api, parent_page_id, dry_run, result, progress, task)

    return result


def _import_item(
    item: ExportedPage | ExportedDatabase,
    api: NotionImporter,
    parent_id: str,
    dry_run: bool,
    result: ImportResult,
    progress: Progress,
    task: Any,
) -> None:
    """アイテムを再帰的にインポートする"""
    if isinstance(item, ExportedPage):
        _import_page(item, api, parent_id, dry_run, result, progress, task)
    elif isinstance(item, ExportedDatabase):
        _import_database(item, api, parent_id, dry_run, result, progress, task)


def _import_page(
    page: ExportedPage,
    api: NotionImporter,
    parent_id: str,
    dry_run: bool,
    result: ImportResult,
    progress: Progress,
    task: Any,
) -> None:
    """ページをインポートする"""
    try:
        progress.update(task, description=f"ページ: {page.title[:40]}")

        # Markdownを読み込んでブロックに変換
        md_text = page.markdown_path.read_text(encoding="utf-8")
        blocks = markdown_to_blocks(md_text)

        if dry_run:
            logger.info("[DRY RUN] ページ作成: %s (%dブロック)", page.title, len(blocks))
            page_id = "dry-run-id"
        else:
            page_id = api.create_page(parent_id, page.title, blocks)

        page.notion_id = page_id
        result.pages_created += 1
        progress.advance(task)

        # 子アイテムを再帰的にインポート
        for child in page.children:
            _import_item(
                child,
                api,
                page_id if not dry_run else parent_id,
                dry_run,
                result,
                progress,
                task,
            )

    except Exception as e:
        error_msg = f"ページ '{page.title}' のインポートに失敗: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)
        progress.advance(task)


def _import_database(
    db: ExportedDatabase,
    api: NotionImporter,
    parent_id: str,
    dry_run: bool,
    result: ImportResult,
    progress: Progress,
    task: Any,
) -> None:
    """データベースをインポートする"""
    try:
        progress.update(task, description=f"DB: {db.title[:40]}")

        # スキーマを推定
        columns = infer_schema(db.csv_path)
        schema = columns_to_database_schema(columns)

        if dry_run:
            logger.info(
                "[DRY RUN] データベース作成: %s (%dカラム, %d行)",
                db.title,
                len(columns),
                db.row_count,
            )
            db_id = "dry-run-db-id"
        else:
            db_id = api.create_database(parent_id, db.title, schema)

        db.notion_id = db_id
        result.databases_created += 1

        # 行を追加
        rows = read_csv_rows(db.csv_path)
        for row in rows:
            try:
                properties = row_to_page_properties(row, columns)
                if dry_run:
                    logger.debug("[DRY RUN] 行追加: %s", list(row.values())[:2])
                else:
                    api.create_database_row(db_id, properties)
                result.rows_created += 1
            except Exception as e:
                error_msg = f"DB '{db.title}' の行追加に失敗: {e}"
                logger.warning(error_msg)
                result.errors.append(error_msg)

        progress.advance(task)

    except Exception as e:
        error_msg = f"データベース '{db.title}' のインポートに失敗: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)
        progress.advance(task)
