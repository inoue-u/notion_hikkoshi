"""CSVファイルをNotionデータベースのスキーマとページプロパティに変換する"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ColumnDef


def _is_number(value: str) -> bool:
    """文字列が数値として解析可能か判定する"""
    try:
        float(value)
        return True
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    """文字列がISO日付形式か判定する"""
    patterns = [
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
        r"^\d{4}/\d{2}/\d{2}$",
    ]
    return any(re.match(p, value) for p in patterns)


def _is_url(value: str) -> bool:
    """文字列がURLか判定する"""
    return bool(re.match(r"^https?://", value))


def _is_checkbox(value: str) -> bool:
    """文字列がチェックボックス値か判定する"""
    return value.lower() in {"true", "false", "yes", "no", "0", "1"}


def infer_column_type(values: list[str]) -> str:
    """値のサンプルからカラムの型を推定する。

    空でない値を対象に判定する。
    """
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


def infer_schema(csv_path: Path) -> list[ColumnDef]:
    """CSVファイルからスキーマを推定する。

    最初のカラムは常にtitle型として扱う。
    他のカラムは値を分析して型を推定する。
    """
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []

        headers = list(reader.fieldnames)
        # 各カラムの値を収集（最大100行サンプリング）
        column_values: dict[str, list[str]] = {h: [] for h in headers}
        for i, row in enumerate(reader):
            if i >= 100:
                break
            for h in headers:
                column_values[h].append(row.get(h, ""))

    columns = [ColumnDef(name=headers[0], inferred_type="title")]
    for h in headers[1:]:
        col_type = infer_column_type(column_values[h])
        columns.append(ColumnDef(name=h, inferred_type=col_type))

    return columns


def columns_to_database_schema(columns: list[ColumnDef]) -> dict[str, Any]:
    """カラム定義リストをNotion APIのデータベーススキーマに変換する"""
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
    """CSV行をNotion APIのページプロパティに変換する"""
    properties: dict[str, Any] = {}

    for col in columns:
        value = row.get(col.name, "").strip()

        if col.inferred_type == "title":
            properties[col.name] = {
                "title": [{"type": "text", "text": {"content": value}}]
            }
        elif col.inferred_type == "rich_text":
            properties[col.name] = {
                "rich_text": [{"type": "text", "text": {"content": value}}]
            }
        elif col.inferred_type == "number":
            try:
                properties[col.name] = {"number": float(value)}
            except ValueError:
                properties[col.name] = {"number": None}
        elif col.inferred_type == "date":
            if value:
                # YYYY/MM/DD → YYYY-MM-DD に変換
                date_str = value.replace("/", "-")
                # 日付部分のみ抽出
                date_str = date_str[:10]
                properties[col.name] = {"date": {"start": date_str}}
            else:
                properties[col.name] = {"date": None}
        elif col.inferred_type == "url":
            properties[col.name] = {"url": value if value else None}
        elif col.inferred_type == "checkbox":
            properties[col.name] = {
                "checkbox": value.lower() in {"true", "yes", "1"}
            }

    return properties


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """CSVファイルの全行を読み込む"""
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)
