"""CSVファイルをNotionデータベースのスキーマとページプロパティに変換する

列名の正規化を一元管理し、DB作成時と行追加時で同一の列名を保証する。
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ColumnDef

logger = logging.getLogger(__name__)


def normalize_header(raw: str) -> str:
    """CSVヘッダを正規化する。

    - UTF-8 BOM 除去
    - 制御文字除去
    - 前後空白除去
    """
    # BOM除去
    cleaned = raw.lstrip("\ufeff")
    # 制御文字除去 (改行・タブ等)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    # 前後空白除去
    cleaned = cleaned.strip()
    return cleaned


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    patterns = [
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
        r"^\d{4}/\d{2}/\d{2}$",
    ]
    return any(re.match(p, value) for p in patterns)


def _is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value))


def _is_checkbox(value: str) -> bool:
    return value.lower() in {"true", "false", "yes", "no", "0", "1"}


def infer_column_type(values: list[str]) -> str:
    """値のサンプルからカラムの型を推定する。"""
    non_empty = [v.strip() for v in values if v.strip()]
    if not non_empty:
        return "rich_text"

    if all(_is_checkbox(v) for v in non_empty):
        return "checkbox"
    if all(_is_number(v) for v in non_empty):
        return "number"
    if all(_is_date(v) for v in non_empty):
        return "date"
    if all(_is_url(v) for v in non_empty):
        return "url"

    return "rich_text"


@dataclass
class DatabaseSchema:
    """データベーススキーマ: 列定義と正規化マッピングを一元管理する。

    DB作成時と行追加時で同一のオブジェクトを使い、プロパティ名の不一致を防ぐ。
    """

    columns: list[ColumnDef] = field(default_factory=list)
    # CSVの生ヘッダ → 正規化後の列名
    raw_to_normalized: dict[str, str] = field(default_factory=dict)

    @property
    def title_column(self) -> str | None:
        """titleとして採用した列名を返す"""
        for col in self.columns:
            if col.inferred_type == "title":
                return col.name
        return None

    @property
    def normalized_names(self) -> list[str]:
        """正規化後の列名リスト"""
        return [col.name for col in self.columns]

    def to_notion_schema(self) -> dict[str, Any]:
        """Notion APIのデータベーススキーマに変換する"""
        properties: dict[str, Any] = {}
        for col in self.columns:
            if col.inferred_type == "title":
                properties[col.name] = {"title": {}}
            elif col.inferred_type == "rich_text":
                properties[col.name] = {"rich_text": {}}
            elif col.inferred_type == "number":
                properties[col.name] = {"number": {"format": "number"}}
            elif col.inferred_type == "date":
                properties[col.name] = {"date": {}}
            elif col.inferred_type == "url":
                properties[col.name] = {"url": {}}
            elif col.inferred_type == "checkbox":
                properties[col.name] = {"checkbox": {}}
            else:
                properties[col.name] = {"rich_text": {}}
        return properties

    def row_to_properties(self, raw_row: dict[str, str]) -> dict[str, Any]:
        """CSV行をNotion APIのページプロパティに変換する。

        raw_rowのキーはCSVの生ヘッダ。正規化マッピングを使って
        DB作成時と同一の列名でプロパティを生成する。
        """
        properties: dict[str, Any] = {}
        col_by_name = {col.name: col for col in self.columns}

        for raw_key, value in raw_row.items():
            normalized_key = self.raw_to_normalized.get(raw_key)
            if normalized_key is None:
                # 未知のキーは正規化を試みる
                normalized_key = normalize_header(raw_key)

            col = col_by_name.get(normalized_key)
            if col is None:
                logger.warning(
                    "不明な列を検出 (スキップせず rich_text として処理): "
                    "生ヘッダ=%r → 正規化=%r, DB列名一覧=%r",
                    raw_key,
                    normalized_key,
                    self.normalized_names,
                )
                # graceful degradation: rich_text として追加を試みる
                # ただしDB側に列が無い場合APIエラーになるので、スキップ
                continue

            value = (value or "").strip()
            properties[col.name] = _value_to_property(col, value)

        return properties


def _value_to_property(col: ColumnDef, value: str) -> dict[str, Any]:
    """カラム定義と値からNotion APIプロパティを生成する"""
    if col.inferred_type == "title":
        return {"title": [{"type": "text", "text": {"content": value}}]}
    elif col.inferred_type == "rich_text":
        return {"rich_text": [{"type": "text", "text": {"content": value}}]}
    elif col.inferred_type == "number":
        try:
            return {"number": float(value)} if value else {"number": None}
        except ValueError:
            logger.warning("数値変換失敗: 列=%s, 値=%r", col.name, value)
            return {"number": None}
    elif col.inferred_type == "date":
        if value:
            date_str = value.replace("/", "-")[:10]
            return {"date": {"start": date_str}}
        return {"date": None}
    elif col.inferred_type == "url":
        return {"url": value if value else None}
    elif col.inferred_type == "checkbox":
        return {"checkbox": value.lower() in {"true", "yes", "1"}}
    else:
        return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def infer_schema(csv_path: Path) -> DatabaseSchema:
    """CSVファイルからスキーマを推定する。

    BOM除去・空白除去済みの正規化列名を使い、
    生ヘッダ→正規化名のマッピングも保持する。
    """
    logger.info("CSVスキーマ推定開始: %s", csv_path.name)

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return DatabaseSchema()

        raw_headers = list(reader.fieldnames)
        # 正規化マッピングを構築
        raw_to_normalized: dict[str, str] = {}
        normalized_headers: list[str] = []
        for raw_h in raw_headers:
            norm = normalize_header(raw_h)
            raw_to_normalized[raw_h] = norm
            normalized_headers.append(norm)
            if raw_h != norm:
                logger.info("列名正規化: %r → %r", raw_h, norm)

        # 各カラムの値を収集（最大100行サンプリング）
        column_values: dict[str, list[str]] = {h: [] for h in raw_headers}
        for i, row in enumerate(reader):
            if i >= 100:
                break
            for h in raw_headers:
                column_values[h].append(row.get(h, ""))

    # 最初のカラムを title として扱う
    columns = [ColumnDef(name=normalized_headers[0], inferred_type="title")]
    logger.info("title列として採用: %r", normalized_headers[0])

    for raw_h, norm_h in zip(raw_headers[1:], normalized_headers[1:]):
        col_type = infer_column_type(column_values[raw_h])
        columns.append(ColumnDef(name=norm_h, inferred_type=col_type))
        logger.debug("列推定: %r → 型=%s", norm_h, col_type)

    schema = DatabaseSchema(
        columns=columns,
        raw_to_normalized=raw_to_normalized,
    )
    logger.info(
        "スキーマ推定完了: %d列, title=%r",
        len(columns),
        schema.title_column,
    )
    return schema


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """CSVファイルの全行を読み込む (BOM対応)"""
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


# --- 後方互換性のためのエイリアス ---

def columns_to_database_schema(columns: list[ColumnDef]) -> dict[str, Any]:
    """カラム定義リストをNotion APIのデータベーススキーマに変換する (後方互換)"""
    properties: dict[str, Any] = {}
    for col in columns:
        if col.inferred_type == "title":
            properties[col.name] = {"title": {}}
        elif col.inferred_type == "rich_text":
            properties[col.name] = {"rich_text": {}}
        elif col.inferred_type == "number":
            properties[col.name] = {"number": {"format": "number"}}
        elif col.inferred_type == "date":
            properties[col.name] = {"date": {}}
        elif col.inferred_type == "url":
            properties[col.name] = {"url": {}}
        elif col.inferred_type == "checkbox":
            properties[col.name] = {"checkbox": {}}
        else:
            properties[col.name] = {"rich_text": {}}
    return properties


def row_to_page_properties(
    row: dict[str, str], columns: list[ColumnDef]
) -> dict[str, Any]:
    """CSV行をNotion APIのページプロパティに変換する (後方互換)"""
    properties: dict[str, Any] = {}
    for col in columns:
        value = row.get(col.name, "").strip()
        properties[col.name] = _value_to_property(col, value)
    return properties
