"""PLANE 1 — the DATA cipher: what seals the database and the request
log (the same cipher for both, by design: the log must protect exactly
what the database protects).

A random data-encryption key (DEK) seals content; the DEK is never
stored in the clear — it is wrapped by a key-encryption key (KEK) from
the provider named in the configuration, and the wrapped form lives in
the keystore (keys.toml). Several slots can wrap one DEK, so switching
providers is a re-wrap, never a re-encryption. Provisioning is strictly
additive. This plane and the api-key plane (`crypto.keys`) share the
primitives and nothing else — neither can read the other's keys.
"""

import base64
import hashlib
import os
import secrets
import subprocess
import sys
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from cryptography.exceptions import InvalidTag

from otaku.encryption._primitives import keychain, keyfile
from otaku.encryption._primitives.aead import (
    KEY_LEN,
    Cipher,
    EncryptionError,
    PlainCipher,
    unwrap_key,
    wrap_key,
)
from otaku.formatting import toml_scalar

# Answers a passphrase prompt: ask(prompt) -> the secret. The frontend
# supplies it at composition; crypto never reads stdin itself.
AskSecret = Callable[[str], str]

_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_KEK_ACCOUNT = "kek"  # the keychain account; the api-key plane uses "config"


class Keystore:
    """keys.toml: the wrapped DEK and its KEK slots. Written with
    exclusive create (0600, in a 0700 dir) — an existing keystore is
    never overwritten, because the wrapped DEK it holds is the only copy
    of the key the sealed content was written with."""

    _VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path

    def exists(self) -> bool:
        return self._path.exists()

    def slots(self) -> list[dict[str, Any]]:
        """Raises OSError/ValueError on an unreadable file."""
        raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        slots = raw.get("slots", [])
        return list(slots) if isinstance(slots, list) else []

    def write(self, slots: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        try:
            # Born 0600: never a moment (or a crash residue) at umask perms.
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as e:
            raise EncryptionError(f"{self._path} already exists; refusing to overwrite") from e
        with os.fdopen(fd, "w", encoding="utf-8") as f:
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


def unlock(
    provider: str,
    *,
    keys_file: Path,
    kek_file: Path,
    service: str,
    retrieve_command: str | None = None,
    ask: AskSecret | None = None,
) -> Cipher:
    """The session cipher. Provider "none" short-circuits to the plain
    passthrough; otherwise the keystore is read and the DEK unwrapped
    with the named provider's KEK — or, when no keystore exists, a fresh
    DEK is minted and the keystore written (first enable). `service`
    names the keychain item; `ask` answers the passphrase provider (a
    passphrase needed with no `ask` is a EncryptionError). Raises EncryptionError
    with a curated message for every refusal."""
    if provider == "none":
        return PlainCipher()
    providers = _providers(
        kek_file=kek_file, service=service, retrieve_command=retrieve_command, ask=ask
    )
    if provider not in providers:
        raise EncryptionError(f"unknown encryption provider {provider!r}")

    keystore = Keystore(keys_file)
    if not keystore.exists():
        dek = secrets.token_bytes(KEY_LEN)
        slot, kek = providers[provider].provision()
        slot.update(wrap_key(dek, kek))
        keystore.write([slot])
        return Cipher(dek)

    try:
        slots = keystore.slots()
    except (OSError, ValueError) as e:
        # tomllib's decode error is a ValueError: a hand-edited or corrupt
        # keystore gets the curated message, never a traceback.
        raise EncryptionError(f"{keys_file} is unreadable ({e}); restore it from backup") from e
    slots.sort(key=lambda s: 0 if s.get("provider") == provider else 1)
    errors: list[str] = []
    for slot in slots:
        name = str(slot.get("provider"))
        kek_provider = providers.get(name)
        if kek_provider is None:
            errors.append(f"unknown provider {name!r} in keystore")
            continue
        try:
            dek = unwrap_key(slot, kek_provider.retrieve(slot))
        except (EncryptionError, InvalidTag, ValueError, KeyError, TypeError) as e:
            # InvalidTag stringifies to "" — say what it means instead. A
            # slot missing a field (KeyError) or holding the wrong type
            # is named the same way, never a traceback. One slot answers
            # for itself; only a choice of them needs its name.
            if isinstance(e, KeyError):
                reason = f"slot is missing {e}"
            else:
                reason = str(e) or "the retrieved key does not unwrap this keystore"
            errors.append(f"{name}: {reason}" if len(slots) > 1 else reason)
            continue
        return Cipher(dek)
    raise EncryptionError("; ".join(errors) or f"{keys_file} has no slots")


# ---------- KEK providers ----------


class KekProvider(ABC):
    """One way to obtain the key-encryption key. `provision` runs once,
    when encryption is first enabled: the keystore slot describing this
    provider plus the KEK to wrap the fresh DEK with — reusing key
    material the provider already holds, never overwriting any.
    `retrieve` runs on every later launch."""

    name: ClassVar[str]

    @abstractmethod
    def provision(self) -> tuple[dict[str, Any], bytes]: ...

    @abstractmethod
    def retrieve(self, slot: dict[str, Any]) -> bytes: ...


class KeychainKek(KekProvider):
    """KEK in the OS keychain, the item named per home folder so parallel
    setups never share — or clobber — each other's key."""

    name = "keychain"

    def __init__(self, service: str) -> None:
        self._service = service

    def provision(self) -> tuple[dict[str, Any], bytes]:
        if not keychain.available():
            raise EncryptionError(
                "no OS keychain tool found (`security` / `secret-tool`); "
                'use provider = "passphrase", "command", or "disk"'
            )
        kek = keychain.get(self._service, _KEK_ACCOUNT)
        if kek is not None and len(kek) != KEY_LEN:
            raise EncryptionError(
                f"keychain item {self._service!r} holds {len(kek)} bytes, "
                f"want {KEY_LEN}; refusing to overwrite it"
            )
        if kek is None:
            kek = secrets.token_bytes(KEY_LEN)
            keychain.put(self._service, _KEK_ACCOUNT, kek)
        return {"provider": self.name}, kek

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        kek = keychain.get(self._service, _KEK_ACCOUNT)
        if kek is None:
            raise EncryptionError("key not found in the OS keychain")
        return kek


class CommandKek(KekProvider):
    """KEK printed to stdout by the configured retrieve_command
    (1Password, pass, a hardware-token script — anything). The user
    stores the secret; otaku only reads it."""

    name = "command"

    def __init__(self, retrieve_command: str | None) -> None:
        self._command = retrieve_command

    def provision(self) -> tuple[dict[str, Any], bytes]:
        if self._command:
            try:
                return {"provider": self.name}, self._run()
            except EncryptionError:
                pass  # nothing stored yet — a true first enable; mint below
        kek = secrets.token_bytes(KEY_LEN)
        print(
            "otaku: store this key where your retrieve_command will read it "
            f"(base64):\n  {base64.b64encode(kek).decode()}",
            file=sys.stderr,
        )
        return {"provider": self.name}, kek

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        return self._run()

    def _run(self) -> bytes:
        if not self._command:
            raise EncryptionError('provider "command" needs [encryption].retrieve_command')
        result = subprocess.run(self._command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise EncryptionError(
                f"retrieve_command failed: {result.stderr.strip() or result.returncode}"
            )
        try:
            kek = base64.b64decode(result.stdout.strip(), validate=True)
        except Exception as e:
            raise EncryptionError("retrieve_command output is not valid base64") from e
        if len(kek) != KEY_LEN:
            raise EncryptionError(f"retrieve_command returned {len(kek)} bytes, want {KEY_LEN}")
        return kek


class PassphraseKek(KekProvider):
    """KEK derived from a passphrase via scrypt — nothing is stored
    anywhere; the passphrase is asked at every launch, through the
    injected `ask`."""

    name = "passphrase"

    def __init__(self, ask: AskSecret | None) -> None:
        self._ask = ask

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

    def _derive(self, salt: bytes, params: dict[str, int], *, confirm: bool) -> bytes:
        if self._ask is None:
            raise EncryptionError(
                "a passphrase is needed and nothing can ask for one here "
                "(headless launch over a passphrase-encrypted store)"
            )
        passphrase = self._ask("otaku passphrase: ")
        if not passphrase:
            raise EncryptionError("empty passphrase")
        if confirm and passphrase != self._ask("confirm passphrase: "):
            raise EncryptionError("passphrases do not match")
        return hashlib.scrypt(
            passphrase.encode(),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=KEY_LEN,
            maxmem=128 * params["n"] * params["r"] * 2,
        )


class DiskKek(KekProvider):
    """KEK in the given key file (0600). Protects nothing against a local
    reader, but keeps the database file itself opaque."""

    name = "disk"

    def __init__(self, kek_file: Path) -> None:
        self._kek_file = kek_file

    def provision(self) -> tuple[dict[str, Any], bytes]:
        return {"provider": self.name}, keyfile.read_or_create(self._kek_file, KEY_LEN)

    def retrieve(self, slot: dict[str, Any]) -> bytes:
        return keyfile.read_or_create(self._kek_file, KEY_LEN)


def _providers(
    *, kek_file: Path, service: str, retrieve_command: str | None, ask: AskSecret | None
) -> dict[str, KekProvider]:
    built: tuple[KekProvider, ...] = (
        KeychainKek(service),
        CommandKek(retrieve_command),
        PassphraseKek(ask),
        DiskKek(kek_file),
    )
    return {p.name: p for p in built}
