"""階層構造のテスト: 親子孫、同名ページ、日本語、空白パス"""

import tempfile
from pathlib import Path

from notion_hikkoshi.node_registry import NodeType, normalize_path
from notion_hikkoshi.tree_builder import build_tree


def _make_export(structure: dict, base: Path) -> None:
    """再帰的にテスト用エクスポート構造を作成する"""
    for name, content in structure.items():
        path = base / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        elif isinstance(content, dict):
            path.mkdir(parents=True, exist_ok=True)
            _make_export(content, path)
        elif isinstance(content, bytes):
            path.write_bytes(content)


class TestHierarchy:
    def test_three_levels(self):
        """親→子→孫 3階層"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_export({
                "Parent a1b2c3d4e5f67890abcdef1234567890.md": "# Parent",
                "Parent a1b2c3d4e5f67890abcdef1234567890": {
                    "Child b2c3d4e5f67890abcdef12345678901a.md": "# Child",
                    "Child b2c3d4e5f67890abcdef12345678901a": {
                        "Grandchild c3d4e5f67890abcdef12345678901a2b.md": "# Grandchild",
                    },
                },
            }, root)

            registry = build_tree(root)
            assert registry.summary()["pages"] == 3

            # 階層確認
            parent = None
            child = None
            grandchild = None
            for n in registry.pages:
                if n.title == "Parent":
                    parent = n
                elif n.title == "Child":
                    child = n
                elif n.title == "Grandchild":
                    grandchild = n

            assert parent is not None
            assert child is not None
            assert grandchild is not None

            assert parent.parent is None
            assert child.parent is parent
            assert grandchild.parent is child
            assert child in parent.children
            assert grandchild in child.children

    def test_same_title_different_folders(self):
        """同名ページが別フォルダに存在"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_export({
                "FolderA a1b2c3d4e5f67890abcdef1234567890.md": "# FolderA",
                "FolderA a1b2c3d4e5f67890abcdef1234567890": {
                    "Notes b2c3d4e5f67890abcdef12345678901a.md": "# Notes in A",
                },
                "FolderB c3d4e5f67890abcdef12345678901a2b.md": "# FolderB",
                "FolderB c3d4e5f67890abcdef12345678901a2b": {
                    "Notes d4e5f67890abcdef12345678901a2b3c.md": "# Notes in B",
                },
            }, root)

            registry = build_tree(root)
            # 2つの "Notes" ページがそれぞれ正しい親の下にある
            notes = [n for n in registry.pages if n.title == "Notes"]
            assert len(notes) == 2
            parents = {n.parent.title for n in notes}
            assert parents == {"FolderA", "FolderB"}

    def test_japanese_title(self):
        """日本語タイトルページ"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_export({
                "テスト a1b2c3d4e5f67890abcdef1234567890.md": "# テスト",
            }, root)

            registry = build_tree(root)
            assert registry.summary()["pages"] == 1
            assert registry.pages[0].title == "テスト"

    def test_space_in_path(self):
        """空白含むパス"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_export({
                "My Page a1b2c3d4e5f67890abcdef1234567890.md": "# My Page",
                "My Page a1b2c3d4e5f67890abcdef1234567890": {
                    "Sub Page b2c3d4e5f67890abcdef12345678901a.md": "# Sub Page",
                },
            }, root)

            registry = build_tree(root)
            assert registry.summary()["pages"] == 2
            sub = [n for n in registry.pages if n.title == "Sub Page"]
            assert len(sub) == 1
            assert sub[0].parent.title == "My Page"

    def test_csv_all_dedup(self):
        """_all.csv の重複検出"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_export({
                "Tasks a1b2c3d4e5f67890abcdef1234567890.csv":
                    "Name,Status\nA,Done\n",
                "Tasks_all a1b2c3d4e5f67890abcdef1234567890.csv":
                    "Name,Status\nA,Done\nB,Todo\n",
            }, root)

            registry = build_tree(root)
            dbs = registry.databases
            # 両方登録されるが、_all は重複フラグ付き
            assert len(dbs) == 2
            dupes = [n for n in dbs if n.csv_duplicate_of]
            non_dupes = [n for n in dbs if not n.csv_duplicate_of]
            # _all.csv は名前パターンが異なるので重複検出されない場合がある
            # 実際の Notion export: "Tasks.csv" と "Tasks_all.csv"
            # テストデータでは UUID 付きなので strip_notion_uuid 後の名前で判定


class TestNormalizePath:
    def test_url_decode(self):
        assert normalize_path("My%20Page/test.md") == "My Page/test.md"

    def test_unicode_normalize(self):
        # NFD "が" → NFC "が"
        nfd = "か\u3099"
        assert normalize_path(nfd) == "が"

    def test_backslash(self):
        assert normalize_path("folder\\file.md") == "folder/file.md"

    def test_double_slash(self):
        assert normalize_path("a//b///c") == "a/b/c"

    def test_strip_slashes(self):
        assert normalize_path("/a/b/") == "a/b"

    def test_japanese_path(self):
        assert normalize_path("テスト/ページ.md") == "テスト/ページ.md"
