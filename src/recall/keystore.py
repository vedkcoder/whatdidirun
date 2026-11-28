"""Encryption for API keys stored in the database (AES-256-GCM).

The 32-byte master key lives next to the database, protected per platform:
  Windows      wrapped with DPAPI (CryptProtectData), so only this Windows user account can unwrap it
  elsewhere    a 0600 file readable only by this user, or RECALL_SECRET_KEY (e.g. for Docker)

On Linux/macOS the protection therefore comes from file permissions: anyone who can read your data
directory as you can decrypt the keys. It keeps keys out of plain text, backups of the DB alone, and
casual viewing.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from .config import IS_WINDOWS

KEY_FILE = "secret.key"
DPAPI_FILE = "secret.key.dpapi"


class KeyStoreError(RuntimeError):
    pass


# ---- Windows DPAPI (ctypes, no extra dependency) -------------------------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    args = (ctypes.byref(blob_in), "whatdidirun" if protect else None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not fn(*args):
        raise KeyStoreError(f"Windows DPAPI {'protect' if protect else 'unprotect'} failed (error {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# ---- master key ----------------------------------------------------------------------------------------

def _from_env(raw: str) -> bytes:
    """RECALL_SECRET_KEY: 32 bytes as urlsafe base64 (e.g. `openssl rand -base64 32`), or any passphrase."""
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(key) == 32:
            return key
    except ValueError:
        pass
    return hashlib.sha256(raw.encode()).digest()


def _write_private(path: Path, data: bytes) -> None:
    """Create a file only this user can read; fail if it already exists (no races between processes)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


class KeyStore:
    def __init__(self, data_dir: Path, windows: bool = IS_WINDOWS):
        self.data_dir = Path(data_dir)
        self.windows = windows
        self._key: bytes | None = None

    @property
    def backend(self) -> str:
        if os.environ.get("RECALL_SECRET_KEY"):
            return "env"
        return "dpapi" if self.windows else "file"

    def _load_or_create(self) -> bytes:
        if raw := os.environ.get("RECALL_SECRET_KEY"):
            return _from_env(raw)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / (DPAPI_FILE if self.windows else KEY_FILE)
        for _ in range(2):
            if path.exists():
                blob = path.read_bytes()
                key = _dpapi(blob, protect=False) if self.windows else blob
                if len(key) != 32:
                    raise KeyStoreError(f"{path} is not a valid key file")
                return key
            key = os.urandom(32)
            try:
                _write_private(path, _dpapi(key, protect=True) if self.windows else key)
                return key
            except FileExistsError:  # another process created it first: read theirs
                continue
        raise KeyStoreError(f"could not create {path}")

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = self._load_or_create()
        return self._key

    def encrypt(self, plaintext: str, context: str) -> tuple[bytes, bytes]:
        """Returns (nonce, ciphertext). `context` (the provider id) is bound in as associated data."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        return nonce, AESGCM(self.key).encrypt(nonce, plaintext.encode(), context.encode())

    def decrypt(self, nonce: bytes, ciphertext: bytes, context: str) -> str:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            return AESGCM(self.key).decrypt(nonce, ciphertext, context.encode()).decode()
        except InvalidTag:
            raise KeyStoreError(
                "a saved API key can't be decrypted (the master key changed, e.g. a different RECALL_SECRET_KEY "
                "or Windows user). Save the key again in Settings."
            ) from None
