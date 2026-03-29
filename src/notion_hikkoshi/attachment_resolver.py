"""Markdown内のローカルファイル参照を検出・解決し、添付ファイル情報を生成する"""

from __future__ import annotations

import logging
import mimetypes
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .models import Attachment

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
PDF_EXTENSIONS = {".pdf"}

# mimetypes が認識しない拡張子の補完
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")


def _classify(ext: str) -> str:
    """拡張子からカテゴリを判定する"""
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    return "other"


def _guess_mime(path: Path) -> str:
    """ファイルのMIMEタイプを推定する"""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def extract_local_references(md_text: str) -> list[str]:
    """Markdown本文からローカルファイル参照を抽出する。

    検出対象:
    - ![alt](relative/path.png)
    - [file.pdf](relative/path.pdf)
    - <img src="relative/path.png">
    """
    refs: list[str] = []

    # Markdown画像: ![alt](path)
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", md_text):
        path = m.group(2).strip()
        if not path.startswith(("http://", "https://", "#")):
            refs.append(path)

    # Markdownリンク: [text](path) — 画像でないもの
    for m in re.finditer(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)", md_text):
        path = m.group(2).strip()
        if not path.startswith(("http://", "https://", "#")):
            # ファイル拡張子があるもののみ (ページリンクを除外)
            decoded = urllib.parse.unquote(path)
            if "." in Path(decoded).name:
                refs.append(path)

    # HTML img: <img src="path">
    for m in re.finditer(r'<img\s+[^>]*src=["\']([^"\']+)["\']', md_text, re.IGNORECASE):
        path = m.group(1).strip()
        if not path.startswith(("http://", "https://")):
            refs.append(path)

    return list(dict.fromkeys(refs))  # 重複除去して順序保持


def resolve_attachments(
    md_text: str,
    md_file_path: Path,
) -> list[Attachment]:
    """Markdownから参照されるローカルファイルを解決し、Attachmentリストを返す。

    Args:
        md_text: Markdownテキスト
        md_file_path: Markdownファイルのパス (相対パス解決の基準)

    Returns:
        検出されたAttachmentのリスト
    """
    refs = extract_local_references(md_text)
    attachments: list[Attachment] = []
    md_dir = md_file_path.parent

    for ref in refs:
        decoded_ref = urllib.parse.unquote(ref)
        # Markdownファイルの親ディレクトリ基準で解決
        resolved = (md_dir / decoded_ref).resolve()

        exists = resolved.is_file()
        ext = resolved.suffix.lower()
        category = _classify(ext)
        mime = _guess_mime(resolved) if exists else _guess_mime(Path(decoded_ref))

        att = Attachment(
            file_path=resolved,
            file_name=resolved.name,
            mime_type=mime,
            category=category,
            referenced_from=ref,
            exists=exists,
        )
        attachments.append(att)

        if exists:
            logger.info(
                "添付ファイル検出: %s (カテゴリ=%s, MIME=%s)",
                att.file_name,
                category,
                mime,
            )
        else:
            logger.warning(
                "添付ファイル未検出: %s → 解決パス=%s (存在しません)",
                ref,
                resolved,
            )

    return attachments


def attachments_to_blocks(attachments: list[Attachment]) -> list[dict[str, Any]]:
    """Attachmentリストからページ末尾に追加するNotionブロックを生成する。

    - 画像: 可能ならimage block (外部URLが必要なため、ローカルの場合はプレースホルダ)
    - PDF: file block のプレースホルダ
    - その他: テキストで記載

    Notion APIは直接ファイルアップロードに対応していないため、
    ローカルファイルはプレースホルダブロックとして記載する。
    """
    if not attachments:
        return []

    blocks: list[dict[str, Any]] = []

    # セクションヘッダ
    blocks.append({
        "type": "heading_3",
        "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": "添付ファイル"}}],
        },
    })

    for att in attachments:
        if not att.exists:
            # 存在しないファイルは警告テキストのみ
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"[未検出] {att.file_name} ({att.referenced_from})"},
                        "annotations": {"italic": True, "color": "red"},
                    }],
                },
            })
            continue

        if att.category == "image":
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"[画像] {att.file_name}"},
                        "annotations": {"bold": True},
                    }, {
                        "type": "text",
                        "text": {"content": f" — ローカルパス: {att.file_path}"},
                        "annotations": {"italic": True, "color": "gray"},
                    }],
                },
            })
        elif att.category == "pdf":
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"[PDF] {att.file_name}"},
                        "annotations": {"bold": True},
                    }, {
                        "type": "text",
                        "text": {"content": f" — ローカルパス: {att.file_path}"},
                        "annotations": {"italic": True, "color": "gray"},
                    }],
                },
            })
        else:
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"[ファイル] {att.file_name} ({att.mime_type})"},
                    }, {
                        "type": "text",
                        "text": {"content": f" — ローカルパス: {att.file_path}"},
                        "annotations": {"italic": True, "color": "gray"},
                    }],
                },
            })

    return blocks
