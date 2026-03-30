"""ノードレジストリ: source_path を唯一の真実として全ノードを管理する"""

from __future__ import annotations

import logging
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)


class NodeType(Enum):
    PAGE = "page"
    DATABASE = "database"


def normalize_path(raw_path: str) -> str:
    """パスを正規化する。

    - URL decode
    - Unicode NFC 正規化 (macOS HFS+ 由来の NFD 差異対応)
    - パス区切り文字の統一 (→ /)
    - 連続スラッシュの除去
    - 先頭/末尾スラッシュの除去
    """
    decoded = urllib.parse.unquote(raw_path)
    normalized = unicodedata.normalize("NFC", decoded)
    normalized = normalized.replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized)
    normalized = normalized.strip("/")
    return normalized


def strip_notion_uuid(filename: str) -> str:
    """ファイル名からNotionが付与する32文字hexのUUIDサフィックスを除去する。"""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    cleaned = re.sub(r"\s+[0-9a-f]{32}$", "", stem)
    return cleaned.strip() if cleaned else stem.strip()


@dataclass
class Node:
    """エクスポート内の1つのページまたはデータベースを表す。

    source_path は展開ディレクトリからの相対パスで、唯一の識別子。
    """

    source_path: str  # 展開ルートからの相対パス (正規化済み)
    title: str
    node_type: NodeType
    file_path: Path  # 絶対パス (.md or .csv)
    parent: Node | None = field(default=None, repr=False)
    children: list[Node] = field(default_factory=list)
    notion_id: str | None = None

    # ページ固有
    attachments: list[AssetRef] = field(default_factory=list)
    internal_links: list[InternalLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # DB固有
    csv_duplicate_of: str | None = None  # _all.csv の場合、元CSVのsource_path

    @property
    def parent_source_path(self) -> str | None:
        return self.parent.source_path if self.parent else None

    @property
    def depth(self) -> int:
        d = 0
        n = self.parent
        while n:
            d += 1
            n = n.parent
        return d


@dataclass
class AssetRef:
    """Markdown内で参照された添付ファイル"""

    referenced_from: str  # Markdown内の生参照パス
    resolved_path: Path | None  # 解決後の絶対パス
    category: str  # "image", "pdf", "other"
    mime_type: str
    exists: bool
    block_index: int = -1  # 本文中の出現位置 (ブロック番号)
    saved: bool = False  # Notionに保存成功したか


@dataclass
class InternalLink:
    """Markdown内の内部ページリンク"""

    original_target: str  # Markdown内のリンク先
    resolved_node: Node | None = None  # 解決されたNode
    notion_url: str | None = None  # 最終的なNotionページURL
    fallback: bool = False  # plain textにフォールバックしたか


class NodeRegistry:
    """全Nodeを source_path で管理するレジストリ。

    パス正規化を一元的に行い、重複や曖昧さを排除する。
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self._nodes: dict[str, Node] = {}  # normalized source_path → Node
        # .md パスのマッピング (リンク解決用)
        self._md_path_map: dict[str, Node] = {}

    @property
    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    @property
    def pages(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.node_type == NodeType.PAGE]

    @property
    def databases(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.node_type == NodeType.DATABASE]

    @property
    def root_nodes(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.parent is None]

    def register(self, node: Node) -> None:
        """ノードを登録する"""
        key = normalize_path(node.source_path)
        if key in self._nodes:
            logger.warning(
                "重複ノード検出 (上書き): %s", key,
            )
        self._nodes[key] = node

        # .md パスマップに追加
        if node.node_type == NodeType.PAGE:
            self._md_path_map[key] = node
            # .md なしでもマッチするように
            if key.endswith(".md"):
                self._md_path_map[key[:-3]] = node

    def get(self, source_path: str) -> Node | None:
        """正規化パスでノードを検索する"""
        key = normalize_path(source_path)
        return self._nodes.get(key)

    def resolve_link(self, link_target: str, from_node: Node) -> Node | None:
        """内部リンクを解決する。

        link_target は Markdown 内のリンク先 (.md ファイルへの相対パス)。
        from_node のファイル位置を基準に相対パスを解決する。
        """
        decoded = urllib.parse.unquote(link_target)
        decoded = unicodedata.normalize("NFC", decoded)

        # from_node のディレクトリ基準で相対パスを解決
        from_dir = from_node.file_path.parent
        resolved_abs = (from_dir / decoded).resolve()

        # root_dir からの相対パスに変換
        try:
            rel = resolved_abs.relative_to(self.root_dir)
            key = normalize_path(str(rel))
        except ValueError:
            key = normalize_path(decoded)

        # 直接マッチ
        node = self._md_path_map.get(key)
        if node:
            return node

        # ファイル名のみでのフォールバックマッチ
        target_name = normalize_path(Path(decoded).name)
        for path_key, n in self._md_path_map.items():
            if path_key.endswith("/" + target_name) or path_key == target_name:
                logger.debug(
                    "リンク解決 (ファイル名マッチ): %r → %s",
                    link_target, path_key,
                )
                return n

        return None

    def to_relative_path(self, abs_path: Path) -> str:
        """絶対パスをルートからの相対パス文字列に変換する"""
        try:
            return normalize_path(str(abs_path.resolve().relative_to(self.root_dir)))
        except ValueError:
            return normalize_path(str(abs_path))

    def summary(self) -> dict[str, int]:
        """統計情報を返す"""
        pages = sum(1 for n in self._nodes.values() if n.node_type == NodeType.PAGE)
        dbs = sum(1 for n in self._nodes.values() if n.node_type == NodeType.DATABASE)
        dupes = sum(
            1 for n in self._nodes.values()
            if n.node_type == NodeType.DATABASE and n.csv_duplicate_of
        )
        return {"pages": pages, "databases": dbs, "csv_duplicates": dupes}
