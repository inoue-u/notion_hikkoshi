"""インポートオーケストレーター"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rich.progress import Progress, SpinnerColumn, TextColumn

from .attachment_resolver import attachments_to_blocks, resolve_attachments
from .csv_parser import infer_schema, read_csv_rows
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
    rows_failed: int = 0
    attachments_found: int = 0
    attachments_missing: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def import_tree(
    tree: ExportTree,
    api: NotionImporter,
    parent_page_id: str,
    dry_run: bool = False,
    include_assets: bool = True,
) -> ImportResult:
    """エクスポートツリーをNotionにインポートする。"""
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
            _import_item(
                item, api, parent_page_id, dry_run, include_assets,
                result, progress, task,
            )

    return result


def _import_item(
    item: ExportedPage | ExportedDatabase,
    api: NotionImporter,
    parent_id: str,
    dry_run: bool,
    include_assets: bool,
    result: ImportResult,
    progress: Progress,
    task: Any,
) -> None:
    if isinstance(item, ExportedPage):
        _import_page(item, api, parent_id, dry_run, include_assets, result, progress, task)
    elif isinstance(item, ExportedDatabase):
        _import_database(item, api, parent_id, dry_run, result, progress, task)


def _import_page(
    page: ExportedPage,
    api: NotionImporter,
    parent_id: str,
    dry_run: bool,
    include_assets: bool,
    result: ImportResult,
    progress: Progress,
    task: Any,
) -> None:
    try:
        progress.update(task, description=f"ページ: {page.title[:40]}")

        md_text = page.markdown_path.read_text(encoding="utf-8")
        blocks = markdown_to_blocks(md_text)

        # 添付ファイルブロックを追加
        if include_assets and page.attachments:
            existing = [a for a in page.attachments if a.exists]
            missing = [a for a in page.attachments if not a.exists]
            result.attachments_found += len(existing)
            result.attachments_missing += len(missing)

            att_blocks = attachments_to_blocks(page.attachments)
            blocks.extend(att_blocks)

            for a in existing:
                logger.info(
                    "添付ファイル処理: %s (カテゴリ=%s, パス=%s)",
                    a.file_name, a.category, a.file_path,
                )
            for a in missing:
                logger.warning(
                    "添付ファイル未検出: %s (参照=%s, 解決パス=%s)",
                    a.file_name, a.referenced_from, a.file_path,
                )

        if dry_run:
            att_count = len(page.attachments) if page.attachments else 0
            img_count = sum(1 for a in page.attachments if a.category == "image")
            pdf_count = sum(1 for a in page.attachments if a.category == "pdf")
            missing_count = sum(1 for a in page.attachments if not a.exists)
            logger.info(
                "[DRY RUN] ページ作成: %s (%dブロック, 添付=%d [画像=%d, PDF=%d], 未検出=%d)",
                page.title, len(blocks), att_count, img_count, pdf_count, missing_count,
            )
            page_id = "dry-run-id"
        else:
            page_id = api.create_page(parent_id, page.title, blocks)

        page.notion_id = page_id
        result.pages_created += 1
        progress.advance(task)

        for child in page.children:
            _import_item(
                child, api,
                page_id if not dry_run else parent_id,
                dry_run, include_assets, result, progress, task,
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
    try:
        progress.update(task, description=f"DB: {db.title[:40]}")
        logger.info("データベース処理開始: %s (CSV: %s)", db.title, db.csv_path.name)

        # スキーマを推定 (DatabaseSchemaオブジェクトで一元管理)
        schema = infer_schema(db.csv_path)
        notion_schema = schema.to_notion_schema()

        logger.info(
            "DBスキーマ確定: title列=%r, 全列=%r",
            schema.title_column,
            schema.normalized_names,
        )

        if dry_run:
            logger.info(
                "[DRY RUN] データベース作成: %s (%d列, %d行, title列=%r)",
                db.title, len(schema.columns), db.row_count, schema.title_column,
            )
            for col in schema.columns:
                logger.info(
                    "[DRY RUN]   列: %r → 型=%s%s",
                    col.name, col.inferred_type,
                    " (title)" if col.inferred_type == "title" else "",
                )
            db_id = "dry-run-db-id"
        else:
            db_id = api.create_database(parent_id, db.title, notion_schema)

        db.notion_id = db_id
        result.databases_created += 1

        # 行を追加 (DatabaseSchemaの統一メソッドでプロパティ生成)
        rows = read_csv_rows(db.csv_path)
        for row_idx, row in enumerate(rows):
            try:
                properties = schema.row_to_properties(row)
                if dry_run:
                    title_val = ""
                    if schema.title_column and schema.title_column in properties:
                        title_prop = properties[schema.title_column]
                        if "title" in title_prop and title_prop["title"]:
                            title_val = title_prop["title"][0]["text"]["content"]
                    logger.debug(
                        "[DRY RUN] 行追加 #%d: title=%r, 列数=%d",
                        row_idx + 1, title_val, len(properties),
                    )
                else:
                    api.create_database_row(db_id, properties)
                result.rows_created += 1
            except Exception as e:
                result.rows_failed += 1
                prop_names = list(properties.keys()) if 'properties' in dir() else []
                error_msg = (
                    f"DB '{db.title}' 行#{row_idx + 1} 追加失敗: {e}\n"
                    f"  送信プロパティ名: {prop_names}\n"
                    f"  DB列名: {schema.normalized_names}"
                )
                logger.warning(error_msg)
                result.errors.append(error_msg)

        progress.advance(task)

    except Exception as e:
        error_msg = f"データベース '{db.title}' のインポートに失敗: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)
        progress.advance(task)
