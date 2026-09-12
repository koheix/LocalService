"""app/limits.py の純粋なユニットテスト(DB・ネットワーク不要)。"""

import pytest

from app.errors import RateLimitError
from app.limits import ConcurrencySlots, RateLimiter


def test_rate_limiter_allows_up_to_limit_then_blocks() -> None:
    limiter = RateLimiter()
    for _ in range(5):
        limiter.check(user_id=1, limit_per_min=5)
    with pytest.raises(RateLimitError):
        limiter.check(user_id=1, limit_per_min=5)


def test_rate_limiter_error_has_retry_after_header() -> None:
    limiter = RateLimiter()
    limiter.check(user_id=1, limit_per_min=1)
    with pytest.raises(RateLimitError) as exc_info:
        limiter.check(user_id=1, limit_per_min=1)
    assert "Retry-After" in exc_info.value.headers


def test_rate_limiter_tracks_users_independently() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check(user_id=1, limit_per_min=3)
    # user_id=2 は別枠なので制限に達していない
    limiter.check(user_id=2, limit_per_min=3)


def test_concurrency_slots_acquire_up_to_limit() -> None:
    slots = ConcurrencySlots()
    assert slots.acquire(1, 2) is True
    assert slots.acquire(1, 2) is True
    assert slots.acquire(1, 2) is False


def test_concurrency_slots_release_frees_a_slot() -> None:
    slots = ConcurrencySlots()
    slots.acquire(1, 1)
    assert slots.acquire(1, 1) is False
    slots.release(1)
    assert slots.acquire(1, 1) is True


def test_concurrency_slots_release_without_acquire_is_noop() -> None:
    slots = ConcurrencySlots()
    slots.release(1)  # 例外にならない
    assert slots.acquire(1, 1) is True
