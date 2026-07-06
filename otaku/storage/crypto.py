"""Client-side encryption with envelope key management.

A random 32-byte Data Encryption Key (DEK) encrypts message/summary blobs
(AES-256-GCM, via `Cipher`). The DEK is never stored in the clear: it is wrapped
by a Key Encryption Key (KEK) and the wrapped form lives in
``~/.otaku/keys.json``. The KEK comes from a provider:

    keychain    KEK in the OS keychain (macOS ``security`` / Linux
                ``secret-tool``); otaku generates and stores it. Default.
    command     KEK printed by ``[encryption].retrieve_command`` (1Password
                ``op``, ``pass``, anything). You store the secret; otaku reads it.
    passphrase  KEK derived from a passphrase via scrypt — nothing stored,
                prompts each launch.
    disk        KEK in ``~/.otaku/kek.key`` (0600) — zero-friction opt-out and
                the automatic fallback when no keychain tool is present.

Several slots can wrap the same DEK, so providers coexist and swapping one is a
re-wrap with no data re-encryption.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from otaku.config import CONFIG_DIR, Encryption

KEY_LEN = 32

KEYSTORE_PATH = CONFIG_DIR / "keys.json"
DISK_KEK_PATH = CONFIG_DIR / "kek.key"
LEGACY_KEYFILE = CONFIG_DIR / "history.key"  # pre-envelope; ~/.otaku only

# scrypt cost (~tens of ms, ~32 MB to derive).
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}

_KC_SERVICE = "otaku"
_KC_ACCOUNT = "kek"

# Default config section, owned here so config.default_config() can assemble it.
DEFAULT_CONFIG = '[encryption]\nprovider = "keychain"'


class CryptoError(Exception):
    pass


class Cipher:
    """AES-256-GCM AEAD with a 12-byte nonce (keyed by the DEK)."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_LEN:
            raise CryptoError(f"key length {len(key)}, want {KEY_LEN}")
        self._aead = AESGCM(key)

    def seal(self, plaintext: bytes) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        return self._aead.encrypt(nonce, plaintext, None), nonce

    def unseal(self, ciphertext: bytes, nonce: bytes) -> bytes:
        return self._aead.decrypt(nonce, ciphertext, None)


# ---------- DEK wrapping ----------


def _wrap_dek(dek: bytes, kek: bytes) -> dict[str, str]:
    nonce = secrets.token_bytes(12)
    wrapped = AESGCM(kek).encrypt(nonce, dek, None)
    return {
        "wrapped_dek": base64.b64encode(wrapped).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }


def _unwrap_dek(slot: dict[str, Any], kek: bytes) -> bytes:
    wrapped = base64.b64decode(slot["wrapped_dek"])
    nonce = base64.b64decode(slot["nonce"])
    return AESGCM(kek).decrypt(nonce, wrapped, None)


# ---------- keystore I/O ----------


def _read_keystore() -> dict[str, Any] | None:
    if not KEYSTORE_PATH.exists():
        return None
    data: dict[str, Any] = json.loads(KEYSTORE_PATH.read_text())
    return data


def _write_keystore(keystore: dict[str, Any]) -> None:
    KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(KEYSTORE_PATH.parent, 0o700)
    KEYSTORE_PATH.write_text(json.dumps(keystore, indent=2) + "\n")
    os.chmod(KEYSTORE_PATH, 0o600)


# ---------- KEK providers ----------


def _keychain_tool() -> str | None:
    return shutil.which("security" if sys.platform == "darwin" else "secret-tool")


def _keychain_get() -> bytes | None:
    if sys.platform == "darwin":
        r = subprocess.run(
            ["security", "find-generic-password", "-s", _KC_SERVICE, "-a", _KC_ACCOUNT, "-w"],
            capture_output=True,
            text=True,
        )
    else:
        r = subprocess.run(
            ["secret-tool", "lookup", "service", _KC_SERVICE, "account", _KC_ACCOUNT],
            capture_output=True,
            text=True,
        )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return base64.b64decode(r.stdout.strip())


def _keychain_put(kek: bytes) -> None:
    b64 = base64.b64encode(kek).decode()
    if sys.platform == "darwin":
        # `security` only takes the secret as a -w argv arg (visible briefly in
        # the user's own `ps`); acceptable for a same-user, momentary store.
        r = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                _KC_SERVICE,
                "-a",
                _KC_ACCOUNT,
                "-w",
                b64,
            ],
            capture_output=True,
            text=True,
        )
    else:
        r = subprocess.run(
            [
                "secret-tool",
                "store",
                "--label=otaku",
                "service",
                _KC_SERVICE,
                "account",
                _KC_ACCOUNT,
            ],
            input=b64,
            capture_output=True,
            text=True,
        )
    if r.returncode != 0:
        raise CryptoError(f"keychain store failed: {r.stderr.strip() or r.returncode}")


def _disk_get_or_create() -> bytes:
    if DISK_KEK_PATH.exists():
        kek = DISK_KEK_PATH.read_bytes()
        if len(kek) != KEY_LEN:
            raise CryptoError(f"{DISK_KEK_PATH}: expected {KEY_LEN} bytes, found {len(kek)}")
        return kek
    DISK_KEK_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DISK_KEK_PATH.parent, 0o700)
    kek = secrets.token_bytes(KEY_LEN)
    DISK_KEK_PATH.write_bytes(kek)
    os.chmod(DISK_KEK_PATH, 0o600)
    return kek


def _passphrase_kek(salt: bytes, params: dict[str, int], *, confirm: bool) -> bytes:
    pw = getpass.getpass("otaku passphrase: ")
    if not pw:
        raise CryptoError("empty passphrase")
    if confirm and pw != getpass.getpass("confirm passphrase: "):
        raise CryptoError("passphrases do not match")
    return hashlib.scrypt(
        pw.encode(),
        salt=salt,
        n=params["n"],
        r=params["r"],
        p=params["p"],
        dklen=KEY_LEN,
        maxmem=128 * params["n"] * params["r"] * 2,
    )


def _command_get(retrieve_command: str) -> bytes:
    r = subprocess.run(retrieve_command, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        raise CryptoError(f"retrieve_command failed: {r.stderr.strip() or r.returncode}")
    try:
        kek = base64.b64decode(r.stdout.strip(), validate=True)
    except Exception as e:
        raise CryptoError("retrieve_command output is not valid base64") from e
    if len(kek) != KEY_LEN:
        raise CryptoError(f"retrieve_command returned {len(kek)} bytes, want {KEY_LEN}")
    return kek


# ---------- orchestration ----------


def _provision(enc: Encryption) -> dict[str, Any]:
    """Create a fresh slot (provider params + KEK) for first-run setup. Returns
    the slot dict augmented with an internal ``_kek`` for the caller to wrap with."""
    provider = enc.provider
    if provider == "keychain":
        if _keychain_tool() is None:
            print(
                f"otaku: no OS keychain tool found; storing the key in {DISK_KEK_PATH} "
                '(0600). Set [encryption].provider = "passphrase" for stronger protection.',
                file=sys.stderr,
            )
            return {"provider": "disk", "_kek": _disk_get_or_create()}
        kek = secrets.token_bytes(KEY_LEN)
        _keychain_put(kek)
        return {"provider": "keychain", "_kek": kek}
    if provider == "disk":
        return {"provider": "disk", "_kek": _disk_get_or_create()}
    if provider == "passphrase":
        salt = secrets.token_bytes(16)
        kek = _passphrase_kek(salt, _SCRYPT, confirm=True)
        return {
            "provider": "passphrase",
            "salt": base64.b64encode(salt).decode(),
            "scrypt": _SCRYPT,
            "_kek": kek,
        }
    if provider == "command":
        kek = secrets.token_bytes(KEY_LEN)
        print(
            "otaku: store this key where your retrieve_command will read it "
            f"(base64):\n  {base64.b64encode(kek).decode()}",
            file=sys.stderr,
        )
        return {"provider": "command", "_kek": kek}
    raise CryptoError(f"unknown encryption provider {provider!r}")


def _kek_for_slot(slot: dict[str, Any], enc: Encryption) -> bytes:
    provider = slot.get("provider")
    if provider == "keychain":
        kek = _keychain_get()
        if kek is None:
            raise CryptoError("key not found in OS keychain")
        return kek
    if provider == "disk":
        return _disk_get_or_create()
    if provider == "passphrase":
        return _passphrase_kek(
            base64.b64decode(slot["salt"]), slot.get("scrypt", _SCRYPT), confirm=False
        )
    if provider == "command":
        if not enc.retrieve_command:
            raise CryptoError("provider 'command' needs [encryption].retrieve_command")
        return _command_get(enc.retrieve_command)
    raise CryptoError(f"unknown provider in keystore: {provider!r}")


def unlock(enc: Encryption) -> Cipher:
    """Return a Cipher keyed by the DEK, creating the keystore on first run."""
    keystore = _read_keystore()
    if keystore is None:
        dek = secrets.token_bytes(KEY_LEN)
        slot = _provision(enc)
        kek = slot.pop("_kek")
        slot.update(_wrap_dek(dek, kek))
        _write_keystore({"version": 1, "slots": [slot]})
        LEGACY_KEYFILE.unlink(missing_ok=True)  # ~/.otaku only
        return Cipher(dek)

    slots: list[dict[str, Any]] = keystore.get("slots", [])
    # Try the configured provider's slot first, then any other.
    slots.sort(key=lambda s: 0 if s.get("provider") == enc.provider else 1)
    errors: list[str] = []
    for slot in slots:
        try:
            dek = _unwrap_dek(slot, _kek_for_slot(slot, enc))
        except (CryptoError, InvalidTag, ValueError) as e:
            errors.append(f"{slot.get('provider')}: {e}")
            continue
        return Cipher(dek)
    raise CryptoError("could not unlock encryption key (" + "; ".join(errors) + ")")
