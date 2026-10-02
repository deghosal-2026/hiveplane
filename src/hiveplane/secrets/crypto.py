"""Envelope encryption for the secret store (M45-01).

A per-value data key encrypts the plaintext (AES-256-GCM); the data key is in
turn wrapped by a managed key-provider key. The store persists ciphertext, the
nonce, the wrapped data key, and the key id — never plaintext.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hiveplane.secrets.models import SecretError

_NONCE_BYTES = 12
_KEY_BYTES = 32


class EncryptedValue:
    """A freshly encrypted value and its wrapped data key."""

    __slots__ = ("ciphertext", "key_id", "key_nonce", "nonce", "wrapped_key")

    def __init__(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        wrapped_key: bytes,
        key_nonce: bytes,
        key_id: str,
    ) -> None:
        self.ciphertext = ciphertext
        self.nonce = nonce
        self.wrapped_key = wrapped_key
        self.key_nonce = key_nonce
        self.key_id = key_id


class KeyProvider(Protocol):
    """Wraps and unwraps per-value data keys with a managed key."""

    def encrypt(self, plaintext: bytes) -> EncryptedValue: ...

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        wrapped_key: bytes,
        key_nonce: bytes,
        key_id: str,
    ) -> bytes: ...


class LocalKeyProvider:
    """A local AES-256-GCM key provider (file/env key, wrapped data keys)."""

    def __init__(self, master_key: bytes, *, key_id: str = "local") -> None:
        if len(master_key) != _KEY_BYTES:
            raise SecretError("master key must be 32 bytes")
        self._master_key = master_key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        """Return the id of the managed key."""
        return self._key_id

    def encrypt(self, plaintext: bytes) -> EncryptedValue:
        """Encrypt with a fresh data key wrapped by the master key."""
        data_key = os.urandom(_KEY_BYTES)
        data = AESGCM(data_key)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = data.encrypt(nonce, plaintext, None)

        wrapper = AESGCM(self._master_key)
        key_nonce = os.urandom(_NONCE_BYTES)
        wrapped_key = wrapper.encrypt(key_nonce, data_key, self._key_id.encode("utf-8"))
        return EncryptedValue(
            ciphertext=ciphertext,
            nonce=nonce,
            wrapped_key=wrapped_key,
            key_nonce=key_nonce,
            key_id=self._key_id,
        )

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        wrapped_key: bytes,
        key_nonce: bytes,
        key_id: str,
    ) -> bytes:
        """Unwrap the data key and decrypt, failing closed on tampering."""
        wrapper = AESGCM(self._master_key)
        try:
            data_key = wrapper.decrypt(key_nonce, wrapped_key, key_id.encode("utf-8"))
        except InvalidTag as exc:
            raise SecretError("data key unwrap failed") from exc
        data = AESGCM(data_key)
        try:
            return data.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise SecretError("secret decryption failed") from exc


def load_or_create_master_key(path: str | Path) -> bytes:
    """Load a 32-byte master key from ``path``, creating it if absent."""
    key_path = Path(path)
    if key_path.is_file():
        try:
            key = bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
        except ValueError as exc:
            raise SecretError("master key file is malformed") from exc
        if len(key) != _KEY_BYTES:
            raise SecretError("master key file is malformed")
        return key
    key = os.urandom(_KEY_BYTES)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key.hex(), encoding="utf-8")
    with contextlib.suppress(OSError):
        key_path.chmod(0o600)
    return key
