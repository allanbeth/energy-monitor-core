from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional, Tuple


AUTH_ITERATIONS = 260000
SALT_BYTES = 16


def build_password_hash(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        AUTH_ITERATIONS,
    )
    return f"pbkdf2_sha256${AUTH_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password_hash(stored_hash: str, password: str) -> bool:
    text = str(stored_hash or "")
    parts = text.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False

    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = parts[3]
    except Exception:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        iterations,
    ).hex()
    return hmac.compare_digest(derived, expected)


def build_basic_token(username: str, password: str, instance_id: str = "") -> str:
    raw = f"{username}:{password}".encode("utf-8")
    token = f"Basic {base64.b64encode(raw).decode('utf-8')}"
    normalized_instance = str(instance_id or "").strip()
    if normalized_instance:
        token = f"{token};instance={normalized_instance}"
    return token


def parse_basic_token(token: str) -> Tuple[str, str, str]:
    normalized = str(token or "").strip()
    if not normalized.lower().startswith("basic "):
        return "", "", ""

    try:
        token_body = normalized.split(" ", 1)[1].strip()
        instance_id = ""
        if ";instance=" in token_body:
            token_body, instance_id = token_body.split(";instance=", 1)
            instance_id = instance_id.strip()

        decoded = base64.b64decode(token_body).decode("utf-8")
    except Exception:
        return "", "", ""

    if ":" not in decoded:
        return "", "", ""

    username, password = decoded.split(":", 1)
    return username, password, instance_id
