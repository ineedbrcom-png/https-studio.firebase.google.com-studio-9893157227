"""Authentication helpers: password hashing and token creation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict

from .errors import HttpError


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data: str) -> bytes:
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, *, salt: str | None = None) -> Dict[str, str]:
    if not salt:
        salt_bytes = secrets.token_bytes(16)
    else:
        salt_bytes = base64.b64decode(salt.encode("ascii"))
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 120_000)
    return {
        "hash": base64.b64encode(dk).decode("ascii"),
        "salt": base64.b64encode(salt_bytes).decode("ascii"),
    }


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    computed = hash_password(password, salt=salt)["hash"]
    return hmac.compare_digest(computed, stored_hash)


def _get_secret() -> bytes:
    secret = os.environ.get("JWT_SECRET", "change-me")
    return secret.encode("utf-8")


def create_token(payload: Dict[str, Any], *, expires_in: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = dict(payload)
    payload.setdefault("iat", int(time.time()))
    payload["exp"] = int(time.time()) + int(expires_in)

    header_b64 = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(_get_secret(), signing_input, hashlib.sha256).digest()
    signature_b64 = _base64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode_token(token: str) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise HttpError.unauthorized("Invalid token format") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_signature = hmac.new(_get_secret(), signing_input, hashlib.sha256).digest()
    signature = _base64url_decode(signature_b64)
    if not hmac.compare_digest(expected_signature, signature):
        raise HttpError.unauthorized("Invalid token signature")

    payload_bytes = _base64url_decode(payload_b64)
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise HttpError.unauthorized("Invalid token payload") from exc

    exp = payload.get("exp")
    if exp is None or int(exp) < int(time.time()):
        raise HttpError.unauthorized("Token has expired")

    return payload

