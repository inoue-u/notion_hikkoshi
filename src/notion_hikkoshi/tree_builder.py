"""エクスポートのフォルダ構造からページ階層を再構築する"""

from __future__ import annotations

import logging
from pathlib import Path

from .attachment_resolver import resolve_attachments
from .csv_parser import infer_schema
from .models import ExportedDatabase, ExportedPage, ExportTree
from .utils import strip_notion_uuid

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".pptx", ".zip", ".txt"}


def _count_csv_rows(csv_path: Path) -> int:
    """CSVファイルの行数をカウントする（ヘッダー除く）"""
    import csv
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)
            return sum(1 for _ in reader)
    except Exception:
        return 0


def _get_csv_schema(csv_path: Path):
    """CSVファイルからスキーマを取得する"""
    try:
        return infer_schema(csv_path)
    except Exception as e:
        logger.warning("CSVスキーマ推定失敗: %s: %s", csv_path.name, e)
        return None


def _find_child_folder(parent_dir: Path, md_file: Path) -> Path | None:
    """Markdownファイルに対応する子フォルダを探す。"""
    folder_name = md_file.stem
    child_dir = parent_dir / folder_name
    if child_dir.is_dir():
        return child_dir
    return None


def _build_items(
    directory: Path,
) -> list[ExportedPage | ExportedDatabase]:
    """ディレクトリ内のアイテムを再帰的に構築する"""
    items: list[ExportedPage | ExportedDatabase] = []

    if not directory.is_dir():
        return items

    md_files = sorted(directory.glob("*.md"))
    csv_files = sorted(directory.glob("*.csv"))

    # CSVファイル → データベース
    for csv_file in csv_files:
        title = strip_notion_uuid(csv_file.name)
        schema = _get_csv_schema(csv_file)
        columns = schema.columns if schema else []
        row_count = _count_csv_rows(csv_file)
        db = ExportedDatabase(
            title=title,
            csv_path=csv_file,
            columns=columns,
            row_count=row_count,
        )
        items.append(db)
        logger.debug("データベース検出: %s (%d行)", title, row_count)

    # Markdownファイル → ページ
    for md_file in md_files:
        title = strip_notion_uuid(md_file.name)
        page = ExportedPage(title=title, markdown_path=md_file)

        # Markdown本文から添付ファイルを解決
        try:
            md_text = md_file.read_text(encoding="utf-8")
            page.attachments = resolve_attachments(md_text, md_file)
        except Exception as e:
            logger.warning("添付ファイル解決失敗: %s: %s", md_file.name, e)

        # 対応する子フォルダ
        child_dir = _find_child_folder(directory, md_file)
        if child_dir:
            # 画像ファイルを収集
            for f in child_dir.iterdir():
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                    page.images.append(f)

            page.children = _build_items(child_dir)

        items.append(page)
        att_count = len(page.attachments)
        img_count = sum(1 for a in page.attachments if a.category == "image")
        pdf_count = sum(1 for a in page.attachments if a.category == "pdf")
        logger.debug(
            "ページ検出: %s (子=%d, 添付=%d [画像=%d, PDF=%d])",
            title, len(page.children), att_count, img_count, pdf_count,
        )

    return items


def build_tree(root_dir: Path) -> ExportTree:
    """エクスポートディレクトリからページツリーを構築する。"""
    logger.info("ツリー構築開始: %s", root_dir)
    tree = ExportTree(root_children=_build_items(root_dir))
    logger.info(
        "ツリー構築完了: %dページ, %dデータベース",
        tree.page_count,
        tree.database_count,
    )
    return tree
