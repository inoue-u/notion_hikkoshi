"""ZIPファイルの展開処理"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_export(zip_path: Path) -> Path:
    """Notionエクスポートの ZIP を一時ディレクトリに展開する。

    Notionのエクスポートは、ZIPの中にUUID付きの単一フォルダが
    含まれることがある。その場合は内側のフォルダを返す。

    Args:
        zip_path: ZIPファイルのパス

    Returns:
        展開されたルートディレクトリのパス
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIPファイルが見つかりません: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"有効なZIPファイルではありません: {zip_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="notion_hikkoshi_"))
    logger.info("展開先: %s", tmp_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # Notionのエクスポートは単一フォルダを含むことがある
    entries = list(tmp_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        logger.info("単一フォルダを検出: %s", entries[0].name)
        return entries[0]

    return tmp_dir


def extract_or_use_directory(path: Path) -> Path:
    """ZIPファイルまたはディレクトリを受け取り、展開済みディレクトリを返す。

    ZIPの場合は展開し、ディレクトリの場合はそのまま返す。
    """
    if path.is_dir():
        logger.info("ディレクトリを直接使用: %s", path)
        return path
    return extract_export(path)
