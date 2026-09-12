"""app/limits.py の純粋なユニットテスト(DB・ネットワーク不要)。"""

import pytest

import app.limits as limits_mod
from app.errors import RateLimitError
from app.limits import ConcurrencySlots, RateLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(limits_mod.time, "monotonic", fake)
    return fake


def test_rate_limiter_allows_up_to_limit_then_blocks() -> None:
    limiter = RateLimiter()
    for _ in range(5):
        limiter.check(user_id=1, limit_per_min=5)
    with pytest.raises(RateLimitError):
        limiter.check(user_id=1, limit_per_min=5)


def test_rate_limiter_retry_after_is_exact(clock: _FakeClock) -> None:
    limiter = RateLimiter()
    limiter.check(user_id=1, limit_per_min=1)  # t=0 で1件消費
    clock.advance(10.0)  # t=10
    with pytest.raises(RateLimitError) as exc_info:
        limiter.check(user_id=1, limit_per_min=1)
    # 直近のヒットは t=0, ウィンドウが空くのは t=60。now=10 なので残り50秒+1。
    assert exc_info.value.headers["Retry-After"] == "51"


def test_rate_limiter_sliding_window_expires(clock: _FakeClock) -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check(user_id=1, limit_per_min=3)  # t=0 で3件使い切る

    clock.advance(59.9)
    with pytest.raises(RateLimitError):
        limiter.check(user_id=1, limit_per_min=3)  # まだウィンドウ内

    clock.advance(0.2)  # t=60.1、最初の3件がウィンドウから抜ける
    limiter.check(user_id=1, limit_per_min=3)  # 例外にならない


def test_rate_limiter_tracks_users_independently() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check(user_id=1, limit_per_min=3)
    with pytest.raises(RateLimitError):
        limiter.check(user_id=1, limit_per_min=3)

    # user_id=2 は別枠なので、user1が上限でも上限まで丸々使える。
    for _ in range(3):
        limiter.check(user_id=2, limit_per_min=3)
    with pytest.raises(RateLimitError):
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


def test_concurrency_slots_tracks_users_independently() -> None:
    slots = ConcurrencySlots()
    assert slots.acquire(1, 1) is True
    assert slots.acquire(1, 1) is False
    # user_id=2 は別枠なので、user1が上限でも取得できる。
    assert slots.acquire(2, 1) is True
