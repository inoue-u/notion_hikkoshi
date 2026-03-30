"""MarkdownをNotion APIのブロックオブジェクトに非破壊的に変換する。

原則:
- 改行、箇条書き、見出し、引用、コードブロック、表記順を保持する
- リンクテキストとリンク先を勝手に変更しない
- Invalid URL は plain text にフォールバックする
- 変換不能な要素はログに記録する
- ブロック単位で try/except し、1つの失敗がページ全体を壊さない
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

import mistune

logger = logging.getLogger(__name__)

RICH_TEXT_MAX_LENGTH = 2000


def _is_valid_url(url: str) -> bool:
    """Notion APIが受け入れ可能なURLか判定する"""
    if not url:
        return False
    # Notion API は http/https のみ受け入れる
    if url.startswith(("http://", "https://")):
        try:
            parsed = urllib.parse.urlparse(url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False
    # mailto:, tel: 等も Notion は受け入れない
    return False


def _split_text(text: str, max_len: int = RICH_TEXT_MAX_LENGTH) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
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
    color: str | None = None,
) -> list[dict[str, Any]]:
    """Notion APIのrich_textオブジェクトを生成する。

    link が不正URLの場合は plain text にフォールバックする。
    """
    if not text:
        return []

    # URLバリデーション: 不正ならリンクなしにフォールバック
    effective_link = link
    if link and not _is_valid_url(link):
        logger.debug("Invalid URL をフォールバック: %r → plain text", link)
        effective_link = None

    annotations = {}
    if bold:
        annotations["bold"] = True
    if italic:
        annotations["italic"] = True
    if strikethrough:
        annotations["strikethrough"] = True
    if code:
        annotations["code"] = True
    if color:
        annotations["color"] = color

    result = []
    for chunk in _split_text(text):
        rt: dict[str, Any] = {
            "type": "text",
            "text": {"content": chunk},
        }
        if effective_link:
            rt["text"]["link"] = {"url": effective_link}
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

        try:
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
                # URL バリデーション付き rich_text
                result.extend(_make_rich_text(link_text, link=link_url))
            elif ttype == "softbreak" or ttype == "linebreak":
                result.extend(_make_rich_text("\n"))
            elif ttype == "image":
                # インライン画像はパラグラフ内では処理しない (上位で処理)
                attrs = token.get("attrs", {})
                src = attrs.get("src", attrs.get("url", token.get("src", "")))
                alt = attrs.get("alt", token.get("alt", ""))
                if not alt and token.get("children"):
                    alt = "".join(_extract_text(token["children"]))
                result.extend(_make_rich_text(f"[{alt or src}]", italic=True))
            else:
                raw = token.get("raw", token.get("text", ""))
                if raw:
                    result.extend(_make_rich_text(raw))
        except Exception as e:
            logger.warning("インライン要素変換失敗 (type=%s): %s", ttype, e)
            raw = token.get("raw", token.get("text", ""))
            if raw:
                result.extend(_make_rich_text(raw))

    return result


def _extract_text(tokens: list[dict]) -> list[str]:
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
    """mistune ASTからNotionブロックのリストを生成する。

    ブロック単位で try/except し、失敗要素はプレースホルダに変換する。
    """

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._degraded_count = 0

    def render_tokens(self, tokens: list[dict]) -> list[dict[str, Any]]:
        self.blocks = []
        self.warnings = []
        self._degraded_count = 0

        for token in tokens:
            try:
                self._render_token(token)
            except Exception as e:
                self._degraded_count += 1
                raw = token.get("raw", token.get("text", ""))
                ttype = token.get("type", "unknown")
                warning = f"ブロック変換失敗 (type={ttype}): {e}"
                self.warnings.append(warning)
                logger.warning(warning)

                # フォールバック: 生テキストをパラグラフとして保存
                if raw:
                    self.blocks.append({
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": _make_rich_text(
                                raw[:RICH_TEXT_MAX_LENGTH],
                                color="gray",
                            ),
                        },
                    })

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
            try:
                self._render_list_item(item, ordered)
            except Exception as e:
                logger.warning("リストアイテム変換失敗: %s", e)
                raw = "".join(_extract_text(item.get("children", [])))
                if raw:
                    self.blocks.append({
                        "type": "paragraph",
                        "paragraph": {"rich_text": _make_rich_text(raw)},
                    })

    def _render_list_item(self, token: dict, ordered: bool) -> None:
        children = token.get("children", [])
        text_content = "".join(_extract_text(children))
        todo_match = re.match(r"^\[([ xX])\]\s*", text_content)

        if todo_match:
            checked = todo_match.group(1).lower() == "x"
            rich_text = self._get_list_item_rich_text(children, strip_todo=True)
            self.blocks.append({
                "type": "to_do",
                "to_do": {"rich_text": rich_text, "checked": checked},
            })
            return

        rich_text = self._get_list_item_rich_text(children)
        block_type = "numbered_list_item" if ordered else "bulleted_list_item"
        block: dict[str, Any] = {
            "type": block_type,
            block_type: {"rich_text": rich_text},
        }
        nested_blocks = self._get_nested_list_blocks(children)
        if nested_blocks:
            block[block_type]["children"] = nested_blocks
        self.blocks.append(block)

    def _get_list_item_rich_text(
        self, children: list[dict], strip_todo: bool = False,
    ) -> list[dict[str, Any]]:
        rich_text: list[dict[str, Any]] = []
        for child in children:
            if child.get("type") == "paragraph":
                rt = _inline_to_rich_text(child.get("children", []))
                rich_text.extend(rt)
                break

        if strip_todo and rich_text:
            first = rich_text[0]
            if first.get("type") == "text":
                content = first["text"]["content"]
                content = re.sub(r"^\[([ xX])\]\s*", "", content)
                first["text"]["content"] = content
                if not content:
                    rich_text = rich_text[1:]

        return rich_text

    def _get_nested_list_blocks(self, children: list[dict]) -> list[dict[str, Any]]:
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
        text_content = "".join(_extract_text(children))

        emoji_match = re.match(
            r"^([\U0001f300-\U0001f9ff\U00002600-\U000027bf\u2700-\u27bf])\s*",
            text_content,
        )

        if emoji_match:
            icon = emoji_match.group(1)
            rich_text = _inline_to_rich_text(
                children[0].get("children", []) if children else []
            )
            if rich_text:
                first = rich_text[0]
                if first.get("type") == "text":
                    content = first["text"]["content"]
                    content = re.sub(
                        r"^[\U0001f300-\U0001f9ff\U00002600-\U000027bf\u2700-\u27bf]\s*",
                        "", content,
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
            for tr in row_token.get("children", []):
                if tr.get("type") != "table_row":
                    continue
                cells = [
                    _inline_to_rich_text(cell.get("children", []))
                    for cell in tr.get("children", [])
                ]
                rows.append(cells)

        if not rows:
            return

        table_width = max(len(row) for row in rows)
        table_rows = []
        for row in rows:
            while len(row) < table_width:
                row.append([])
            table_rows.append({"type": "table_row", "table_row": {"cells": row}})

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
        attrs = token.get("attrs", {})
        src = attrs.get("src", attrs.get("url", token.get("src", "")))
        alt = attrs.get("alt", token.get("alt", ""))
        # mistune v3 stores alt text in children
        if not alt and token.get("children"):
            alt = "".join(_extract_text(token["children"]))

        if not src:
            return

        if _is_valid_url(src):
            self.blocks.append({
                "type": "image",
                "image": {"type": "external", "external": {"url": src}},
            })
            return

        # ローカルファイル参照 → asset_resolver がインラインで処理する
        # ここではマーカーを残す (importer が差し替える)
        decoded = urllib.parse.unquote(src)
        self.blocks.append({
            "type": "__local_asset__",
            "__local_asset__": {
                "src": src,
                "alt": alt,
                "decoded": decoded,
            },
        })


def _normalize_language(lang: str) -> str:
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


def markdown_to_blocks(md_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """MarkdownテキストをNotionブロックのリストに変換する。

    Returns:
        (blocks, warnings) - ブロックリストと変換警告リスト
    """
    md = mistune.create_markdown(renderer=None)
    tokens = md(md_text)

    renderer = NotionBlockRenderer()
    blocks = renderer.render_tokens(tokens)
    return blocks, renderer.warnings
