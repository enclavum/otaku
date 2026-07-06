"""Tests for the envelope-encryption layer.

This guards unrecoverable user data, so it gets exhaustive coverage: the AEAD
cipher, DEK wrapping, every KEK provider's first-run + reopen path, multi-slot
resilience, and the failure modes that must raise rather than silently corrupt.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from otaku.config import Encryption
from otaku.storage import crypto
from otaku.storage.crypto import (
    Cipher,
    CryptoError,
    _command_get,
    _unwrap_dek,
    _wrap_dek,
    unlock,
)


class TestCipher:
    def test_roundtrip(self, cipher: Cipher) -> None:
        ct, nonce = cipher.seal(b"hello world")
        assert cipher.unseal(ct, nonce) == b"hello world"

    def test_nonce_is_12_bytes_and_unique(self, cipher: Cipher) -> None:
        _, n1 = cipher.seal(b"x")
        _, n2 = cipher.seal(b"x")
        assert len(n1) == 12
        assert n1 != n2  # fresh nonce each call

    def test_ciphertext_differs_per_call(self, cipher: Cipher) -> None:
        c1, _ = cipher.seal(b"x")
        c2, _ = cipher.seal(b"x")
        assert c1 != c2

    def test_wrong_key_length_raises(self) -> None:
        with pytest.raises(CryptoError, match="key length"):
            Cipher(b"tooshort")

    def test_tampered_ciphertext_fails(self, cipher: Cipher) -> None:
        from cryptography.exceptions import InvalidTag

        ct, nonce = cipher.seal(b"secret")
        with pytest.raises(InvalidTag):
            # flip (not zero) the first byte so tampering is guaranteed even
            # when ct[0] already happens to be 0x00
            cipher.unseal(bytes([ct[0] ^ 0xFF]) + ct[1:], nonce)

    def test_wrong_nonce_fails(self, cipher: Cipher) -> None:
        from cryptography.exceptions import InvalidTag

        ct, _ = cipher.seal(b"secret")
        with pytest.raises(InvalidTag):
            cipher.unseal(ct, b"\x00" * 12)


class TestWrap:
    def test_wrap_unwrap_roundtrip(self) -> None:
        dek = b"d" * 32
        kek = b"k" * 32
        slot = _wrap_dek(dek, kek)
        assert set(slot) == {"wrapped_dek", "nonce"}
        assert _unwrap_dek(slot, kek) == dek

    def test_unwrap_with_wrong_kek_fails(self) -> None:
        from cryptography.exceptions import InvalidTag

        slot = _wrap_dek(b"d" * 32, b"k" * 32)
        with pytest.raises(InvalidTag):
            _unwrap_dek(slot, b"x" * 32)


class TestUnlockFirstRun:
    def test_disk_provider_creates_keystore_and_kek(self) -> None:
        cipher = unlock(Encryption(provider="disk"))
        assert isinstance(cipher, Cipher)
        assert crypto.KEYSTORE_PATH.exists()
        assert crypto.DISK_KEK_PATH.exists()
        import json

        ks = json.loads(crypto.KEYSTORE_PATH.read_text())
        assert ks["version"] == 1
        assert ks["slots"][0]["provider"] == "disk"

    def test_keychain_provider_uses_in_memory_keychain(self) -> None:
        cipher = unlock(Encryption(provider="keychain"))
        assert isinstance(cipher, Cipher)
        import json

        ks = json.loads(crypto.KEYSTORE_PATH.read_text())
        assert ks["slots"][0]["provider"] == "keychain"
        assert not crypto.DISK_KEK_PATH.exists()

    def test_keychain_without_tool_falls_back_to_disk(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(crypto, "_keychain_tool", lambda: None)
        unlock(Encryption(provider="keychain"))
        import json

        ks = json.loads(crypto.KEYSTORE_PATH.read_text())
        assert ks["slots"][0]["provider"] == "disk"
        assert "no OS keychain tool" in capsys.readouterr().err

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(CryptoError, match="unknown encryption provider"):
            unlock(Encryption(provider="bogus"))

    def test_leaves_legacy_keyfile_alone(self) -> None:
        # Pre-envelope key material: dead to the app, but never deleted.
        legacy = crypto.KEYSTORE_PATH.parent / "history.key"
        legacy.write_bytes(b"old key")
        unlock(Encryption(provider="disk"))
        assert legacy.read_bytes() == b"old key"


class TestUnlockReopen:
    def test_disk_reopen_yields_same_dek(self) -> None:
        c1 = unlock(Encryption(provider="disk"))
        ct, nonce = c1.seal(b"payload")
        c2 = unlock(Encryption(provider="disk"))  # reads existing keystore
        assert c2.unseal(ct, nonce) == b"payload"

    def test_keychain_reopen_yields_same_dek(self) -> None:
        c1 = unlock(Encryption(provider="keychain"))
        ct, nonce = c1.seal(b"payload")
        c2 = unlock(Encryption(provider="keychain"))
        assert c2.unseal(ct, nonce) == b"payload"


class TestProvisionNeverOverwrites:
    def test_reprovision_reuses_keychain_kek(self) -> None:
        # Losing keys.json and relaunching must not burn the KEK: a restored
        # backup of keys.json has to keep unlocking the original DEK.
        c1 = unlock(Encryption(provider="keychain"))
        ct, nonce = c1.seal(b"survives re-provisioning")
        backup = crypto.KEYSTORE_PATH.read_bytes()
        crypto.KEYSTORE_PATH.unlink()
        unlock(Encryption(provider="keychain"))  # re-provisions; must reuse the KEK
        crypto.KEYSTORE_PATH.unlink()
        crypto.KEYSTORE_PATH.write_bytes(backup)
        c2 = unlock(Encryption(provider="keychain"))
        assert c2.unseal(ct, nonce) == b"survives re-provisioning"

    def test_invalid_existing_keychain_item_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crypto, "_keychain_get", lambda: b"short")
        with pytest.raises(CryptoError, match="refusing to overwrite"):
            unlock(Encryption(provider="keychain"))

    def test_command_provision_reuses_retrievable_kek(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        b64 = base64.b64encode(b"K" * 32).decode()
        enc = Encryption(provider="command", retrieve_command=f"printf %s {b64}")
        c1 = unlock(enc)
        assert "store this key" not in capsys.readouterr().err
        ct, nonce = c1.seal(b"cmd-data")
        c2 = unlock(enc)
        assert c2.unseal(ct, nonce) == b"cmd-data"

    def test_command_provision_mints_when_command_has_no_key_yet(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unlock(Encryption(provider="command", retrieve_command="false"))
        assert "store this key" in capsys.readouterr().err

    def test_write_keystore_refuses_overwrite(self) -> None:
        crypto.KEYSTORE_PATH.write_text("{}")
        with pytest.raises(CryptoError, match="refusing to overwrite"):
            crypto._write_keystore({"version": 1, "slots": []})
        assert crypto.KEYSTORE_PATH.read_text() == "{}"


class TestKeychainService:
    def test_default_dir_keeps_legacy_name(self) -> None:
        assert crypto._kc_service(Path.home() / ".otaku") == "otaku"

    def test_custom_dir_gets_own_item(self, tmp_path: Path) -> None:
        alt = tmp_path / "alt"
        assert crypto._kc_service(alt) == f"otaku:{alt}"


class TestPassphraseProvider:
    def test_first_run_and_reopen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crypto.getpass, "getpass", lambda *_: "correct horse")
        c1 = unlock(Encryption(provider="passphrase"))
        ct, nonce = c1.seal(b"data")
        import json

        assert json.loads(crypto.KEYSTORE_PATH.read_text())["slots"][0]["provider"] == "passphrase"
        c2 = unlock(Encryption(provider="passphrase"))
        assert c2.unseal(ct, nonce) == b"data"

    def test_empty_passphrase_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crypto.getpass, "getpass", lambda *_: "")
        with pytest.raises(CryptoError, match="empty passphrase"):
            unlock(Encryption(provider="passphrase"))

    def test_confirm_mismatch_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answers = iter(["first", "second"])
        monkeypatch.setattr(crypto.getpass, "getpass", lambda *_: next(answers))
        with pytest.raises(CryptoError, match="do not match"):
            unlock(Encryption(provider="passphrase"))


class TestCommandProvider:
    def test_first_run_and_reopen_via_real_subprocess(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # First run mints a random KEK and prints it; capture the base64 and
        # feed it back through a real `printf` retrieve_command on reopen.
        c1 = unlock(Encryption(provider="command"))
        ct, nonce = c1.seal(b"cmd-data")
        printed = capsys.readouterr().err
        m = re.search(r"\n\s+([A-Za-z0-9+/=]+)\s*$", printed)
        assert m is not None
        b64 = m.group(1)
        enc = Encryption(provider="command", retrieve_command=f"printf %s {b64}")
        c2 = unlock(enc)
        assert c2.unseal(ct, nonce) == b"cmd-data"

    def test_command_missing_raises_on_reopen(self) -> None:
        # provision a command slot, then reopen with no retrieve_command
        unlock(Encryption(provider="command"))
        with pytest.raises(CryptoError, match="retrieve_command"):
            unlock(Encryption(provider="command", retrieve_command=None))


class TestCommandGet:
    def test_nonzero_exit_raises(self) -> None:
        with pytest.raises(CryptoError, match="retrieve_command failed"):
            _command_get("false")

    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(CryptoError, match="not valid base64"):
            _command_get("printf 'not base64 @@@'")

    def test_wrong_length_raises(self) -> None:
        b64 = base64.b64encode(b"abc").decode()  # 3 bytes, not 32
        with pytest.raises(CryptoError, match="returned 3 bytes"):
            _command_get(f"printf %s {b64}")

    def test_valid_32_bytes(self) -> None:
        b64 = base64.b64encode(b"z" * 32).decode()
        assert _command_get(f"printf %s {b64}") == b"z" * 32


class TestMultiSlot:
    def _write_keystore(self, slots: list[dict]) -> None:
        import json

        crypto.KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        crypto.KEYSTORE_PATH.write_text(json.dumps({"version": 1, "slots": slots}))

    def test_configured_provider_slot_is_tried_first(self) -> None:
        dek = b"D" * 32
        kek_cmd = b"C" * 32
        cmd_b64 = base64.b64encode(kek_cmd).decode()
        # a disk slot (kek not on disk → would fail) plus a command slot
        self._write_keystore(
            [
                {"provider": "disk", **_wrap_dek(dek, b"A" * 32)},
                {"provider": "command", **_wrap_dek(dek, kek_cmd)},
            ]
        )
        # disk kek.key is absent, so the disk slot cannot unwrap; but the
        # command provider is configured, so its slot sorts first and wins.
        enc = Encryption(provider="command", retrieve_command=f"printf %s {cmd_b64}")
        cipher = unlock(enc)
        ref = Cipher(dek)
        ct, nonce = ref.seal(b"ok")
        assert cipher.unseal(ct, nonce) == b"ok"

    def test_failing_slot_is_skipped_for_next(self) -> None:
        dek = b"D" * 32
        # keychain slot whose KEK is NOT in the in-memory keychain (fails),
        # plus a disk slot whose KEK we place on disk (succeeds).
        disk_kek = b"K" * 32
        crypto.DISK_KEK_PATH.parent.mkdir(parents=True, exist_ok=True)
        crypto.DISK_KEK_PATH.write_bytes(disk_kek)
        self._write_keystore(
            [
                {"provider": "keychain", **_wrap_dek(dek, b"Z" * 32)},
                {"provider": "disk", **_wrap_dek(dek, disk_kek)},
            ]
        )
        cipher = unlock(Encryption(provider="keychain"))  # keychain fails → disk wins
        ref = Cipher(dek)
        ct, nonce = ref.seal(b"ok")
        assert cipher.unseal(ct, nonce) == b"ok"

    def test_all_slots_fail_raises(self) -> None:
        self._write_keystore([{"provider": "keychain", **_wrap_dek(b"D" * 32, b"Z" * 32)}])
        # in-memory keychain is empty → keychain_get returns None → CryptoError.
        # A single slot reports its reason bare — no provider label, no
        # "could not unlock" headline (the CLI adds that).
        with pytest.raises(CryptoError, match=r"^key not found in OS keychain$"):
            unlock(Encryption(provider="keychain"))

    def test_unknown_provider_in_slot_raises(self) -> None:
        self._write_keystore([{"provider": "martian", **_wrap_dek(b"D" * 32, b"Z" * 32)}])
        with pytest.raises(CryptoError, match="unknown provider in keystore"):
            unlock(Encryption(provider="martian"))

    def test_key_mismatch_gets_spelled_out(self) -> None:
        # InvalidTag stringifies to "" — the message must explain the mismatch.
        crypto._keychain_put(b"A" * 32)  # keychain holds a key…
        self._write_keystore([{"provider": "keychain", **_wrap_dek(b"D" * 32, b"Z" * 32)}])
        with pytest.raises(CryptoError, match="wrong or replaced KEK"):
            unlock(Encryption(provider="keychain"))

    def test_multi_slot_failures_are_labelled_per_provider(self) -> None:
        crypto._keychain_put(b"A" * 32)
        self._write_keystore(
            [
                {"provider": "keychain", **_wrap_dek(b"D" * 32, b"Z" * 32)},
                {"provider": "martian", **_wrap_dek(b"D" * 32, b"Z" * 32)},
            ]
        )
        with pytest.raises(CryptoError, match=r"keychain: .*; martian: "):
            unlock(Encryption(provider="keychain"))
