"""The sealing ciphers.

`Cipher` is AES-256-GCM: what one key seals, only that key unseals, and a
tampered or truncated value is refused rather than misread. `PlainCipher`
(provider "none") is a passthrough — bytes stored exactly as given.
"""

import pytest
from cryptography.exceptions import InvalidTag

from otaku.crypto import Cipher, CryptoError, PlainCipher

KEY = b"k" * 32
OTHER_KEY = b"o" * 32


class TestCipher:
    def test_roundtrips(self) -> None:
        cipher = Cipher(KEY)
        assert cipher.unseal(cipher.seal("привет".encode())) == "привет".encode()

    def test_sealed_bytes_are_not_the_plaintext(self) -> None:
        assert b"secret" not in Cipher(KEY).seal(b"secret")

    def test_sealing_twice_differs(self) -> None:
        cipher = Cipher(KEY)
        assert cipher.seal(b"same") != cipher.seal(b"same")

    def test_the_wrong_key_is_refused(self) -> None:
        sealed = Cipher(KEY).seal(b"secret")
        with pytest.raises(InvalidTag):
            Cipher(OTHER_KEY).unseal(sealed)

    def test_a_truncated_value_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Cipher(KEY).unseal(b"short")

    def test_a_wrong_sized_key_is_refused(self) -> None:
        with pytest.raises(CryptoError):
            Cipher(b"tiny")


class TestPlainCipher:
    def test_seal_is_a_passthrough(self) -> None:
        assert PlainCipher().seal(b"visible") == b"visible"

    def test_unseal_is_a_passthrough(self) -> None:
        assert PlainCipher().unseal(b"visible") == b"visible"
