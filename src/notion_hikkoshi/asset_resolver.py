"""添付ファイル解決: Markdown内のローカルファイル参照を検出・解決し、
本文中の出現位置に対応する形でブロック化する。
"""

from __future__ import annotations

import logging
import mimetypes
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from .node_registry import AssetRef, Node

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
PDF_EXTENSIONS = {".pdf"}

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")


def _classify(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    return "other"


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def resolve_assets_for_node(
    node: Node,
    md_text: str,
) -> list[AssetRef]:
    """ノードのMarkdown本文内の添付ファイル参照を解決する。

    画像 ![alt](path) と ファイルリンク [text](path.ext) を検出し、
    ファイルの存在確認・MIME判定を行う。
    """
    md_dir = node.file_path.parent
    refs: list[AssetRef] = []
    seen: set[str] = set()

    # 画像参照: ![alt](path)
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", md_text):
        raw_path = m.group(2).strip()
        if raw_path.startswith(("http://", "https://")):
            continue
        if raw_path in seen:
            continue
        seen.add(raw_path)

        ref = _resolve_one(raw_path, md_dir)
        refs.append(ref)

    # ファイルリンク: [text](path.ext) — .md は除外
    for m in re.finditer(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)", md_text):
        raw_path = m.group(2).strip()
        if raw_path.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if raw_path in seen:
            continue

        decoded = urllib.parse.unquote(raw_path)
        decoded = unicodedata.normalize("NFC", decoded)
        ext = Path(decoded).suffix.lower()

        # .md は内部リンク (link_resolver が処理)
        if ext == ".md":
            continue
        # 拡張子のないものも内部リンク候補なのでスキップ
        if ext == "":
            continue

        seen.add(raw_path)
        ref = _resolve_one(raw_path, md_dir)
        refs.append(ref)

    # HTML img
    for m in re.finditer(r'<img\s+[^>]*src=["\']([^"\']+)["\']', md_text, re.IGNORECASE):
        raw_path = m.group(1).strip()
        if raw_path.startswith(("http://", "https://")):
            continue
        if raw_path in seen:
            continue
        seen.add(raw_path)
        ref = _resolve_one(raw_path, md_dir)
        refs.append(ref)

    for ref in refs:
        if ref.exists:
            logger.info(
                "添付ファイル検出: %s (カテゴリ=%s, MIME=%s, パス=%s)",
                ref.resolved_path.name if ref.resolved_path else "?",
                ref.category,
                ref.mime_type,
                ref.resolved_path,
            )
        else:
            logger.warning(
                "添付ファイル未解決: %r → %s (ファイルが存在しません)",
                ref.referenced_from,
                ref.resolved_path,
            )

    return refs


def _resolve_one(raw_path: str, md_dir: Path) -> AssetRef:
    """1つの参照パスを解決する"""
    decoded = urllib.parse.unquote(raw_path)
    decoded = unicodedata.normalize("NFC", decoded)
    resolved = (md_dir / decoded).resolve()
    exists = resolved.is_file()
    ext = resolved.suffix.lower()

    return AssetRef(
        referenced_from=raw_path,
        resolved_path=resolved,
        category=_classify(ext),
        mime_type=_guess_mime(resolved) if exists else _guess_mime(Path(decoded)),
        exists=exists,
    )


def make_asset_block(ref: AssetRef) -> dict[str, Any]:
    """添付ファイル参照を本文中の出現位置に挿入するNotionブロックを生成する。

    Notion APIはローカルファイルアップロードに非対応なので、
    プレースホルダブロックを生成する。
    """
    fname = ref.resolved_path.name if ref.resolved_path else ref.referenced_from

    if not ref.exists:
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"⚠ 添付ファイル未検出: {fname} ({ref.referenced_from})"},
                    "annotations": {"italic": True, "color": "red"},
                }],
            },
        }

    if ref.category == "image":
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"🖼 {fname}"},
                    "annotations": {"bold": True},
                }, {
                    "type": "text",
                    "text": {"content": f" (ローカル画像 — 手動アップロードが必要)"},
                    "annotations": {"italic": True, "color": "gray"},
                }],
            },
        }
    elif ref.category == "pdf":
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"📄 {fname}"},
                    "annotations": {"bold": True},
                }, {
                    "type": "text",
                    "text": {"content": f" (PDF — 手動アップロードが必要)"},
                    "annotations": {"italic": True, "color": "gray"},
                }],
            },
        }
    else:
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"📎 {fname} ({ref.mime_type})"},
                }, {
                    "type": "text",
                    "text": {"content": f" (手動アップロードが必要)"},
                    "annotations": {"italic": True, "color": "gray"},
                }],
            },
        }
