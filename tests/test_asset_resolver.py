"""asset_resolver のテスト"""

import tempfile
from pathlib import Path

from notion_hikkoshi.asset_resolver import (
    make_asset_block,
    resolve_assets_for_node,
)
from notion_hikkoshi.node_registry import AssetRef, Node, NodeType


def _make_node(md_path: Path, title: str = "test") -> Node:
    return Node(
        source_path=str(md_path),
        title=title,
        node_type=NodeType.PAGE,
        file_path=md_path,
    )


class TestResolveAssets:
    def test_existing_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            img = tmpdir / "photo.png"
            img.write_bytes(b"\x89PNG")

            md_file = tmpdir / "page.md"
            md_file.write_text("![photo](photo.png)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].exists is True
            assert assets[0].category == "image"

    def test_existing_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pdf = tmpdir / "report.pdf"
            pdf.write_bytes(b"%PDF")

            md_file = tmpdir / "page.md"
            md_file.write_text("[report](report.pdf)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].category == "pdf"

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            md_file = tmpdir / "page.md"
            md_file.write_text("![missing](no_such_file.png)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].exists is False

    def test_japanese_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            img = tmpdir / "テスト画像.png"
            img.write_bytes(b"\x89PNG")

            md_file = tmpdir / "page.md"
            md_file.write_text("![test](テスト画像.png)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].exists is True

    def test_space_in_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            img = tmpdir / "image 1.png"
            img.write_bytes(b"\x89PNG")

            md_file = tmpdir / "page.md"
            md_file.write_text("![img](image%201.png)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].exists is True

    def test_subfolder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            sub = tmpdir / "sub"
            sub.mkdir()
            img = sub / "nested.jpg"
            img.write_bytes(b"\xff\xd8")

            md_file = tmpdir / "page.md"
            md_file.write_text("![img](sub/nested.jpg)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].exists is True
            assert assets[0].category == "image"

    def test_docx_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            doc = tmpdir / "doc.docx"
            doc.write_bytes(b"PK")

            md_file = tmpdir / "page.md"
            md_file.write_text("[document](doc.docx)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 1
            assert assets[0].category == "other"

    def test_md_links_excluded(self):
        """内部 .md リンクは添付ファイルとして検出されない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            md_file = tmpdir / "page.md"
            md_file.write_text("[other page](other.md)")
            node = _make_node(md_file)

            assets = resolve_assets_for_node(node, md_file.read_text())
            assert len(assets) == 0


class TestMakeAssetBlock:
    def test_existing_image(self):
        ref = AssetRef(
            referenced_from="img.png",
            resolved_path=Path("/tmp/img.png"),
            category="image",
            mime_type="image/png",
            exists=True,
        )
        block = make_asset_block(ref)
        assert block["type"] == "paragraph"
        assert "🖼" in block["paragraph"]["rich_text"][0]["text"]["content"]

    def test_missing_file(self):
        ref = AssetRef(
            referenced_from="gone.pdf",
            resolved_path=Path("/tmp/gone.pdf"),
            category="pdf",
            mime_type="application/pdf",
            exists=False,
        )
        block = make_asset_block(ref)
        assert "⚠" in block["paragraph"]["rich_text"][0]["text"]["content"]
