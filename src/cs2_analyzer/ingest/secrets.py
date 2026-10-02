"""Encryption for secrets users give us (the Steam game authentication code).

Uses Fernet (AES-128-CBC + HMAC-SHA256). The key comes from ``CS2A_SECRET_KEY``;
for local use a key is generated once and kept in ``ingest.secret_key_file``.
Losing the key means users have to enter their code again; it never exposes it.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from cs2_analyzer.config import ConfigError


class SecretBox:
    def __init__(self, key: bytes):
        self._f = Fernet(key)

    @classmethod
    def from_config(cls, config) -> "SecretBox":
        env = os.environ.get("CS2A_SECRET_KEY")
        if env:
            try:
                return cls(env.strip().encode())
            except ValueError:
                raise ConfigError(
                    "CS2A_SECRET_KEY is not a valid key. Generate one with: python -c \"from cryptography.fernet "
                    "import Fernet; print(Fernet.generate_key().decode())\" (or remove it to use a generated key file)"
                ) from None
        path = Path(config.get("ingest.secret_key_file", "./data/secret.key"))
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(Fernet.generate_key())
            try:
                path.chmod(0o600)
            except OSError:  # Windows
                pass
        return cls(path.read_bytes().strip())

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._f.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None
