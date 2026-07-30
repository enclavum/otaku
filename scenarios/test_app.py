"""The launch itself (`otaku.app`): the key ceremony and what it seals,
the daily backup snapshot, and resuming over a story that is gone.

Encryption runs the real path end to end — the "command" KEK provider
with a scripted retrieve_command — so these stories prove the core
principle, not a stub: sealed bytes on disk, a wrong key refused, a
missing keystore refused BEFORE the ceremony could mint over it."""

import base64

import pytest

from otaku import crypto
from otaku.settings import config as config_mod
from otaku.store import is_encrypted
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, run_otaku, set_config
from scenarios.support.server import ModelServer

KEY = base64.b64encode(b"k" * 32).decode()
OTHER_KEY = base64.b64encode(b"x" * 32).decode()


class TestEncryption:
    def test_content_is_sealed_on_disk(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        try:
            app.play("I enter the hall.")
        finally:
            app.close()
        raw = app.paths.database_file.read_bytes()
        assert b"I enter the hall." not in raw
        assert scripted.CHAT_REPLY.encode() not in raw
        assert is_encrypted(app.paths.database_file) is True
        # The request log is sealed the same way.
        for logfile in (app.paths.root / "logs").rglob("*"):
            if logfile.is_file():
                assert b"I enter the hall." not in logfile.read_bytes()

    def test_provider_none_stores_readable_plain_text(self, app: App) -> None:
        app.play("I enter the hall.")
        app.close()
        raw = app.paths.database_file.read_bytes()
        assert b"I enter the hall." in raw
        assert is_encrypted(app.paths.database_file) is False

    def test_the_right_key_reopens_the_story(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        relaunched = launch(root, server)
        try:
            assert [m.body for m in relaunched.session.messages] == [
                "I enter the hall.",
                scripted.CHAT_REPLY,
            ]
        finally:
            relaunched.close()

    def test_logs_decrypt_with_the_key(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        result = run_otaku(root, "logs", "requests")
        assert result.returncode == 0
        assert "I enter the hall." in result.stdout

    def test_a_wrong_key_is_refused(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        set_encryption(root, KEY)
        launch(root, server).close()
        set_encryption(root, OTHER_KEY)
        with pytest.raises(crypto.CryptoError, match="Could not unlock"):
            launch(root, server)

    def test_a_missing_keystore_is_refused_before_the_ceremony(
        self, server: ModelServer, tmp_path
    ) -> None:
        # Unlocking without the keystore would mint a fresh key OVER the
        # sealed rows, making them permanently unreadable — refused first.
        root = tmp_path / "state"
        set_encryption(root, KEY)
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        app.paths.keys_file.unlink()
        with pytest.raises(crypto.CryptoError, match="is missing"):
            launch(root, server)


class TestBackups:
    def test_a_daily_snapshot_appears_on_reopen(self, server: ModelServer, tmp_path) -> None:
        root = tmp_path / "state"
        app = launch(root, server)
        app.play("I enter the hall.")
        app.close()
        relaunched = launch(root, server)
        relaunched.close()
        assert any(app.paths.backups_dir.iterdir())


class TestResume:
    def test_a_deleted_remembered_story_starts_fresh(self, app: App) -> None:
        app.play("I enter the hall.")
        app.store.stories.delete(app.session.story_id)
        relaunched = launch(app.paths.root, app.server)
        try:
            assert relaunched.session.story_id is None
        finally:
            relaunched.close()


def set_encryption(root, key: str) -> None:
    set_config(
        root,
        encryption=config_mod.Encryption(provider="command", retrieve_command=f"echo {key}"),
    )
