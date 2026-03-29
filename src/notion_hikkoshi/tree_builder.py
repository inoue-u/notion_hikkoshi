"""エクスポートのフォルダ構造からページ階層を再構築する"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .models import ColumnDef, ExportedDatabase, ExportedPage, ExportTree
from .utils import strip_notion_uuid

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


def _count_csv_rows(csv_path: Path) -> int:
    """CSVファイルの行数をカウントする（ヘッダー除く）"""
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # ヘッダーをスキップ
            return sum(1 for _ in reader)
    except Exception:
        return 0


def _get_csv_columns(csv_path: Path) -> list[ColumnDef]:
    """CSVファイルからカラム定義を取得する（型推定あり）"""
    try:
        from .csv_parser import infer_schema

        return infer_schema(csv_path)
    except Exception:
        return []


def _find_child_folder(parent_dir: Path, md_file: Path) -> Path | None:
    """Markdownファイルに対応する子フォルダを探す。

    Notionのエクスポートでは、ページ "Page A <uuid>.md" に対応する
    子コンテンツは "Page A <uuid>/" フォルダに格納される。
    """
    folder_name = md_file.stem  # 拡張子を除いたファイル名
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

    # まずディレクトリ直下のファイルを収集
    md_files = sorted(directory.glob("*.md"))
    csv_files = sorted(directory.glob("*.csv"))

    # CSVファイル → データベースとして処理
    for csv_file in csv_files:
        title = strip_notion_uuid(csv_file.name)
        columns = _get_csv_columns(csv_file)
        row_count = _count_csv_rows(csv_file)
        db = ExportedDatabase(
            title=title,
            csv_path=csv_file,
            columns=columns,
            row_count=row_count,
        )
        items.append(db)
        logger.debug("データベース検出: %s (%d行)", title, row_count)

    # Markdownファイル → ページとして処理
    for md_file in md_files:
        title = strip_notion_uuid(md_file.name)
        page = ExportedPage(title=title, markdown_path=md_file)

        # 対応する子フォルダを探す
        child_dir = _find_child_folder(directory, md_file)
        if child_dir:
            # 画像ファイルを収集
            for f in child_dir.iterdir():
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                    page.images.append(f)

            # 子ページ・データベースを再帰的に構築
            page.children = _build_items(child_dir)

        items.append(page)
        logger.debug("ページ検出: %s (子: %d)", title, len(page.children))

    return items


def build_tree(root_dir: Path) -> ExportTree:
    """エクスポートディレクトリからページツリーを構築する。

    Args:
        root_dir: 展開されたエクスポートのルートディレクトリ

    Returns:
        ExportTree: ページ階層のツリー構造
    """
    logger.info("ツリー構築開始: %s", root_dir)
    tree = ExportTree(root_children=_build_items(root_dir))
    logger.info(
        "ツリー構築完了: %dページ, %dデータベース",
        tree.page_count,
        tree.database_count,
    )
    return tree
