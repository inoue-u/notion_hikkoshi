"""MarkdownをNotion APIのブロックオブジェクトに変換する"""

from __future__ import annotations

import re
from typing import Any

import mistune

# Notion APIのリッチテキスト文字数上限
RICH_TEXT_MAX_LENGTH = 2000


def _split_text(text: str, max_len: int = RICH_TEXT_MAX_LENGTH) -> list[str]:
    """テキストを最大長で分割する"""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 単語境界で分割を試みる
        split_pos = text.rfind(" ", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    return chunks


def _make_rich_text(
    text: str,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    code: bool = False,
    link: str | None = None,
) -> list[dict[str, Any]]:
    """Notion APIのrich_textオブジェクトを生成する"""
    if not text:
        return []

    annotations = {}
    if bold:
        annotations["bold"] = True
    if italic:
        annotations["italic"] = True
    if strikethrough:
        annotations["strikethrough"] = True
    if code:
        annotations["code"] = True

    result = []
    for chunk in _split_text(text):
        rt: dict[str, Any] = {
            "type": "text",
            "text": {"content": chunk},
        }
        if link:
            rt["text"]["link"] = {"url": link}
        if annotations:
            rt["annotations"] = annotations
        result.append(rt)
    return result


def _inline_to_rich_text(tokens: list[dict]) -> list[dict[str, Any]]:
    """mistuneのインライントークン列をrich_textに変換する"""
    result: list[dict[str, Any]] = []

    for token in tokens:
        ttype = token.get("type", "")
        children = token.get("children")

        if ttype == "text":
            result.extend(_make_rich_text(token.get("raw", token.get("text", ""))))
        elif ttype == "codespan":
            result.extend(_make_rich_text(token.get("raw", token.get("text", "")), code=True))
        elif ttype == "strong":
            if children:
                for rt in _inline_to_rich_text(children):
                    rt.setdefault("annotations", {})["bold"] = True
                    result.append(rt)
            else:
                result.extend(_make_rich_text(token.get("raw", ""), bold=True))
        elif ttype == "emphasis":
            if children:
                for rt in _inline_to_rich_text(children):
                    rt.setdefault("annotations", {})["italic"] = True
                    result.append(rt)
            else:
                result.extend(_make_rich_text(token.get("raw", ""), italic=True))
        elif ttype == "strikethrough":
            if children:
                for rt in _inline_to_rich_text(children):
                    rt.setdefault("annotations", {})["strikethrough"] = True
                    result.append(rt)
            else:
                result.extend(_make_rich_text(token.get("raw", ""), strikethrough=True))
        elif ttype == "link":
            link_url = token.get("link", token.get("attrs", {}).get("url", ""))
            if children:
                link_text = "".join(_extract_text(children))
            else:
                link_text = token.get("text", link_url)
            result.extend(_make_rich_text(link_text, link=link_url))
        elif ttype == "softbreak" or ttype == "linebreak":
            result.extend(_make_rich_text("\n"))
        else:
            # フォールバック: rawテキストをそのまま使用
            raw = token.get("raw", token.get("text", ""))
            if raw:
                result.extend(_make_rich_text(raw))

    return result


def _extract_text(tokens: list[dict]) -> list[str]:
    """トークン列からプレーンテキストを抽出する"""
    texts = []
    for token in tokens:
        if "raw" in token:
            texts.append(token["raw"])
        elif "text" in token:
            texts.append(token["text"])
        elif "children" in token and token["children"]:
            texts.extend(_extract_text(token["children"]))
    return texts


class NotionBlockRenderer:
    """mistune ASTからNotionブロックのリストを生成する"""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []

    def render_tokens(self, tokens: list[dict]) -> list[dict[str, Any]]:
        """トークン列をNotionブロックに変換する"""
        self.blocks = []
        for token in tokens:
            self._render_token(token)
        return self.blocks

    def _render_token(self, token: dict) -> None:
        ttype = token.get("type", "")

        if ttype == "paragraph":
            self._render_paragraph(token)
        elif ttype == "heading":
            self._render_heading(token)
        elif ttype == "list":
            self._render_list(token)
        elif ttype == "block_code":
            self._render_code_block(token)
        elif ttype == "block_quote":
            self._render_blockquote(token)
        elif ttype == "thematic_break":
            self.blocks.append({"type": "divider", "divider": {}})
        elif ttype == "block_html":
            # HTMLブロックはそのままコードブロックとして表示
            raw = token.get("raw", token.get("text", ""))
            if raw.strip():
                self.blocks.append({
                    "type": "code",
                    "code": {
                        "rich_text": _make_rich_text(raw),
                        "language": "html",
                    },
                })
        elif ttype == "table":
            self._render_table(token)
        elif ttype == "image":
            self._render_image(token)

    def _render_paragraph(self, token: dict) -> None:
        children = token.get("children", [])

        # 画像のみのパラグラフを検出
        if len(children) == 1 and children[0].get("type") == "image":
            self._render_image(children[0])
            return

        rich_text = _inline_to_rich_text(children)
        if rich_text:
            self.blocks.append({
                "type": "paragraph",
                "paragraph": {"rich_text": rich_text},
            })

    def _render_heading(self, token: dict) -> None:
        children = token.get("children", [])
        level = token.get("attrs", {}).get("level", 1)
        rich_text = _inline_to_rich_text(children)

        # Notionはheading_1, heading_2, heading_3のみ対応
        level = min(level, 3)
        block_type = f"heading_{level}"

        self.blocks.append({
            "type": block_type,
            block_type: {"rich_text": rich_text},
        })

    def _render_list(self, token: dict) -> None:
        ordered = token.get("attrs", {}).get("ordered", False)
        items = token.get("children", [])

        for item in items:
            if item.get("type") != "list_item":
                continue
            self._render_list_item(item, ordered)

    def _render_list_item(self, token: dict, ordered: bool) -> None:
        children = token.get("children", [])

        # To-doリストの検出
        text_content = "".join(_extract_text(children))
        todo_match = re.match(r"^\[([ xX])\]\s*", text_content)

        if todo_match:
            checked = todo_match.group(1).lower() == "x"
            # チェックボックスのプレフィックスを除去した内容を取得
            rich_text = self._get_list_item_rich_text(children, strip_todo=True)
            self.blocks.append({
                "type": "to_do",
                "to_do": {
                    "rich_text": rich_text,
                    "checked": checked,
                },
            })
            return

        rich_text = self._get_list_item_rich_text(children)
        block_type = "numbered_list_item" if ordered else "bulleted_list_item"

        block: dict[str, Any] = {
            "type": block_type,
            block_type: {"rich_text": rich_text},
        }

        # ネストされたリストを子ブロックとして追加
        nested_blocks = self._get_nested_list_blocks(children)
        if nested_blocks:
            block[block_type]["children"] = nested_blocks

        self.blocks.append(block)

    def _get_list_item_rich_text(
        self, children: list[dict], strip_todo: bool = False
    ) -> list[dict[str, Any]]:
        """リストアイテムからrich_textを抽出する"""
        rich_text: list[dict[str, Any]] = []
        for child in children:
            if child.get("type") == "paragraph":
                rt = _inline_to_rich_text(child.get("children", []))
                rich_text.extend(rt)
                break  # 最初のパラグラフのみ

        if strip_todo and rich_text:
            # "[ ] " or "[x] " プレフィックスを除去
            first = rich_text[0]
            if first.get("type") == "text":
                content = first["text"]["content"]
                content = re.sub(r"^\[([ xX])\]\s*", "", content)
                first["text"]["content"] = content
                if not content:
                    rich_text = rich_text[1:]

        return rich_text

    def _get_nested_list_blocks(self, children: list[dict]) -> list[dict[str, Any]]:
        """ネストされたリストをブロックとして返す"""
        nested: list[dict[str, Any]] = []
        for child in children:
            if child.get("type") == "list":
                renderer = NotionBlockRenderer()
                renderer._render_list(child)
                nested.extend(renderer.blocks)
        return nested

    def _render_code_block(self, token: dict) -> None:
        raw = token.get("raw", token.get("text", ""))
        language = token.get("attrs", {}).get("info", "plain text") or "plain text"

        # Notionが対応する言語名にマッピング
        language = _normalize_language(language)

        self.blocks.append({
            "type": "code",
            "code": {
                "rich_text": _make_rich_text(raw.rstrip("\n")),
                "language": language,
            },
        })

    def _render_blockquote(self, token: dict) -> None:
        children = token.get("children", [])

        # コールアウト検出: 最初の文字が絵文字の場合
        text_content = "".join(_extract_text(children))
        emoji_match = re.match(
            r"^([\U0001f300-\U0001f9ff\U00002600-\U000027bf\u2700-\u27bf])\s*",
            text_content,
        )

        if emoji_match:
            icon = emoji_match.group(1)
            # コールアウトブロックとして生成
            rich_text = _inline_to_rich_text(
                children[0].get("children", []) if children else []
            )
            # 絵文字プレフィックスを除去
            if rich_text:
                first = rich_text[0]
                if first.get("type") == "text":
                    content = first["text"]["content"]
                    content = re.sub(
                        r"^[\U0001f300-\U0001f9ff\U00002600-\U000027bf\u2700-\u27bf]\s*",
                        "",
                        content,
                    )
                    first["text"]["content"] = content

            self.blocks.append({
                "type": "callout",
                "callout": {
                    "rich_text": rich_text,
                    "icon": {"type": "emoji", "emoji": icon},
                },
            })
            return

        # 通常の引用
        renderer = NotionBlockRenderer()
        rich_text_parts: list[dict[str, Any]] = []
        for child in children:
            if child.get("type") == "paragraph":
                rich_text_parts.extend(
                    _inline_to_rich_text(child.get("children", []))
                )

        self.blocks.append({
            "type": "quote",
            "quote": {"rich_text": rich_text_parts},
        })

    def _render_table(self, token: dict) -> None:
        children = token.get("children", [])
        if not children:
            return

        rows: list[list[list[dict[str, Any]]]] = []
        for row_token in children:
            if row_token.get("type") not in ("table_head", "table_body"):
                continue
            row_children = row_token.get("children", [])
            for tr in row_children:
                if tr.get("type") != "table_row":
                    continue
                cells: list[list[dict[str, Any]]] = []
                for cell in tr.get("children", []):
                    cell_rt = _inline_to_rich_text(cell.get("children", []))
                    cells.append(cell_rt)
                rows.append(cells)

        if not rows:
            return

        table_width = max(len(row) for row in rows) if rows else 1

        table_rows = []
        for row in rows:
            # 各行のセル数をテーブル幅に揃える
            while len(row) < table_width:
                row.append([])
            table_rows.append({
                "type": "table_row",
                "table_row": {"cells": row},
            })

        self.blocks.append({
            "type": "table",
            "table": {
                "table_width": table_width,
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            },
        })

    def _render_image(self, token: dict) -> None:
        src = token.get("attrs", {}).get("src", token.get("src", ""))
        alt = token.get("attrs", {}).get("alt", token.get("alt", ""))

        if not src:
            return

        # 外部URL → image block
        if src.startswith(("http://", "https://")):
            self.blocks.append({
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": src},
                },
            })
            return

        # ローカルファイル → プレースホルダ (添付ファイルとして別途処理)
        import urllib.parse
        decoded = urllib.parse.unquote(src)
        display = alt or decoded
        self.blocks.append({
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": f"[画像: {display}]"},
                        "annotations": {"italic": True, "color": "gray"},
                    },
                ],
            },
        })


def _normalize_language(lang: str) -> str:
    """言語名をNotion APIが受け付ける形式に正規化する"""
    lang = lang.lower().strip()
    mapping = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "rb": "ruby",
        "sh": "shell",
        "bash": "shell",
        "zsh": "shell",
        "yml": "yaml",
        "md": "markdown",
        "": "plain text",
    }
    return mapping.get(lang, lang)


def markdown_to_blocks(md_text: str) -> list[dict[str, Any]]:
    """MarkdownテキストをNotionブロックのリストに変換する。

    Args:
        md_text: Markdownテキスト

    Returns:
        Notion APIブロックオブジェクトのリスト
    """
    md = mistune.create_markdown(renderer=None)
    tokens = md(md_text)

    renderer = NotionBlockRenderer()
    return renderer.render_tokens(tokens)
