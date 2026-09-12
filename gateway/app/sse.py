"""OpenAI互換SSEストリームの中継で共通に使うユーティリティ。

バックエンド(Ollama)からのチャンクを素通しするだけだと、
- `model` フィールドが内部実体名(backend_name)のまま漏れる(D-003違反)
- `usage` がバックエンドの既定では返らない(stream_options が必要)
という2点が問題になるため、ここで吸収する。
"""

import json


def with_stream_usage(payload: dict) -> dict:
    """バックエンドへのペイロードに usage 取得オプションを追加する。"""
    return {**payload, "stream_options": {"include_usage": True}}


def rewrite_chunk(chunk: bytes, served_name: str, usage: dict[str, int]) -> bytes:
    """SSEチャンクの `model` を served_name に書き換え、`usage` を取り込む。

    1バイト列チャンクに複数の `data: ...` 行が含まれる場合にも対応するが、
    1行がチャンク境界をまたいで分割された場合はその行のみ書き換えを諦めて
    そのまま通す(ベストエフォート。バイト列自体は破壊しない)。
    """
    lines = chunk.decode("utf-8", errors="ignore").split("\n")
    out_lines = []
    for line in lines:
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            out_lines.append(line)
            continue
        try:
            obj = json.loads(line[len("data: ") :])
        except json.JSONDecodeError:
            out_lines.append(line)
            continue

        if "model" in obj:
            obj["model"] = served_name

        obj_usage = obj.get("usage")
        if obj_usage:
            usage["prompt_tokens"] = obj_usage.get("prompt_tokens", 0)
            usage["completion_tokens"] = obj_usage.get("completion_tokens", 0)

        out_lines.append(f"data: {json.dumps(obj, ensure_ascii=False)}")
    return "\n".join(out_lines).encode("utf-8")
