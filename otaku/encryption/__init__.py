"""Content encryption — a dependency-free library with TWO key planes,
deliberately unable to read each other's keys:

- `data` — what seals the user's data: the database and the request log
  (one cipher for both, by design) — the DEK/KEK ceremony over keys.toml.
- `keys` — what seals api keys inside providers.toml: its own key, its
  own tokens, its own error.

Beneath both, `_primitives` (aead, keychain, keyfile): shared mechanism
holding no keys of its own. This package knows nothing of the
application: every path and label is handed in, and the passphrase
arrives through an `ask` callable. Key WIRING happens only in
`backend.launch`.
"""

from otaku.encryption._primitives.aead import Cipher, EncryptionError, PlainCipher
from otaku.encryption._primitives.keychain import purge_items as purge_keychain_items
from otaku.encryption.data import AskSecret, Keystore, unlock
from otaku.encryption.keys import SealedError, is_sealed, opener, seal, unseal

__all__ = [
    "AskSecret",
    "Cipher",
    "EncryptionError",
    "Keystore",
    "PlainCipher",
    "SealedError",
    "is_sealed",
    "opener",
    "purge_keychain_items",
    "seal",
    "unlock",
    "unseal",
]
