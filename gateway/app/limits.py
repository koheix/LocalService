"""同時実行数・レート制限。

Phase 0 はプロセス1つ（Compose上でgatewayはレプリカ数1）が前提のため、
インメモリで十分。将来マルチプロセス化する場合はRedis等に置き換える。
"""

import time
from collections import defaultdict, deque

from app.errors import RateLimitError


class RateLimiter:
    """固定ユーザーあたり直近60秒のリクエスト数を制限する（スライディングウィンドウ）。"""

    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, limit_per_min: int) -> None:
        now = time.monotonic()
        window_start = now - 60.0
        hits = self._hits[user_id]
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= limit_per_min:
            retry_after = max(1, int(hits[0] + 60.0 - now) + 1)
            raise RateLimitError(
                "リクエスト数の上限に達しました", headers={"Retry-After": str(retry_after)}
            )
        hits.append(now)


class ConcurrencySlots:
    """ユーザーあたりの同時実行数を制限する。"""

    def __init__(self) -> None:
        self._counts: dict[int, int] = defaultdict(int)

    def acquire(self, user_id: int, limit: int) -> bool:
        if self._counts[user_id] >= limit:
            return False
        self._counts[user_id] += 1
        return True

    def release(self, user_id: int) -> None:
        if self._counts[user_id] > 0:
            self._counts[user_id] -= 1


rate_limiter = RateLimiter()
concurrency_slots = ConcurrencySlots()
