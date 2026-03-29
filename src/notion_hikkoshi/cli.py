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
from .models import ExportedDatabase, ExportedPage, ExportTree
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
    """エクスポートデータを解析し、内容を表示する。

    EXPORT_PATH: エクスポートしたZIPファイルまたは展開済みディレクトリ
    """
    console.print("\n[bold]エクスポートデータを解析中...[/bold]\n")

    root_dir = extract_or_use_directory(export_path)
    tree = build_tree(root_dir)

    # サマリー表示
    console.print(f"  ページ数: [bold green]{tree.page_count}[/bold green]")
    console.print(f"  データベース数: [bold blue]{tree.database_count}[/bold blue]")
    console.print()

    # ツリー表示
    rich_tree = Tree("[bold]エクスポート内容[/bold]")
    _build_rich_tree(rich_tree, tree.root_children)
    console.print(rich_tree)
    console.print()

    # データベース詳細
    _print_database_details(tree)


@main.command(name="import")
@click.argument("export_path", type=click.Path(exists=True, path_type=Path))
@click.option("--token", "-t", help="Notion APIトークン")
@click.option(
    "--parent-page",
    "-p",
    required=True,
    help="インポート先の親ページID",
)
@click.option("--dry-run", is_flag=True, help="実際のAPI呼び出しを行わない")
def import_cmd(
    export_path: Path,
    token: str | None,
    parent_page: str,
    dry_run: bool,
) -> None:
    """エクスポートデータを新しいアカウントにインポートする。

    EXPORT_PATH: エクスポートしたZIPファイルまたは展開済みディレクトリ
    """
    if dry_run:
        console.print("\n[bold yellow]== DRY RUN モード ==[/bold yellow]\n")
    else:
        notion_token = get_notion_token(token)

    console.print("[bold]エクスポートデータを解析中...[/bold]")
    root_dir = extract_or_use_directory(export_path)
    tree = build_tree(root_dir)

    console.print(f"  ページ数: [bold green]{tree.page_count}[/bold green]")
    console.print(f"  データベース数: [bold blue]{tree.database_count}[/bold blue]")
    console.print()

    if not dry_run:
        # 確認プロンプト
        if not click.confirm("インポートを開始しますか？"):
            console.print("[yellow]キャンセルしました[/yellow]")
            return

    # インポート実行
    if dry_run:
        # dry_runモードではダミーのAPIクライアントを使用
        api = None  # type: ignore
        result = import_tree(tree, api, parent_page, dry_run=True)
    else:
        api = NotionImporter(notion_token)
        result = import_tree(tree, api, parent_page, dry_run=False)

    # 結果表示
    console.print()
    console.print("[bold]== インポート結果 ==[/bold]")
    console.print(f"  ページ作成: [green]{result.pages_created}[/green]")
    console.print(f"  データベース作成: [blue]{result.databases_created}[/blue]")
    console.print(f"  行作成: [cyan]{result.rows_created}[/cyan]")

    if result.has_errors:
        console.print(f"  エラー: [red]{len(result.errors)}[/red]")
        for error in result.errors:
            console.print(f"    [red]• {error}[/red]")
    else:
        console.print("  [bold green]エラーなし[/bold green]")


def _build_rich_tree(
    parent: Tree,
    items: list[ExportedPage | ExportedDatabase],
) -> None:
    """Rich Treeにアイテムを追加する"""
    for item in items:
        if isinstance(item, ExportedPage):
            label = f"📄 {item.title}"
            if item.images:
                label += f" [dim]({len(item.images)}画像)[/dim]"
            node = parent.add(label)
            if item.children:
                _build_rich_tree(node, item.children)
        elif isinstance(item, ExportedDatabase):
            label = (
                f"🗃️  {item.title} "
                f"[dim]({len(item.columns)}列, {item.row_count}行)[/dim]"
            )
            parent.add(label)


def _print_database_details(tree: ExportTree) -> None:
    """データベースの詳細情報を表示する"""
    databases: list[ExportedDatabase] = []
    stack: list[ExportedPage | ExportedDatabase] = list(tree.root_children)
    while stack:
        item = stack.pop()
        if isinstance(item, ExportedDatabase):
            databases.append(item)
        elif isinstance(item, ExportedPage):
            stack.extend(item.children)

    if not databases:
        return

    console.print("[bold]データベース詳細:[/bold]\n")
    for db in databases:
        table = Table(title=db.title, show_lines=True)
        table.add_column("カラム名", style="cyan")
        table.add_column("推定型", style="green")
        for col in db.columns:
            table.add_row(col.name, col.inferred_type)
        console.print(table)
        console.print()


if __name__ == "__main__":
    main()
