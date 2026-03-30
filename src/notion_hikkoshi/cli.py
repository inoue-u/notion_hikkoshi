"""CLIエントリーポイント"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from .config import get_notion_token
from .extractor import extract_or_use_directory
from .importer import import_tree
from .node_registry import Node, NodeRegistry, NodeType
from .notion_api import NotionImporter
from .tree_builder import build_tree
from .utils import setup_logging

console = Console()


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="詳細なログを表示")
def main(verbose: bool) -> None:
    """notion-hikkoshi: Notionアカウント移行ツール

    前のアカウントからエクスポートしたデータを、新しいアカウントにインポートします。
    """
    setup_logging(verbose)


@main.command()
@click.argument("export_path", type=click.Path(exists=True, path_type=Path))
def parse(export_path: Path) -> None:
    """エクスポートデータを解析し、内容を表示する。"""
    console.print("\n[bold]エクスポートデータを解析中...[/bold]\n")

    root_dir = extract_or_use_directory(export_path)
    registry = build_tree(root_dir)
    _print_summary(registry)
    _print_tree(registry)
    _print_database_details(registry)


@main.command(name="import")
@click.argument("export_path", type=click.Path(exists=True, path_type=Path))
@click.option("--token", "-t", help="Notion APIトークン")
@click.option("--parent-page", "-p", required=True, help="インポート先の親ページID")
@click.option("--dry-run", is_flag=True, help="実際のAPI呼び出しを行わない")
@click.option("--include-assets/--no-assets", default=True, help="添付ファイル処理")
def import_cmd(
    export_path: Path,
    token: str | None,
    parent_page: str,
    dry_run: bool,
    include_assets: bool,
) -> None:
    """エクスポートデータを新しいアカウントにインポートする。"""
    if dry_run:
        console.print("\n[bold yellow]== DRY RUN モード ==[/bold yellow]\n")
    else:
        notion_token = get_notion_token(token)

    console.print("[bold]エクスポートデータを解析中...[/bold]")
    root_dir = extract_or_use_directory(export_path)
    registry = build_tree(root_dir)

    _print_summary(registry)

    if dry_run:
        _print_tree(registry)
        _print_database_details(registry)

    if not dry_run:
        if not click.confirm("インポートを開始しますか？"):
            console.print("[yellow]キャンセルしました[/yellow]")
            return

    if dry_run:
        result = import_tree(registry, None, parent_page, dry_run=True, include_assets=include_assets)
    else:
        api = NotionImporter(notion_token)
        result = import_tree(registry, api, parent_page, dry_run=False, include_assets=include_assets)

    # 結果表示
    console.print()
    console.print("[bold]== インポート結果 ==[/bold]")
    console.print(f"  ページ作成: [green]{result.pages_created}[/green]")
    console.print(f"  データベース作成: [blue]{result.databases_created}[/blue]")
    console.print(f"  CSV重複スキップ: [dim]{result.csv_duplicates_skipped}[/dim]")
    console.print(f"  DB行作成: [cyan]{result.rows_created}[/cyan]")
    if result.rows_failed:
        console.print(f"  DB行失敗: [red]{result.rows_failed}[/red]")
    console.print(f"  ブロック書込: [green]{result.blocks_written}[/green]")
    if result.blocks_failed:
        console.print(f"  ブロック失敗: [red]{result.blocks_failed}[/red]")
    if include_assets:
        console.print(
            f"  添付ファイル: 検出={result.attachments_detected}, "
            f"保存=[green]{result.attachments_saved}[/green], "
            f"失敗=[red]{result.attachments_failed}[/red], "
            f"未解決=[yellow]{result.attachments_missing}[/yellow]"
        )
    console.print(
        f"  内部リンク: 解決=[green]{result.links_resolved}[/green], "
        f"未解決=[yellow]{result.links_unresolved}[/yellow]"
    )
    if result.degraded_blocks:
        console.print(f"  degradedブロック: [yellow]{result.degraded_blocks}[/yellow]")

    if result.has_errors:
        console.print(f"\n  [bold red]エラー: {len(result.errors)}件[/bold red]")
        for error in result.errors[:20]:
            console.print(f"    [red]• {error}[/red]")
        if len(result.errors) > 20:
            console.print(f"    [dim]... 他 {len(result.errors) - 20} 件[/dim]")
    else:
        console.print("  [bold green]エラーなし[/bold green]")


def _print_summary(registry: NodeRegistry) -> None:
    """サマリー表示"""
    summary = registry.summary()
    console.print(f"  ページ数: [bold green]{summary['pages']}[/bold green]")
    console.print(f"  データベース数: [bold blue]{summary['databases']}[/bold blue]")
    if summary["csv_duplicates"]:
        console.print(
            f"  CSV重複 (_all.csv): [dim]{summary['csv_duplicates']}[/dim]"
        )
    console.print()


def _print_tree(registry: NodeRegistry) -> None:
    """階層ツリー表示"""
    rich_tree = Tree("[bold]エクスポート内容[/bold]")

    for node in sorted(registry.root_nodes, key=lambda n: n.source_path):
        _add_node_to_tree(rich_tree, node)

    console.print(rich_tree)
    console.print()


def _add_node_to_tree(parent: Tree, node: Node) -> None:
    """Treeにノードを再帰追加"""
    if node.node_type == NodeType.PAGE:
        label = f"📄 {node.title}"
        extras = []
        if node.attachments:
            imgs = sum(1 for a in node.attachments if a.category == "image")
            pdfs = sum(1 for a in node.attachments if a.category == "pdf")
            missing = sum(1 for a in node.attachments if not a.exists)
            if imgs:
                extras.append(f"{imgs}画像")
            if pdfs:
                extras.append(f"{pdfs}PDF")
            if missing:
                extras.append(f"[red]{missing}未検出[/red]")
        if extras:
            label += f" [dim]({', '.join(extras)})[/dim]"
        child_tree = parent.add(label)
        for child in sorted(node.children, key=lambda n: n.source_path):
            _add_node_to_tree(child_tree, child)

    elif node.node_type == NodeType.DATABASE:
        dup = " [dim](重複→スキップ)[/dim]" if node.csv_duplicate_of else ""
        label = f"🗃️  {node.title}{dup}"
        parent.add(label)


def _print_database_details(registry: NodeRegistry) -> None:
    """データベース詳細"""
    dbs = [n for n in registry.databases if not n.csv_duplicate_of]
    if not dbs:
        return

    console.print("[bold]データベース詳細:[/bold]\n")
    from .csv_parser import infer_schema

    for db_node in dbs:
        try:
            schema = infer_schema(db_node.file_path)
        except Exception:
            console.print(f"  [red]スキーマ推定失敗: {db_node.title}[/red]")
            continue

        table = Table(title=f"{db_node.title} ({db_node.source_path})", show_lines=True)
        table.add_column("カラム名", style="cyan")
        table.add_column("推定型", style="green")
        table.add_column("備考", style="yellow")
        for col in schema.columns:
            note = "← title列" if col.inferred_type == "title" else ""
            table.add_row(col.name, col.inferred_type, note)
        console.print(table)
        console.print(f"  title列: [bold]{schema.title_column!r}[/bold]")
        console.print(f"  parent: {db_node.parent_source_path or 'ROOT'}")
        console.print()


if __name__ == "__main__":
    main()
