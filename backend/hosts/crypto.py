"""登录凭据的对称加密（Fernet / AES-128-CBC + HMAC）。

口令与私钥绝不明文落库、也不明文返回前端；接口仅暴露
``has_password`` / ``has_private_key`` 之类的布尔位。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.CREDENTIAL_KEY
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise ImproperlyConfigured(
                "CREDENTIAL_KEY 必须是 32 字节 base64 编码的 Fernet key"
            ) from exc
    return _fernet


def encrypt_secret(plaintext: str) -> bytes:
    if not plaintext:
        return b""
    return get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes | None) -> str:
    if not token:
        return ""
    try:
        return get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        return ""
