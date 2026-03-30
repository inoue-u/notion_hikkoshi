"""データモデル定義 (後方互換用のエイリアスを含む)"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ColumnDef:
    """データベースのカラム定義"""

    name: str
    inferred_type: str  # "title", "rich_text", "number", "date", "url", "checkbox"
