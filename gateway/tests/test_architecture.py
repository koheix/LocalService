"""アーキテクチャ上の非交渉事項を構造的に縛るテスト。"""

from pathlib import Path

ROUTERS_DIR = Path(__file__).resolve().parent.parent / "app" / "routers"


def test_routers_do_not_mention_ollama() -> None:
    """ルーター層にOllama語彙が漏れていないこと。

    CLAUDE.mdの非交渉事項「バックエンド固有の処理はapp/backends/にのみ書く」
    (T-06完了条件: grep -ri "ollama" gateway/app/routers/ がヒット0件)を
    コードレビュー任せにせず、テストで縛る。
    """
    offenders = [
        path.name for path in ROUTERS_DIR.glob("*.py") if "ollama" in path.read_text().lower()
    ]
    assert offenders == []
