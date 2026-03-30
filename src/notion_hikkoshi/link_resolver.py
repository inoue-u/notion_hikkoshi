"""内部リンク解決: Markdown内の .md リンクをNotionページ参照に変換する"""

from __future__ import annotations

import logging
import re
import urllib.parse
from pathlib import Path

from .node_registry import InternalLink, Node, NodeRegistry

logger = logging.getLogger(__name__)


def extract_internal_links(md_text: str) -> list[tuple[str, str, str]]:
    """Markdown本文から内部リンク (.md 参照) を抽出する。

    Returns:
        list of (full_match, link_text, link_target)
    """
    links: list[tuple[str, str, str]] = []

    # [text](target.md) or [text](path/to/target.md)
    for m in re.finditer(r'\[([^\]]*)\]\(([^)]+\.md(?:#[^)]*)?)\)', md_text):
        links.append((m.group(0), m.group(1), m.group(2)))

    # [text](path/to/folder) — Notionはフォルダ=ページなので .md なしもありうる
    # ただし外部URLや添付ファイルは除外
    for m in re.finditer(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)', md_text):
        target = m.group(2).strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        decoded = urllib.parse.unquote(target)
        # 拡張子なし or .md → 内部リンク候補
        ext = Path(decoded).suffix.lower()
        if ext == "" and not any(c in target for c in [".", "@"]):
            # 拡張子なしのローカルパス = ページへのリンク
            full = m.group(0)
            if full not in [l[0] for l in links]:
                links.append((full, m.group(1), target))

    return links


def resolve_links_for_node(
    node: Node,
    md_text: str,
    registry: NodeRegistry,
) -> list[InternalLink]:
    """ノードのMarkdown本文内の内部リンクを解決する。"""
    raw_links = extract_internal_links(md_text)
    results: list[InternalLink] = []

    for full_match, link_text, link_target in raw_links:
        resolved = registry.resolve_link(link_target, node)
        link = InternalLink(
            original_target=link_target,
            resolved_node=resolved,
        )

        if resolved:
            logger.debug(
                "内部リンク解決成功: %s → %s (node=%s)",
                link_target,
                resolved.source_path,
                node.source_path,
            )
        else:
            link.fallback = True
            logger.warning(
                "内部リンク未解決: %s (from=%s) → plain text にフォールバック",
                link_target,
                node.source_path,
            )

        results.append(link)

    return results


def rewrite_links_in_text(
    md_text: str,
    links: list[InternalLink],
) -> str:
    """Markdown本文内の内部リンクをNotionページURLに書き換える。

    解決済みリンクは Notion URL に、未解決はリンクテキストのみに変換。
    """
    result = md_text

    for link in links:
        if link.resolved_node and link.resolved_node.notion_id and link.notion_url:
            # Notion URL に書き換え
            old_pattern = re.escape(link.original_target)
            result = re.sub(
                r'\[([^\]]*)\]\(' + old_pattern + r'\)',
                lambda m, url=link.notion_url: f'[{m.group(1)}]({url})',
                result,
                count=1,
            )
        elif link.fallback:
            # リンクテキストのみにフォールバック
            old_pattern = re.escape(link.original_target)
            result = re.sub(
                r'\[([^\]]*)\]\(' + old_pattern + r'\)',
                lambda m: m.group(1),  # テキストのみ残す
                result,
                count=1,
            )

    return result
