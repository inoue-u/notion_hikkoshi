"""2-pass インポートオーケストレーター

Pass 1: 構造作成
  - 全ページ・DBの空作成 (親→子の順)
  - notion_id の確定

Pass 2: コンテンツ流し込み
  - 本文変換・書き込み
  - 添付ファイル処理
  - 内部リンク解決
  - DB行投入
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rich.progress import Progress, SpinnerColumn, TextColumn

from .asset_resolver import make_asset_block, resolve_assets_for_node
from .csv_parser import infer_schema
from .link_resolver import resolve_links_for_node, rewrite_links_in_text
from .markdown_parser import markdown_to_blocks
from .node_registry import AssetRef, Node, NodeRegistry, NodeType
from .notion_api import NotionImporter
from .notion_database_writer import create_empty_database, insert_database_rows
from .notion_page_writer import create_empty_page, write_page_content

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """インポート結果のサマリー"""

    pages_created: int = 0
    databases_created: int = 0
    rows_created: int = 0
    rows_failed: int = 0
    blocks_written: int = 0
    blocks_failed: int = 0
    attachments_detected: int = 0
    attachments_saved: int = 0
    attachments_failed: int = 0
    attachments_missing: int = 0
    links_resolved: int = 0
    links_unresolved: int = 0
    degraded_blocks: int = 0
    csv_duplicates_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def import_tree(
    registry: NodeRegistry,
    api: NotionImporter | None,
    parent_page_id: str,
    dry_run: bool = False,
    include_assets: bool = True,
) -> ImportResult:
    """2-pass インポートを実行する。"""
    result = ImportResult()
    nodes = registry.all_nodes
    total = len(nodes)

    # DB スキーマキャッシュ (source_path → schema)
    db_schemas: dict[str, Any] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[bold blue]{task.completed}/{task.total}"),
        transient=True,
    ) as progress:

        # === Pass 1: 構造作成 ===
        task1 = progress.add_task("Pass 1: 構造作成...", total=total)

        # BFS順 (親→子) で作成
        ordered = _bfs_order(registry)

        for node in ordered:
            progress.update(task1, description=f"Pass1: {node.title[:40]}")

            if node.node_type == NodeType.DATABASE:
                # 重複CSV はスキップ
                if node.csv_duplicate_of:
                    logger.info(
                        "CSV重複スキップ: %s (元: %s)",
                        node.source_path, node.csv_duplicate_of,
                    )
                    result.csv_duplicates_skipped += 1
                    progress.advance(task1)
                    continue

                schema = infer_schema(node.file_path)
                db_schemas[node.source_path] = schema

                if dry_run:
                    _dry_run_database(node, schema, result)
                    node.notion_id = "dry-run-db-id"
                else:
                    parent_nid = _get_parent_notion_id(node, parent_page_id)
                    node.notion_id = create_empty_database(
                        api, parent_nid, node.title, schema,
                    )
                result.databases_created += 1

            elif node.node_type == NodeType.PAGE:
                if dry_run:
                    node.notion_id = "dry-run-page-id"
                    logger.info(
                        "[DRY RUN] ページ作成: %s (parent=%s, depth=%d)",
                        node.source_path,
                        node.parent_source_path or "ROOT",
                        node.depth,
                    )
                else:
                    parent_nid = _get_parent_notion_id(node, parent_page_id)
                    node.notion_id = create_empty_page(api, parent_nid, node.title)
                result.pages_created += 1

            progress.advance(task1)

        # === Pass 2: コンテンツ流し込み ===
        task2 = progress.add_task("Pass 2: コンテンツ...", total=total)

        for node in ordered:
            progress.update(task2, description=f"Pass2: {node.title[:40]}")

            if node.csv_duplicate_of:
                progress.advance(task2)
                continue

            if node.node_type == NodeType.PAGE:
                _pass2_page(
                    node, registry, api, dry_run, include_assets, result,
                )
            elif node.node_type == NodeType.DATABASE:
                schema = db_schemas.get(node.source_path)
                if schema:
                    _pass2_database(node, schema, api, dry_run, result)

            progress.advance(task2)

    return result


def _bfs_order(registry: NodeRegistry) -> list[Node]:
    """BFS順 (親→子) でノードリストを返す"""
    ordered: list[Node] = []
    queue = list(registry.root_nodes)
    # 安定ソート (source_path順)
    queue.sort(key=lambda n: n.source_path)

    while queue:
        node = queue.pop(0)
        ordered.append(node)
        children = sorted(node.children, key=lambda n: n.source_path)
        queue.extend(children)

    return ordered


def _get_parent_notion_id(node: Node, root_parent_id: str) -> str:
    """ノードの親の notion_id を取得する"""
    if node.parent and node.parent.notion_id:
        return node.parent.notion_id
    return root_parent_id


def _pass2_page(
    node: Node,
    registry: NodeRegistry,
    api: NotionImporter | None,
    dry_run: bool,
    include_assets: bool,
    result: ImportResult,
) -> None:
    """Pass 2: ページのコンテンツ流し込み"""
    try:
        md_text = node.file_path.read_text(encoding="utf-8")

        # 1. 内部リンク解決
        links = resolve_links_for_node(node, md_text, registry)
        node.internal_links = links

        for lnk in links:
            if lnk.resolved_node and lnk.resolved_node.notion_id:
                nid = lnk.resolved_node.notion_id
                if not dry_run and nid != "dry-run-page-id":
                    lnk.notion_url = f"https://www.notion.so/{nid.replace('-', '')}"
                result.links_resolved += 1
            else:
                result.links_unresolved += 1

        # リンク書き換え
        md_text = rewrite_links_in_text(md_text, links)

        # 2. 添付ファイル解決
        if include_assets:
            assets = resolve_assets_for_node(node, md_text)
            node.attachments = assets
            for a in assets:
                result.attachments_detected += 1
                if a.exists:
                    result.attachments_saved += 1
                else:
                    result.attachments_missing += 1

        # 3. Markdown → ブロック変換
        blocks, warnings = markdown_to_blocks(md_text)
        node.warnings.extend(warnings)
        result.degraded_blocks += len(warnings)

        # 4. __local_asset__ マーカーを実際のブロックに差し替え
        if include_assets:
            blocks = _replace_asset_markers(blocks, node)

        # 5. 書き込み
        if dry_run:
            att_count = len(node.attachments)
            link_count = len(links)
            logger.info(
                "[DRY RUN] コンテンツ書込: %s (%dブロック, %d添付, "
                "%dリンク[解決=%d,未解決=%d], %d警告)",
                node.source_path,
                len(blocks),
                att_count,
                link_count,
                sum(1 for l in links if l.resolved_node),
                sum(1 for l in links if l.fallback),
                len(warnings),
            )
            result.blocks_written += len(blocks)
        else:
            success, failed, write_warnings = write_page_content(
                api, node.notion_id, blocks, node.title,
            )
            result.blocks_written += success
            result.blocks_failed += failed
            node.warnings.extend(write_warnings)

        _log_page_summary(node, blocks, links, result)

    except Exception as e:
        error_msg = f"ページコンテンツ書込失敗: {node.source_path}: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)


def _replace_asset_markers(
    blocks: list[dict[str, Any]],
    node: Node,
) -> list[dict[str, Any]]:
    """__local_asset__ マーカーを添付ファイルブロックに差し替える"""
    result_blocks: list[dict[str, Any]] = []
    asset_map = {a.referenced_from: a for a in node.attachments}

    for block in blocks:
        if block.get("type") == "__local_asset__":
            info = block["__local_asset__"]
            src = info["src"]
            asset = asset_map.get(src)
            if asset:
                result_blocks.append(make_asset_block(asset))
            else:
                # アセット未登録 → フォールバック
                decoded = info.get("decoded", src)
                alt = info.get("alt", "")
                display = alt or decoded
                result_blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": f"[{display}]"},
                            "annotations": {"italic": True, "color": "gray"},
                        }],
                    },
                })
        else:
            result_blocks.append(block)

    return result_blocks


def _pass2_database(
    node: Node,
    schema: Any,
    api: NotionImporter | None,
    dry_run: bool,
    result: ImportResult,
) -> None:
    """Pass 2: データベースの行投入"""
    try:
        from .csv_parser import read_csv_rows

        rows = read_csv_rows(node.file_path)

        if dry_run:
            logger.info(
                "[DRY RUN] DB行投入: %s (%d行, title列=%r, 列=%r)",
                node.source_path,
                len(rows),
                schema.title_column,
                schema.normalized_names,
            )
            result.rows_created += len(rows)
            return

        success, failed, warnings = insert_database_rows(api, node, schema)
        result.rows_created += success
        result.rows_failed += failed
        result.errors.extend(warnings)

    except Exception as e:
        error_msg = f"DB行投入失敗: {node.source_path}: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)


def _log_page_summary(
    node: Node,
    blocks: list[dict[str, Any]],
    links: list,
    result: ImportResult,
) -> None:
    """ページごとのサマリーログを出力する"""
    logger.info(
        "ページ処理完了: source=%s, parent=%s, notion_id=%s, "
        "title=%r, children=%d, attachments=%d, links=%d, "
        "blocks=%d, warnings=%d",
        node.source_path,
        node.parent_source_path or "ROOT",
        node.notion_id,
        node.title,
        len(node.children),
        len(node.attachments),
        len(links),
        len(blocks),
        len(node.warnings),
    )


def _dry_run_database(
    node: Node,
    schema: Any,
    result: ImportResult,
) -> None:
    """dry-run でのDB情報表示"""

    def _count_csv_rows_local(path):
        import csv
        try:
            with open(path, encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                next(reader, None)
                return sum(1 for _ in reader)
        except Exception:
            return 0

    row_count = _count_csv_rows_local(node.file_path)
    logger.info(
        "[DRY RUN] データベース作成: %s (parent=%s, %d列, %d行, title列=%r)",
        node.source_path,
        node.parent_source_path or "ROOT",
        len(schema.columns),
        row_count,
        schema.title_column,
    )
    for col in schema.columns:
        logger.info(
            "[DRY RUN]   列: %r → 型=%s%s",
            col.name, col.inferred_type,
            " (title)" if col.inferred_type == "title" else "",
        )
