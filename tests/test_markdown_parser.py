"""markdown_parser のテスト: 見出し、リスト、コード、URL フォールバック等"""

from notion_hikkoshi.markdown_parser import markdown_to_blocks, _is_valid_url


class TestIsValidUrl:
    def test_valid_https(self):
        assert _is_valid_url("https://example.com") is True

    def test_valid_http(self):
        assert _is_valid_url("http://example.com/path") is True

    def test_invalid_relative(self):
        assert _is_valid_url("../page.md") is False

    def test_invalid_local(self):
        assert _is_valid_url("image.png") is False

    def test_invalid_empty(self):
        assert _is_valid_url("") is False

    def test_invalid_mailto(self):
        assert _is_valid_url("mailto:a@b.com") is False

    def test_broken_url(self):
        assert _is_valid_url("not a url at all") is False


class TestHeadings:
    def test_h1(self):
        blocks, _ = markdown_to_blocks("# Heading 1")
        assert blocks[0]["type"] == "heading_1"

    def test_h2(self):
        blocks, _ = markdown_to_blocks("## Heading 2")
        assert blocks[0]["type"] == "heading_2"

    def test_h3(self):
        blocks, _ = markdown_to_blocks("### Heading 3")
        assert blocks[0]["type"] == "heading_3"


class TestLists:
    def test_bullet_list(self):
        blocks, _ = markdown_to_blocks("- item 1\n- item 2\n")
        assert all(b["type"] == "bulleted_list_item" for b in blocks)

    def test_numbered_list(self):
        blocks, _ = markdown_to_blocks("1. first\n2. second\n")
        assert all(b["type"] == "numbered_list_item" for b in blocks)


class TestBlockquote:
    def test_simple_quote(self):
        blocks, _ = markdown_to_blocks("> This is a quote\n")
        assert blocks[0]["type"] == "quote"


class TestCodeBlock:
    def test_code_block(self):
        blocks, _ = markdown_to_blocks("```python\nprint('hello')\n```\n")
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "python"


class TestLinks:
    def test_valid_external_link(self):
        blocks, _ = markdown_to_blocks("[Google](https://google.com)")
        rt = blocks[0]["paragraph"]["rich_text"]
        assert rt[0]["text"]["link"]["url"] == "https://google.com"

    def test_invalid_url_fallback(self):
        """壊れたURLはplain textにフォールバック"""
        blocks, _ = markdown_to_blocks("[broken](not a valid url)")
        rt = blocks[0]["paragraph"]["rich_text"]
        # リンクなしのテキストになるべき
        assert "link" not in rt[0]["text"] or rt[0]["text"]["link"] is None or rt[0]["text"].get("link") is None


class TestImages:
    def test_external_image(self):
        blocks, _ = markdown_to_blocks("![alt](https://example.com/img.png)")
        assert blocks[0]["type"] == "image"

    def test_local_image_marker(self):
        """ローカル画像は __local_asset__ マーカーになる"""
        blocks, _ = markdown_to_blocks("![photo](images/photo.png)")
        assert blocks[0]["type"] == "__local_asset__"
        assert blocks[0]["__local_asset__"]["src"] == "images/photo.png"

    def test_pdf_link(self):
        """PDFリンクはテキストリンクとして処理される"""
        blocks, _ = markdown_to_blocks("[report](docs/report.pdf)")
        # report.pdf はローカルリンクだが .md ではないので通常リンクとして処理
        assert len(blocks) > 0


class TestRobustness:
    def test_empty_text(self):
        blocks, warnings = markdown_to_blocks("")
        assert blocks == []
        assert warnings == []

    def test_plain_text(self):
        blocks, _ = markdown_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"

    def test_mixed_content(self):
        """見出し + リスト + コード の複合"""
        md = "# Title\n\n- item\n\n```\ncode\n```\n"
        blocks, _ = markdown_to_blocks(md)
        types = [b["type"] for b in blocks]
        assert "heading_1" in types
        assert "bulleted_list_item" in types
        assert "code" in types
