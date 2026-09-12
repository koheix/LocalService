"""パスワード検証・セッション・APIキー検証。"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()

API_KEY_PREFIX_LEN = 8


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """セッショントークンは高エントロピーな乱数なので、パスワードと異なり
    低速なArgon2は不要。SHA-256で十分。"""
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry(ttl_hours: int) -> datetime:
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def new_api_key() -> tuple[str, str]:
    """(平文キー, key_prefix) を返す。"""
    key = f"sk-{secrets.token_urlsafe(32)}"
    return key, key[:API_KEY_PREFIX_LEN]


def hash_api_key(key: str) -> str:
    return _ph.hash(key)


def verify_api_key(key: str, key_hash: str) -> bool:
    try:
        return _ph.verify(key_hash, key)
    except VerifyMismatchError:
        return False
