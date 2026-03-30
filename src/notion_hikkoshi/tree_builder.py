"""エクスポートのフォルダ構造から厳密な階層ツリーを再構築する。

source_path を唯一の識別子として使い、親子関係を正確に保つ。
_all.csv の重複検出も行う。
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from .node_registry import (
    Node,
    NodeRegistry,
    NodeType,
    normalize_path,
    strip_notion_uuid,
)

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
ASSET_EXTENSIONS = IMAGE_EXTENSIONS | {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".pptx", ".zip", ".txt",
}


def _count_csv_rows(csv_path: Path) -> int:
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)
            return sum(1 for _ in reader)
    except Exception:
        return 0


def _is_all_csv_duplicate(csv_name: str, sibling_csvs: set[str]) -> str | None:
    """_all.csv が対応する通常CSVの重複かどうかを判定する。

    例: "Tasks_all.csv" は "Tasks.csv" の重複。
    返値: 重複元のファイル名 or None
    """
    m = re.match(r"^(.+)_all\.csv$", csv_name, re.IGNORECASE)
    if m:
        base = m.group(1) + ".csv"
        if base in sibling_csvs:
            return base
    return None


def build_tree(root_dir: Path) -> NodeRegistry:
    """エクスポートディレクトリから NodeRegistry を構築する。

    ディレクトリ階層を正として、親子関係を厳密に再構築する。

    Notion エクスポートの構造:
      root/
        PageA <uuid>.md
        PageA <uuid>/           ← PageA の子要素を含むフォルダ
          SubpageB <uuid>.md
          Database <uuid>.csv
          image.png
    """
    root_dir = root_dir.resolve()
    registry = NodeRegistry(root_dir)
    logger.info("ツリー構築開始: %s", root_dir)

    _build_recursive(root_dir, root_dir, None, registry)

    summary = registry.summary()
    logger.info(
        "ツリー構築完了: %dページ, %dデータベース (うち重複CSV=%d)",
        summary["pages"],
        summary["databases"],
        summary["csv_duplicates"],
    )
    return registry


def _build_recursive(
    directory: Path,
    root_dir: Path,
    parent_node: Node | None,
    registry: NodeRegistry,
) -> None:
    """ディレクトリを再帰的に走査してノードを構築する"""
    if not directory.is_dir():
        return

    md_files = sorted(directory.glob("*.md"))
    csv_files = sorted(directory.glob("*.csv"))

    # CSV重複検出用のセット
    csv_names = {f.name for f in csv_files}

    # CSVファイル → データベースノード
    for csv_file in csv_files:
        rel_path = normalize_path(str(csv_file.relative_to(root_dir)))
        title = strip_notion_uuid(csv_file.name)

        # _all.csv 重複チェック
        dup_of = _is_all_csv_duplicate(csv_file.name, csv_names)
        if dup_of:
            logger.info(
                "CSV重複検出 (スキップ): %s は %s の _all.csv 版",
                csv_file.name, dup_of,
            )

        node = Node(
            source_path=rel_path,
            title=title,
            node_type=NodeType.DATABASE,
            file_path=csv_file,
            parent=parent_node,
            csv_duplicate_of=normalize_path(
                str((csv_file.parent / dup_of).relative_to(root_dir))
            ) if dup_of else None,
        )

        if parent_node:
            parent_node.children.append(node)
        registry.register(node)

        row_count = _count_csv_rows(csv_file)
        logger.debug(
            "データベース登録: source=%s, title=%r, parent=%s, rows=%d%s",
            rel_path,
            title,
            parent_node.source_path if parent_node else "ROOT",
            row_count,
            " (重複)" if dup_of else "",
        )

    # Markdownファイル → ページノード
    for md_file in md_files:
        rel_path = normalize_path(str(md_file.relative_to(root_dir)))
        title = strip_notion_uuid(md_file.name)

        node = Node(
            source_path=rel_path,
            title=title,
            node_type=NodeType.PAGE,
            file_path=md_file,
            parent=parent_node,
        )

        if parent_node:
            parent_node.children.append(node)
        registry.register(node)

        logger.debug(
            "ページ登録: source=%s, title=%r, parent=%s",
            rel_path,
            title,
            parent_node.source_path if parent_node else "ROOT",
        )

        # 対応する子フォルダ (ページ名と同じ stem のフォルダ)
        child_dir = directory / md_file.stem
        if child_dir.is_dir():
            _build_recursive(child_dir, root_dir, node, registry)
