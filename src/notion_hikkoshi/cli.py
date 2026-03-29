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
from .models import Attachment, ExportedDatabase, ExportedPage, ExportTree
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

    # サマリー
    att_stats = _collect_attachment_stats(tree)
    console.print(f"  ページ数: [bold green]{tree.page_count}[/bold green]")
    console.print(f"  データベース数: [bold blue]{tree.database_count}[/bold blue]")
    console.print(
        f"  添付ファイル: [bold cyan]{att_stats['total']}[/bold cyan] "
        f"(画像={att_stats['images']}, PDF={att_stats['pdfs']}, "
        f"その他={att_stats['others']})"
    )
    if att_stats["missing"] > 0:
        console.print(
            f"  [bold red]未検出ファイル: {att_stats['missing']}[/bold red]"
        )
    console.print()

    # ツリー表示
    rich_tree = Tree("[bold]エクスポート内容[/bold]")
    _build_rich_tree(rich_tree, tree.root_children)
    console.print(rich_tree)
    console.print()

    # データベース詳細
    _print_database_details(tree)

    # 未検出ファイル一覧
    _print_missing_attachments(tree)


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
@click.option(
    "--include-assets/--no-assets",
    default=True,
    help="添付ファイルの処理を含める/除外する",
)
def import_cmd(
    export_path: Path,
    token: str | None,
    parent_page: str,
    dry_run: bool,
    include_assets: bool,
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

    # サマリー
    att_stats = _collect_attachment_stats(tree)
    console.print(f"  ページ数: [bold green]{tree.page_count}[/bold green]")
    console.print(f"  データベース数: [bold blue]{tree.database_count}[/bold blue]")
    if include_assets:
        console.print(
            f"  添付ファイル: [bold cyan]{att_stats['total']}[/bold cyan] "
            f"(画像={att_stats['images']}, PDF={att_stats['pdfs']})"
        )
        if att_stats["missing"] > 0:
            console.print(
                f"  [bold red]未検出ファイル: {att_stats['missing']}[/bold red]"
            )
    console.print()

    # dry-run: 詳細表示
    if dry_run:
        _print_database_details(tree)
        _print_missing_attachments(tree)

    if not dry_run:
        if not click.confirm("インポートを開始しますか？"):
            console.print("[yellow]キャンセルしました[/yellow]")
            return

    # インポート実行
    if dry_run:
        api = None  # type: ignore
        result = import_tree(tree, api, parent_page, dry_run=True, include_assets=include_assets)
    else:
        api = NotionImporter(notion_token)
        result = import_tree(tree, api, parent_page, dry_run=False, include_assets=include_assets)

    # 結果表示
    console.print()
    console.print("[bold]== インポート結果 ==[/bold]")
    console.print(f"  ページ作成: [green]{result.pages_created}[/green]")
    console.print(f"  データベース作成: [blue]{result.databases_created}[/blue]")
    console.print(f"  行作成: [cyan]{result.rows_created}[/cyan]")
    if result.rows_failed > 0:
        console.print(f"  行失敗: [red]{result.rows_failed}[/red]")
    if include_assets:
        console.print(f"  添付ファイル検出: [cyan]{result.attachments_found}[/cyan]")
        if result.attachments_missing > 0:
            console.print(
                f"  添付ファイル未検出: [red]{result.attachments_missing}[/red]"
            )

    if result.has_errors:
        console.print(f"  エラー: [red]{len(result.errors)}[/red]")
        for error in result.errors:
            console.print(f"    [red]• {error}[/red]")
    else:
        console.print("  [bold green]エラーなし[/bold green]")


def _collect_attachment_stats(tree: ExportTree) -> dict[str, int]:
    """ツリー全体の添付ファイル統計を収集する"""
    stats = {"total": 0, "images": 0, "pdfs": 0, "others": 0, "missing": 0}
    stack: list[ExportedPage | ExportedDatabase] = list(tree.root_children)
    while stack:
        item = stack.pop()
        if isinstance(item, ExportedPage):
            for att in item.attachments:
                stats["total"] += 1
                if att.category == "image":
                    stats["images"] += 1
                elif att.category == "pdf":
                    stats["pdfs"] += 1
                else:
                    stats["others"] += 1
                if not att.exists:
                    stats["missing"] += 1
            stack.extend(item.children)
    return stats


def _build_rich_tree(
    parent: Tree,
    items: list[ExportedPage | ExportedDatabase],
) -> None:
    """Rich Treeにアイテムを追加する"""
    for item in items:
        if isinstance(item, ExportedPage):
            label = f"📄 {item.title}"
            extras = []
            if item.images:
                extras.append(f"{len(item.images)}画像(フォルダ)")
            att_imgs = sum(1 for a in item.attachments if a.category == "image")
            att_pdfs = sum(1 for a in item.attachments if a.category == "pdf")
            att_others = sum(1 for a in item.attachments if a.category == "other")
            att_missing = sum(1 for a in item.attachments if not a.exists)
            if att_imgs:
                extras.append(f"{att_imgs}画像")
            if att_pdfs:
                extras.append(f"{att_pdfs}PDF")
            if att_others:
                extras.append(f"{att_others}他")
            if att_missing:
                extras.append(f"[red]{att_missing}未検出[/red]")
            if extras:
                label += f" [dim]({', '.join(extras)})[/dim]"
            node = parent.add(label)
            if item.children:
                _build_rich_tree(node, item.children)
        elif isinstance(item, ExportedDatabase):
            title_col = ""
            for col in item.columns:
                if col.inferred_type == "title":
                    title_col = col.name
                    break
            label = (
                f"🗃️  {item.title} "
                f"[dim]({len(item.columns)}列, {item.row_count}行, "
                f"title={title_col!r})[/dim]"
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
        title_col = None
        table = Table(title=db.title, show_lines=True)
        table.add_column("カラム名", style="cyan")
        table.add_column("推定型", style="green")
        table.add_column("備考", style="yellow")
        for col in db.columns:
            note = ""
            if col.inferred_type == "title":
                note = "← title列"
                title_col = col.name
            table.add_row(col.name, col.inferred_type, note)
        console.print(table)
        if title_col:
            console.print(f"  title列: [bold]{title_col!r}[/bold]")
        console.print()


def _print_missing_attachments(tree: ExportTree) -> None:
    """未検出の添付ファイルを一覧表示する"""
    missing: list[tuple[str, Attachment]] = []
    stack: list[ExportedPage | ExportedDatabase] = list(tree.root_children)
    while stack:
        item = stack.pop()
        if isinstance(item, ExportedPage):
            for att in item.attachments:
                if not att.exists:
                    missing.append((item.title, att))
            stack.extend(item.children)

    if not missing:
        return

    console.print("[bold red]未検出の添付ファイル:[/bold red]\n")
    for page_title, att in missing:
        console.print(
            f"  [red]•[/red] {page_title}: "
            f"{att.referenced_from} → {att.file_path}"
        )
    console.print()


if __name__ == "__main__":
    main()
