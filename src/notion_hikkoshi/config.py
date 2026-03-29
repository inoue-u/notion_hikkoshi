"""設定管理"""

from __future__ import annotations

import os

from dotenv import load_dotenv


def get_notion_token(token: str | None = None) -> str:
    """Notion APIトークンを取得する。

    優先順位: 引数 > 環境変数 > .envファイル
    """
    if token:
        return token

    load_dotenv()
    env_token = os.getenv("NOTION_TOKEN")
    if env_token:
        return env_token

    raise ValueError(
        "Notion APIトークンが設定されていません。\n"
        "以下のいずれかの方法で設定してください:\n"
        "  1. --token オプションで指定\n"
        "  2. NOTION_TOKEN 環境変数を設定\n"
        "  3. .env ファイルに NOTION_TOKEN=xxx を記載"
    )
