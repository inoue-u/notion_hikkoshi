"""csv_parser のテスト"""

from pathlib import Path
import tempfile

from notion_hikkoshi.csv_parser import (
    DatabaseSchema,
    infer_schema,
    normalize_header,
    read_csv_rows,
)


class TestNormalizeHeader:
    def test_bom_removal(self):
        assert normalize_header("\ufeffName") == "Name"

    def test_leading_trailing_whitespace(self):
        assert normalize_header("  Name  ") == "Name"

    def test_control_chars(self):
        assert normalize_header("Name\n\r\t") == "Name"

    def test_bom_plus_whitespace(self):
        assert normalize_header("\ufeff  Name  ") == "Name"

    def test_japanese_header(self):
        assert normalize_header("\ufeffタスク名") == "タスク名"

    def test_empty_string(self):
        assert normalize_header("") == ""

    def test_normal_string(self):
        assert normalize_header("Status") == "Status"


def _write_csv(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return Path(f.name)


class TestInferSchema:
    def test_bom_name_header(self):
        path = _write_csv("\ufeffName,Status\nAlice,Active\n")
        schema = infer_schema(path)
        assert schema.title_column == "Name"
        assert schema.columns[0].name == "Name"
        assert schema.columns[0].inferred_type == "title"

    def test_whitespace_headers(self):
        path = _write_csv("  Name  , Status ,  URL  \nA,B,https://x.com\n")
        schema = infer_schema(path)
        assert schema.columns[0].name == "Name"
        assert schema.columns[1].name == "Status"
        assert schema.columns[2].name == "URL"

    def test_japanese_headers(self):
        path = _write_csv("タスク名,ステータス,期限\nタスクA,完了,2024-01-15\n")
        schema = infer_schema(path)
        assert schema.title_column == "タスク名"
        assert schema.columns[1].name == "ステータス"

    def test_created_tags_url_columns(self):
        path = _write_csv(
            "Name,Created,Tags,URL\n"
            "A,2024-01-01,tag1,https://example.com\n"
            "B,2024-02-01,tag2,https://example.com/b\n"
        )
        schema = infer_schema(path)
        col_map = {c.name: c.inferred_type for c in schema.columns}
        assert col_map["Name"] == "title"
        assert col_map["Created"] == "date"
        assert col_map["URL"] == "url"

    def test_checkbox_column(self):
        path = _write_csv("Name,Done\nA,true\nB,false\n")
        schema = infer_schema(path)
        col_map = {c.name: c.inferred_type for c in schema.columns}
        assert col_map["Done"] == "checkbox"

    def test_number_column(self):
        path = _write_csv("Name,Score\nA,95.5\nB,87\n")
        schema = infer_schema(path)
        col_map = {c.name: c.inferred_type for c in schema.columns}
        assert col_map["Score"] == "number"

    def test_date_column(self):
        path = _write_csv("Name,Due\nA,2024-01-15\nB,2024-02-20\n")
        schema = infer_schema(path)
        col_map = {c.name: c.inferred_type for c in schema.columns}
        assert col_map["Due"] == "date"

    def test_title_not_named_name(self):
        path = _write_csv("タイトル,カテゴリ\nテスト,A\n")
        schema = infer_schema(path)
        assert schema.title_column == "タイトル"

    def test_名前_as_title(self):
        path = _write_csv("名前,年齢\n太郎,20\n花子,25\n")
        schema = infer_schema(path)
        assert schema.title_column == "名前"


class TestDatabaseSchemaConsistency:
    def test_bom_consistency(self):
        path = _write_csv("\ufeffName,Status,URL\nAlice,Active,https://x.com\n")
        schema = infer_schema(path)
        notion_schema = schema.to_notion_schema()
        rows = read_csv_rows(path)
        for row in rows:
            props = schema.row_to_properties(row)
            for prop_name in props:
                assert prop_name in notion_schema

    def test_whitespace_consistency(self):
        path = _write_csv("  Name  , Tags \nA,tag1\n")
        schema = infer_schema(path)
        notion_schema = schema.to_notion_schema()
        rows = read_csv_rows(path)
        for row in rows:
            props = schema.row_to_properties(row)
            for prop_name in props:
                assert prop_name in notion_schema

    def test_japanese_consistency(self):
        path = _write_csv("タスク名,期限\nタスクA,2024-01-01\n")
        schema = infer_schema(path)
        notion_schema = schema.to_notion_schema()
        rows = read_csv_rows(path)
        for row in rows:
            props = schema.row_to_properties(row)
            for prop_name in props:
                assert prop_name in notion_schema

    def test_title_property_value_format(self):
        path = _write_csv("\ufeffName,Status\nAlice,Active\n")
        schema = infer_schema(path)
        rows = read_csv_rows(path)
        props = schema.row_to_properties(rows[0])
        title_prop = props[schema.title_column]
        assert "title" in title_prop
        assert title_prop["title"][0]["text"]["content"] == "Alice"
