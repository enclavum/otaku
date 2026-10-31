"""The OS keychain item (macOS `security`, Linux `secret-tool`).

Add-only by design: an existing item is never updated, so no caller can
clobber key material. Items are removed only all at once, every one
under a service (`purge_items`), when its state dir is purged.
"""

import base64
import shutil
import subprocess
import sys

from otaku.encryption._primitives.aead import EncryptionError

# More than a service ever holds — a bound on the macOS purge's loop.
_MOST_ITEMS = 16


def available() -> bool:
    """Whether this machine has a keychain tool at all."""
    return _tool() is not None


def get(service: str, account: str) -> bytes | None:
    """The stored secret, or None when absent. Raises EncryptionError on an
    item that is not valid base64."""
    if sys.platform == "darwin":
        args = ["security", "find-generic-password", "-s", service, "-a", account, "-w"]
    else:
        args = ["secret-tool", "lookup", "service", service, "account", account]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError as e:
        raise EncryptionError(f"keychain item {service!r} is not valid base64 ({e})") from e


def put(service: str, account: str, secret: bytes) -> None:
    """Store a new secret. Raises EncryptionError when the tool refuses."""
    encoded = base64.b64encode(secret).decode()
    if sys.platform == "darwin":
        # `security` takes the secret only as an argv arg (briefly visible
        # in the user's own `ps`) — acceptable for a same-user, momentary
        # store. No -U: add-only, so an existing item is never updated.
        args = ["security", "add-generic-password", "-s", service, "-a", account, "-w", encoded]
        result = subprocess.run(args, capture_output=True, text=True)
    else:
        args = ["secret-tool", "store", f"--label={service}"]
        args += ["service", service, "account", account]
        result = subprocess.run(args, input=encoded, capture_output=True, text=True)
    if result.returncode != 0:
        reason = result.stderr.strip() or result.returncode
        raise EncryptionError(f"keychain store failed: {reason}")


def purge_items(service: str) -> None:
    """Remove every item under `service`, whatever its account; nothing
    where there is none, or no keychain tool."""
    if _tool() is None:
        return
    if sys.platform == "darwin":
        # `security` deletes the first match per call: called until none is left
        for _ in range(_MOST_ITEMS):
            args = ["security", "delete-generic-password", "-s", service]
            if subprocess.run(args, capture_output=True, text=True).returncode != 0:
                break
    else:
        args = ["secret-tool", "clear", "service", service]
        subprocess.run(args, capture_output=True, text=True)


def _tool() -> str | None:
    return shutil.which("security" if sys.platform == "darwin" else "secret-tool")
