"""attachment_resolver のテスト"""

import tempfile
from pathlib import Path

import pytest

from notion_hikkoshi.attachment_resolver import (
    attachments_to_blocks,
    extract_local_references,
    resolve_attachments,
)


class TestExtractLocalReferences:
    def test_markdown_image(self):
        md = "![screenshot](images/screenshot.png)"
        refs = extract_local_references(md)
        assert "images/screenshot.png" in refs

    def test_markdown_pdf_link(self):
        md = "[document](files/report.pdf)"
        refs = extract_local_references(md)
        assert "files/report.pdf" in refs

    def test_external_url_excluded(self):
        md = "![img](https://example.com/img.png)"
        refs = extract_local_references(md)
        assert len(refs) == 0

    def test_multiple_references(self):
        md = (
            "![a](img/a.png)\n"
            "![b](img/b.jpg)\n"
            "[pdf](doc/report.pdf)\n"
        )
        refs = extract_local_references(md)
        assert len(refs) == 3

    def test_html_img(self):
        md = '<img src="photos/test.jpg">'
        refs = extract_local_references(md)
        assert "photos/test.jpg" in refs

    def test_japanese_filename(self):
        md = "![テスト](画像/テスト画像.png)"
        refs = extract_local_references(md)
        assert "画像/テスト画像.png" in refs

    def test_url_encoded_path(self):
        md = "![img](%E7%94%BB%E5%83%8F/test.png)"
        refs = extract_local_references(md)
        assert "%E7%94%BB%E5%83%8F/test.png" in refs

    def test_no_duplicate(self):
        md = "![a](img.png)\n![b](img.png)"
        refs = extract_local_references(md)
        assert len(refs) == 1

    def test_anchor_excluded(self):
        md = "[section](#section-1)"
        refs = extract_local_references(md)
        assert len(refs) == 0


class TestResolveAttachments:
    def test_existing_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # 画像ファイルを作成
            img_dir = tmpdir / "images"
            img_dir.mkdir()
            img_file = img_dir / "photo.png"
            img_file.write_bytes(b"\x89PNG")

            # Markdownファイル
            md_file = tmpdir / "page.md"
            md_file.write_text("![photo](images/photo.png)")

            atts = resolve_attachments(md_file.read_text(), md_file)
            assert len(atts) == 1
            assert atts[0].exists is True
            assert atts[0].category == "image"
            assert atts[0].file_name == "photo.png"

    def test_existing_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pdf_file = tmpdir / "report.pdf"
            pdf_file.write_bytes(b"%PDF")

            md_file = tmpdir / "page.md"
            md_file.write_text("[report](report.pdf)")

            atts = resolve_attachments(md_file.read_text(), md_file)
            assert len(atts) == 1
            assert atts[0].exists is True
            assert atts[0].category == "pdf"

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            md_file = tmpdir / "page.md"
            md_file.write_text("![missing](no_such_file.png)")

            atts = resolve_attachments(md_file.read_text(), md_file)
            assert len(atts) == 1
            assert atts[0].exists is False
            assert atts[0].category == "image"

    def test_japanese_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            img_file = tmpdir / "テスト画像.png"
            img_file.write_bytes(b"\x89PNG")

            md_file = tmpdir / "page.md"
            md_file.write_text("![test](テスト画像.png)")

            atts = resolve_attachments(md_file.read_text(), md_file)
            assert len(atts) == 1
            assert atts[0].exists is True
            assert atts[0].file_name == "テスト画像.png"

    def test_subfolder_attachment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            sub = tmpdir / "sub" / "deep"
            sub.mkdir(parents=True)
            img_file = sub / "nested.jpg"
            img_file.write_bytes(b"\xff\xd8")

            md_file = tmpdir / "page.md"
            md_file.write_text("![img](sub/deep/nested.jpg)")

            atts = resolve_attachments(md_file.read_text(), md_file)
            assert len(atts) == 1
            assert atts[0].exists is True
            assert atts[0].category == "image"


class TestAttachmentsToBlocks:
    def test_existing_image_block(self):
        att = resolve_attachments.__wrapped__ if hasattr(resolve_attachments, '__wrapped__') else None
        from notion_hikkoshi.models import Attachment

        att = Attachment(
            file_path=Path("/tmp/img.png"),
            file_name="img.png",
            mime_type="image/png",
            category="image",
            referenced_from="img.png",
            exists=True,
        )
        blocks = attachments_to_blocks([att])
        # ヘッダ + 1ファイル
        assert len(blocks) == 2
        assert blocks[0]["type"] == "heading_3"
        assert "[画像]" in blocks[1]["paragraph"]["rich_text"][0]["text"]["content"]

    def test_missing_file_block(self):
        from notion_hikkoshi.models import Attachment

        att = Attachment(
            file_path=Path("/tmp/missing.pdf"),
            file_name="missing.pdf",
            mime_type="application/pdf",
            category="pdf",
            referenced_from="missing.pdf",
            exists=False,
        )
        blocks = attachments_to_blocks([att])
        assert len(blocks) == 2
        assert "[未検出]" in blocks[1]["paragraph"]["rich_text"][0]["text"]["content"]

    def test_empty_attachments(self):
        blocks = attachments_to_blocks([])
        assert len(blocks) == 0
