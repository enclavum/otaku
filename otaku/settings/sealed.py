"""Sealed config values: api keys encrypted inside config.toml.

A sealed value is `sealed:` + base64 of nonce-plus-ciphertext under
AES-256-GCM, keyed by a 32-byte sealing key that never sits beside the
config: it lives in the OS keychain (macOS `security`, Linux
`secret-tool`), named per state dir. When configs/config.key exists —
created by hand, or automatically where no keychain tool is available —
that file is the key instead, and the config is then opaque only away
from this machine. An existing key, wherever it is, is always reused.

This plane is wholly separate from the database encryption (`crypto`):
[encryption].provider = "none" leaves api keys sealed all the same, and
neither plane can read the other's key material.
"""

import base64
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from otaku.paths import Paths
from otaku.settings.config import Provider

_PREFIX = "sealed:"
_KEY_LEN = 32
_NONCE_LEN = 12
_ACCOUNT = "config"  # the keychain account; crypto's KEK item uses "kek"


class SealedError(Exception):
    pass


def is_sealed(value: str) -> bool:
    """Whether a config value is a sealed token rather than plain text."""
    return value.startswith(_PREFIX)


def seal(paths: Paths, plaintext: str) -> str:
    """`plaintext` as a sealed token for config.toml, minting the sealing
    key on first use. Raises SealedError when the key can be neither
    found nor stored."""
    key = _load_key(paths, create=True)
    if key is None:
        raise SealedError("no sealing key and no way to create one")
    nonce = secrets.token_bytes(_NONCE_LEN)
    blob = nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return _PREFIX + base64.b64encode(blob).decode()


def unseal(paths: Paths, value: str) -> str:
    """The plain text behind a sealed token (plain text passes through).
    Raises SealedError when the sealing key is gone or the token does not
    decrypt with it."""
    if not is_sealed(value):
        return value
    key = _load_key(paths, create=False)
    if key is None:
        raise SealedError("the sealing key is in neither the OS keychain nor configs/config.key")
    return _opened(key, value)


def resolve_api_keys(
    paths: Paths, providers: dict[str, Provider]
) -> tuple[dict[str, Provider], list[str]]:
    """The providers with sealed api keys opened for the session, plus a
    warning line per key that would not open — its provider keeps an
    empty key, so requests go out unauthenticated rather than with a
    dead token. The sealing key is fetched once for the whole pass — a
    launch never asks the OS keychain per provider."""
    resolved: dict[str, Provider] = {}
    warnings: list[str] = []
    key: bytes | None = None
    fetched = False
    for name, provider in providers.items():
        if is_sealed(provider.api_key):
            if not fetched:
                key, fetched = _load_key(paths, create=False), True
            try:
                if key is None:
                    raise SealedError(
                        "the sealing key is in neither the OS keychain nor configs/config.key"
                    )
                provider = replace(provider, api_key=_opened(key, provider.api_key))
            except SealedError as e:
                warnings.append(
                    f"The api key for {name!r} cannot be unsealed ({e}); "
                    "enter it again in the model picker."
                )
                provider = replace(provider, api_key="")
        resolved[name] = provider
    return resolved, warnings


def _opened(key: bytes, value: str) -> str:
    """A sealed token decrypted with `key`; SealedError when it will not."""
    try:
        blob = base64.b64decode(value[len(_PREFIX) :], validate=True)
        if len(blob) < _NONCE_LEN:
            raise ValueError("token too short to hold a nonce")
        return AESGCM(key).decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], None).decode()
    except Exception as e:
        raise SealedError("the sealed value does not decrypt with the sealing key") from e


# ---------- the sealing key ----------


def _load_key(paths: Paths, *, create: bool) -> bytes | None:
    """The sealing key: the on-disk file when it exists, else the OS
    keychain — minted there on first `create` when a keychain tool is
    available, in the file otherwise. None when absent and not creating,
    or when there is nowhere to create it."""
    file = paths.config_key_file
    if file.exists():
        key = file.read_bytes()
        if len(key) != _KEY_LEN:
            raise SealedError(f"{file}: expected {_KEY_LEN} bytes, found {len(key)}")
        return key
    if _keychain_tool() is not None:
        found = _keychain_get(paths)
        if found is not None or not create:
            return found
        minted = secrets.token_bytes(_KEY_LEN)
        _keychain_put(paths, minted)
        return minted
    if not create:
        return None
    return _create_key_file(file)


def _create_key_file(file: Path) -> bytes:
    file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_KEY_LEN)
    # Born 0600: never a moment (or a crash residue) at umask perms.
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _service(paths: Paths) -> str:
    return f"otaku:{paths.root}"


def _keychain_tool() -> str | None:
    return shutil.which("security" if sys.platform == "darwin" else "secret-tool")


def _keychain_get(paths: Paths) -> bytes | None:
    if sys.platform == "darwin":
        args = ["security", "find-generic-password"]
        args += ["-s", _service(paths), "-a", _ACCOUNT, "-w"]
        result = subprocess.run(args, capture_output=True, text=True)
    else:
        args = ["secret-tool", "lookup", "service", _service(paths), "account", _ACCOUNT]
        result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return base64.b64decode(result.stdout.strip())


def _keychain_put(paths: Paths, key: bytes) -> None:
    encoded = base64.b64encode(key).decode()
    if sys.platform == "darwin":
        # `security` takes the secret only as an argv arg (briefly visible
        # in the user's own `ps`) — acceptable for a same-user, momentary
        # store. No -U: add-only, so an existing item is never updated.
        args = ["security", "add-generic-password"]
        args += ["-s", _service(paths), "-a", _ACCOUNT, "-w", encoded]
        result = subprocess.run(args, capture_output=True, text=True)
    else:
        args = ["secret-tool", "store", f"--label={_service(paths)}"]
        args += ["service", _service(paths), "account", _ACCOUNT]
        result = subprocess.run(args, input=encoded, capture_output=True, text=True)
    if result.returncode != 0:
        reason = result.stderr.strip() or result.returncode
        raise SealedError(f"keychain store failed: {reason}")
