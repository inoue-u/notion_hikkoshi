"""データモデル定義"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ColumnDef:
    """データベースのカラム定義"""

    name: str
    inferred_type: str  # "title", "rich_text", "number", "date", "url", "checkbox"


@dataclass
class ExportedDatabase:
    """エクスポートされたデータベース"""

    title: str
    csv_path: Path
    columns: list[ColumnDef] = field(default_factory=list)
    row_count: int = 0
    notion_id: str | None = None


@dataclass
class ExportedPage:
    """エクスポートされたページ"""

    title: str
    markdown_path: Path
    children: list[ExportedPage | ExportedDatabase] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    notion_id: str | None = None


@dataclass
class ExportTree:
    """エクスポート全体のツリー構造"""

    root_children: list[ExportedPage | ExportedDatabase] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        """全ページ数を再帰的にカウント"""
        count = 0
        stack = list(self.root_children)
        while stack:
            item = stack.pop()
            if isinstance(item, ExportedPage):
                count += 1
                stack.extend(item.children)
        return count

    @property
    def database_count(self) -> int:
        """全データベース数を再帰的にカウント"""
        count = 0
        stack = list(self.root_children)
        while stack:
            item = stack.pop()
            if isinstance(item, ExportedDatabase):
                count += 1
            elif isinstance(item, ExportedPage):
                stack.extend(item.children)
        return count
