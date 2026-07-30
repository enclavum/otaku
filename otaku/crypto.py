"""Content encryption: the cipher and the key ceremony.

A random 32-byte data-encryption key (DEK) seals every content value with
AES-256-GCM. A sealed value is ONE opaque blob — the 12-byte nonce followed
by the ciphertext — so it fits a single column or field and can never be
half-persisted. The DEK is never stored in the clear: it is wrapped by a
key-encryption key (KEK) and the wrapped form lives in configs/keys.toml.

The KEK comes from the `KekProvider` named in [encryption] — see the
subclasses at the bottom of this module: keychain, command, passphrase,
disk. Provider "none" bypasses all of it: `unlock` returns the plain-text
passthrough and no keystore is read or written.

Several keystore slots can wrap the same DEK, so switching providers is a
re-wrap, never a re-encryption. Provisioning is strictly additive: a KEK the
provider already holds is reused, and no key material is ever overwritten —
keys.toml is written with exclusive create, the keychain item is add-only.
Back up keys.toml TOGETHER with its KEK; either alone is useless, and losing
the KEK makes sealed content permanently unreadable.
"""

import base64
import getpass
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tomllib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from otaku.paths import Paths
from otaku.settings.config import Encryption
from otaku.settings.files import toml_scalar

_KEY_LEN = 32
_NONCE_LEN = 12

_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class CryptoError(Exception):
    pass


class Cipher:
    """AES-256-GCM sealing keyed by the DEK."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_LEN:
            raise CryptoError(f"key is {len(key)} bytes, want {_KEY_LEN}")
        self._aead = AESGCM(key)

    def seal(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(_NONCE_LEN)
        return nonce + self._aead.encrypt(nonce, plaintext, None)

    def unseal(self, sealed: bytes) -> bytes:
        if len(sealed) < _NONCE_LEN:
            raise ValueError("sealed value is too short to hold a nonce")
        return self._aead.decrypt(sealed[:_NONCE_LEN], sealed[_NONCE_LEN:], None)


class PlainCipher(Cipher):
    """Passthrough for [encryption].provider = "none": content is stored as
    readable plain text, and the database opens in any sqlite browser."""

    def __init__(self) -> None:
        super().__init__(b"\x00" * _KEY_LEN)

    def seal(self, plaintext: bytes) -> bytes:
        return plaintext

    def unseal(self, sealed: bytes) -> bytes:
        return sealed


def unlock(enc: Encryption, paths: Paths) -> Cipher:
    """Return the session cipher. Provider "none" short-circuits to the
    plain-text passthrough. Otherwise the keystore is read and the DEK
    unwrapped with the provider's KEK — or, when no keystore exists yet, a
    fresh DEK is minted and the keystore written (first enable)."""
    if enc.provider == "none":
        return PlainCipher()
    if enc.provider not in KEK_PROVIDERS:
        raise CryptoError(f"unknown encryption provider {enc.provider!r}")

    keystore = Keystore(paths.keys_file)
    if not keystore.exists():
        dek = secrets.token_bytes(_KEY_LEN)
        slot, kek = KEK_PROVIDERS[enc.provider](enc, paths).provision()
        slot.update(_wrap_dek(dek, kek))
        keystore.write([slot])
        return Cipher(dek)

    slots = keystore.slots()
    slots.sort(key=lambda s: 0 if s.get("provider") == enc.provider else 1)
    errors: list[str] = []
    for slot in slots:
        name = str(slot.get("provider"))
        cls = KEK_PROVIDERS.get(name)
        if cls is None:
            errors.append(f"unknown provider {name!r} in keystore")
            continue
        try:
            dek = _unwrap_dek(slot, cls(enc, paths).retrieve(slot))
        except (CryptoError, InvalidTag, ValueError) as e:
            # InvalidTag stringifies to "" — say what it means instead.
            reason = str(e) or "the retrieved key does not unwrap this keystore"
            errors.append(f"{name}: {reason}" if len(slots) > 1 else reason)
            continue
        return Cipher(dek)
    raise CryptoError("; ".join(errors) or f"{paths.keys_file} has no slots")


# ---------- DEK wrapping ----------


def _wrap_dek(dek: bytes, kek: bytes) -> dict[str, str]:
    nonce = secrets.token_bytes(_NONCE_LEN)
    wrapped = AESGCM(kek).encrypt(nonce, dek, None)
    return {
        "wrapped_dek": base64.b64encode(wrapped).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }


def _unwrap_dek(slot: dict[str, Any], kek: bytes) -> bytes:
    wrapped = base64.b64decode(slot["wrapped_dek"])
    nonce = base64.b64decode(slot["nonce"])
    return AESGCM(kek).decrypt(nonce, wrapped, None)


# ---------- the keystore ----------


class Keystore:
    """The keys.toml file: the wrapped DEK and the KEK slot(s) that open it.

    Written with exclusive create (0600, in a 0700 dir) — an existing
    keystore is never overwritten, because the wrapped DEK it holds is the
    only copy of the key the sealed content was written with."""

    _VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path

    def exists(self) -> bool:
        return self._path.exists()

    def slots(self) -> list[dict[str, Any]]:
        raw = tomllib.loads(self._path.read_text())
        slots = raw.get("slots", [])
        return list(slots) if isinstance(slots, list) else []

    def write(self, slots: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        try:
            # Born 0600: never a moment (or a crash residue) at umask perms.
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as e:
            raise CryptoError(f"{self._path} already exists; refusing to overwrite") from e
        with os.fdopen(fd, "w") as f:
            f.write(self._toml(slots))

    def _toml(self, slots: list[dict[str, Any]]) -> str:
        out = [
            "# otaku keystore — the wrapped data key and the KEK slot(s) that open it.",
            "# Back this up TOGETHER with the KEK; either alone is useless.",
            "",
            f"version = {toml_scalar(self._VERSION)}",
        ]
        for slot in slots:
            out += ["", "[[slots]]"]
            out += [f"{key} = {self._value(value)}" for key, value in slot.items()]
        return "\n".join(out) + "\n"

    @staticmethod
    def _value(value: Any) -> str:
        if isinstance(value, dict):
            inner = ", ".join(f"{k} = {toml_scalar(v)}" for k, v in value.items())
            return "{ " + inner + " }"
        return toml_scalar(value)


# ---------- KEK providers ----------


class KekProvider(ABC):
    """One way to obtain the key-encryption key.

    `provision` runs once, when encryption is first enabled: it returns the
    keystore slot describing this provider plus the KEK to wrap the fresh DEK
    with — reusing key material the provider already holds, never overwriting
    any. `retrieve` runs on every later launch and fetches the KEK the given
    slot needs."""

    name: ClassVar[str]

    def __init__(self, enc: Encryption, paths: Paths) -> None:
        self._enc = enc
        self._paths = paths

    @abstractmethod
    def provision(self) -> tuple[dict[str, Any], bytes]: ...

    @abstractmethod
    def retrieve(self, slot: dict[str, Any]) -> bytes: ...


class KeychainKek(KekProvider):
    """KEK in the OS keychain (macOS `security`, Linux `secret-tool`). The
    item is named per state dir, so parallel setups never share — or
    clobber — each other's key."""

    name = "keychain"
    _ACCOUNT = "kek"

    def provision(self) -> tuple[dict[str, Any], bytes]:
        if self._tool() is None:
            raise CryptoError(
                "no OS keychain tool found (`security` / `secret-tool`); "
                'use provider = "passphrase", "command", or "disk"'
            )
        kek = self._get()
        if kek is not None and len(kek) != _KEY_LEN:
            raise CryptoError(
                f"keychain item {self._service()!r} holds {len(kek)} bytes, "
                f"want {_KEY_LEN}; refusing to overwrite it"
            )
        if kek is None:
            kek = secrets.token_bytes(_KEY_LEN)
            self._put(kek)
        return {"provider": self.name}, kek

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        kek = self._get()
        if kek is None:
            raise CryptoError("key not found in the OS keychain")
        return kek

    def _service(self) -> str:
        return f"otaku:{self._paths.root}"

    @staticmethod
    def _tool() -> str | None:
        return shutil.which("security" if sys.platform == "darwin" else "secret-tool")

    def _get(self) -> bytes | None:
        if sys.platform == "darwin":
            args = ["security", "find-generic-password"]
            args += ["-s", self._service(), "-a", self._ACCOUNT, "-w"]
            result = subprocess.run(args, capture_output=True, text=True)
        else:
            args = ["secret-tool", "lookup", "service", self._service(), "account", self._ACCOUNT]
            result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return base64.b64decode(result.stdout.strip())

    def _put(self, kek: bytes) -> None:
        encoded = base64.b64encode(kek).decode()
        if sys.platform == "darwin":
            # `security` takes the secret only as an argv arg (briefly visible
            # in the user's own `ps`) — acceptable for a same-user, momentary
            # store. No -U: add-only, so an existing item is never updated.
            args = ["security", "add-generic-password"]
            args += ["-s", self._service(), "-a", self._ACCOUNT, "-w", encoded]
            result = subprocess.run(args, capture_output=True, text=True)
        else:
            args = ["secret-tool", "store", f"--label={self._service()}"]
            args += ["service", self._service(), "account", self._ACCOUNT]
            result = subprocess.run(args, input=encoded, capture_output=True, text=True)
        if result.returncode != 0:
            reason = result.stderr.strip() or result.returncode
            raise CryptoError(f"keychain store failed: {reason}")


class CommandKek(KekProvider):
    """KEK printed to stdout by [encryption].retrieve_command (1Password,
    pass, a hardware-token script — anything). The user stores the secret;
    otaku only reads it."""

    name = "command"

    def provision(self) -> tuple[dict[str, Any], bytes]:
        if self._enc.retrieve_command:
            try:
                return {"provider": self.name}, self._run()
            except CryptoError:
                pass  # nothing stored yet — a true first enable; mint below
        kek = secrets.token_bytes(_KEY_LEN)
        print(
            "otaku: store this key where your retrieve_command will read it "
            f"(base64):\n  {base64.b64encode(kek).decode()}",
            file=sys.stderr,
        )
        return {"provider": self.name}, kek

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        return self._run()

    def _run(self) -> bytes:
        command = self._enc.retrieve_command
        if not command:
            raise CryptoError('provider "command" needs [encryption].retrieve_command')
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise CryptoError(
                f"retrieve_command failed: {result.stderr.strip() or result.returncode}"
            )
        try:
            kek = base64.b64decode(result.stdout.strip(), validate=True)
        except Exception as e:
            raise CryptoError("retrieve_command output is not valid base64") from e
        if len(kek) != _KEY_LEN:
            raise CryptoError(f"retrieve_command returned {len(kek)} bytes, want {_KEY_LEN}")
        return kek


class PassphraseKek(KekProvider):
    """KEK derived from a passphrase via scrypt — nothing is stored anywhere;
    the passphrase is asked at every launch."""

    name = "passphrase"

    def provision(self) -> tuple[dict[str, Any], bytes]:
        salt = secrets.token_bytes(16)
        kek = self._derive(salt, _SCRYPT, confirm=True)
        slot = {
            "provider": self.name,
            "salt": base64.b64encode(salt).decode(),
            "scrypt": dict(_SCRYPT),
        }
        return slot, kek

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        return self._derive(
            base64.b64decode(slot["salt"]), slot.get("scrypt", _SCRYPT), confirm=False
        )

    @staticmethod
    def _derive(salt: bytes, params: dict[str, int], *, confirm: bool) -> bytes:
        passphrase = getpass.getpass("otaku passphrase: ")
        if not passphrase:
            raise CryptoError("empty passphrase")
        if confirm and passphrase != getpass.getpass("confirm passphrase: "):
            raise CryptoError("passphrases do not match")
        return hashlib.scrypt(
            passphrase.encode(),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=_KEY_LEN,
            maxmem=128 * params["n"] * params["r"] * 2,
        )


class DiskKek(KekProvider):
    """KEK in configs/kek.key (0600). Protects nothing against a local
    reader, but keeps the database file itself opaque."""

    name = "disk"

    def provision(self) -> tuple[dict[str, Any], bytes]:
        return {"provider": self.name}, self._get_or_create()

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        return self._get_or_create()

    def _get_or_create(self) -> bytes:
        path = self._paths.kek_file
        if path.exists():
            kek = path.read_bytes()
            if len(kek) != _KEY_LEN:
                raise CryptoError(f"{path}: expected {_KEY_LEN} bytes, found {len(kek)}")
            return kek
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        kek = secrets.token_bytes(_KEY_LEN)
        # Born 0600: never a moment (or a crash residue) at umask perms.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(kek)
        return kek


KEK_PROVIDERS: dict[str, type[KekProvider]] = {
    cls.name: cls for cls in (KeychainKek, CommandKek, PassphraseKek, DiskKek)
}
