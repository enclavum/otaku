"""The launch itself (`otaku.app`): the key ceremony and what it seals,
the daily backup snapshot, and resuming over a story that is gone.

Encryption runs the real path end to end — the "command" KEK provider
with a scripted retrieve_command — so these stories prove the core
principle, not a stub: sealed bytes on disk, a wrong key refused, a
missing keystore refused BEFORE the ceremony could mint over it."""

import base64
import secrets
import sqlite3
from datetime import datetime

import pytest

from otaku import crypto
from otaku.app import load_config
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.settings import migrations, sealed
from otaku.store import DatabaseError, is_encrypted
from otaku.terminal import BOLD, RESET
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


class TestDatabaseGuard:
    def test_a_foreign_file_is_refused_with_the_curated_message(
        self, server: ModelServer, tmp_path
    ) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.database_file.write_bytes(b"this is not a database")
        with pytest.raises(DatabaseError, match="move the file aside"):
            launch(tmp_path / "state", server)

    def test_a_crashed_first_run_heals_as_fresh(self, server: ModelServer, tmp_path) -> None:
        """Creation is one transaction: a crash rolls back to a valid,
        table-less file — which the next launch treats as fresh, instead
        of refusing it forever."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        conn = sqlite3.connect(paths.database_file)
        conn.execute("CREATE TABLE husk (id INTEGER PRIMARY KEY)")
        conn.execute("DROP TABLE husk")
        conn.commit()
        conn.close()
        launch(tmp_path / "state", server).close()  # launching IS the assertion


class TestConfigMigration:
    def test_a_first_run_writes_both_config_files(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        # The sealing key as a file — first run now migrates too, and a
        # real autoconfigured key must never reach the OS keychain here.
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        cfg = load_config(paths)
        assert "[providers." not in paths.config_file.read_text()
        providers = paths.providers_file.read_text()
        for section in ("[llamacpp]", "[koboldcpp]", "[ollama]", "[omlx]", "[lmstudio]"):
            assert section in providers
        assert set(cfg.providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    def test_an_old_config_gains_the_new_section_and_a_backup(
        self, server: ModelServer, tmp_path
    ) -> None:
        """A config from an older build passes through the launch's
        `load_config` and comes out in the current shape — surgically,
        the user's own lines intact — with the pre-migration file waiting
        in configs/backups/."""
        app = launch(tmp_path / "state", server)
        app.close()
        config_file = app.paths.config_file
        old = "\n".join(
            line
            for line in config_file.read_text().splitlines()
            if not line.startswith(("[ui]", "dialogue_color =", "dialogue_bold ="))
        )
        config_file.write_text(old + "\n# my note\n")

        cfg = load_config(app.paths)
        migrated = config_file.read_text()
        assert "[ui]" in migrated
        assert 'dialogue_color = "auto"' in migrated
        # Anchored below [settings], where a fresh config renders it.
        assert migrated.index("[settings]") < migrated.index("[ui]") < migrated.index("[context]")
        assert "# my note" in migrated  # the user's own line survived
        assert cfg.dialogue_color == "auto"
        backups = list(app.paths.config_backups_dir.iterdir())
        assert len(backups) == 1
        assert "[ui]" not in backups[0].read_text()

    def test_a_current_config_is_left_untouched(self, server: ModelServer, tmp_path) -> None:
        app = launch(tmp_path / "state", server)
        app.close()
        before = app.paths.config_file.read_text()
        load_config(app.paths)
        assert app.paths.config_file.read_text() == before
        assert not app.paths.config_backups_dir.exists()

    def test_a_missing_providers_file_is_refounded(self, tmp_path) -> None:
        """A crash between the first-run writes — or a hand deletion —
        heals: the file is founded and the known backends ensured."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n")
        cfg = load_config(paths)
        assert paths.providers_file.exists()
        assert set(cfg.providers) == {"llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio"}

    def test_backups_are_private(self, tmp_path) -> None:
        """Every backup is born 0600 in a 0700 dir — a pre-seal copy may
        hold a plain api key."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.providers_file.write_text('[a]\nurl = "x"\napi_key = ""\n')
        assert migrations.update_providers(paths, [migrations.set_key("a", "url", 'url = "y"')])
        stamp = datetime.now().astimezone().strftime("%Y%m%d")
        backup = paths.config_backups_dir / f"providers-{stamp}.toml"
        assert (backup.stat().st_mode & 0o777) == 0o600
        assert (backup.parent.stat().st_mode & 0o777) == 0o700

    def test_an_old_configs_providers_move_into_their_own_file(self, tmp_path) -> None:
        """The cross-file migration: [providers.*] sections leave for
        providers.toml — as they are, headers unprefixed, the retired
        thinking knob and the plain api key cleaned up at their new
        home — and reruns converge on the same state."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text(
            "# the box in the closet\n"
            "[providers.mine]\n"
            'url = "http://localhost:9/v1"\n'
            'api_key = "plain-secret"\n'
            "supports_thinking = true\n"
            "\n"
            "[settings]\n"
            "show_banner = false\n"
        )
        cfg = load_config(paths)
        assert cfg.providers["mine"].url == "http://localhost:9/v1"
        assert sealed.unseal(paths, cfg.providers["mine"].api_key) == "plain-secret"
        moved = paths.providers_file.read_text()
        assert "# the box in the closet\n[mine]\n" in moved
        assert "supports_thinking" not in moved
        assert "plain-secret" not in moved  # sealed on the way over
        # The backends this old config never knew arrived alongside.
        assert "[llamacpp]" in moved
        assert "[lmstudio]" in moved
        conf = paths.config_file.read_text()
        assert "[providers.mine]" not in conf
        assert "show_banner = false" in conf
        # The pre-move config waits in backups; a second launch moves
        # nothing and loads the same providers.
        assert any(p.name.startswith("config-") for p in paths.config_backups_dir.iterdir())
        again = load_config(paths)
        assert again.providers["mine"].api_key == cfg.providers["mine"].api_key

    def test_a_half_done_move_heals_on_the_next_launch(self, tmp_path) -> None:
        """A crash between the move's two writes leaves the section in
        both files; the next launch drops the config copy — the new home
        wins — and converges."""
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.providers_file.write_text('[mine]\nurl = "http://localhost:9/v1"\napi_key = ""\n')
        paths.config_file.write_text(
            '[providers.mine]\nurl = "http://old:9/v1"\n\n[settings]\nshow_banner = false\n'
        )
        cfg = load_config(paths)
        assert "[providers.mine]" not in paths.config_file.read_text()
        assert cfg.providers["mine"].url == "http://localhost:9/v1"  # the new home won

    def test_a_hand_typed_plain_key_seals_at_the_next_launch(self, tmp_path) -> None:
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.config_file.write_text("[settings]\nshow_banner = false\n")
        paths.providers_file.write_text(
            '[mine]\nurl = "http://localhost:9/v1"\napi_key = "pasted-plain"\n'
        )
        cfg = load_config(paths)
        assert "pasted-plain" not in paths.providers_file.read_text()
        assert sealed.unseal(paths, cfg.providers["mine"].api_key) == "pasted-plain"


class TestFirstLaunch:
    """The install experience, end to end: a fresh database seeds the
    sample story, the user lands in it — with or without a reachable
    model — and every model-facing door explains itself until /model."""

    def test_a_fresh_database_seeds_the_sample_and_lands_in_it(self, server, tmp_path) -> None:
        # The first launch over a new database imports the shipped story —
        # a native import, zero model calls — and the user is in the
        # middle of it, memory and all.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server)
        try:
            assert app.server.requests == []  # seeding never calls a model
            story_id = app.session.story_id
            assert story_id is not None
            story = app.store.stories.get(story_id)
            assert story.title == "The River That Forgot Its Name"
            assert len(app.session.messages) == 14
            ids = app.store.stories.get_messages_ids(story_id)
            assert len(app.store.scenes.get_current(story_id, ids)) == 2
            assert [c.name for c in app.store.characters.list(story_id)] == ["Maren", "Tallis"]
        finally:
            app.close()
        # Remembered: a relaunch resumes the sample and does NOT seed again.
        relaunched = launch(tmp_path / "state", server)
        try:
            assert relaunched.session.story_id == story_id
            assert len(relaunched.store.stories.list()) == 1
        finally:
            relaunched.close()

    def test_a_declined_launch_picker_opens_the_session_anyway(self, server, tmp_path) -> None:
        # Esc in the launch picker is not a cancel: the session opens
        # model-less — the same state /model's Esc leaves behind.
        app = launch(tmp_path / "state", server, spec=None)
        try:
            assert app.session.provider_config is None
        finally:
            app.close()

    def test_no_models_still_opens_into_the_sample(self, server, tmp_path, capsys) -> None:
        # Nothing reachable: the session opens without a model, the sample
        # is there to explore, and a turn is kept as story but explains
        # why nothing streams.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            assert app.session.provider_config is None
            story = app.store.stories.get(app.session.story_id)
            assert story.title == "The River That Forgot Its Name"
            capsys.readouterr()
            app.play("Hello? Is someone there?")
            assert "No model selected" in capsys.readouterr().out
            assert app.server.requests == []
            assert len(app.session.messages) == 15  # the turn is story, kept
        finally:
            app.close()

    def test_a_model_picked_later_revives_the_session(self, server, tmp_path, capsys) -> None:
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            app.play("/model test/test-model")
            assert f"Switched to {BOLD}test/test-model{RESET}." in capsys.readouterr().out
            app.play("I climb toward the voice.")
            assert app.session.messages[-1].body == scripted.CHAT_REPLY
        finally:
            app.close()

    def test_an_existing_database_never_seeds(self, server, tmp_path) -> None:
        root = tmp_path / "state"
        app = launch(root, server)  # seed_sample off: the database exists now
        app.play("I enter the hall.")
        app.close()
        set_config(root, seed_sample=True)
        relaunched = launch(root, server)
        try:
            assert len(relaunched.store.stories.list()) == 1  # only the played story
        finally:
            relaunched.close()

    def test_the_knob_off_seeds_nothing(self, app: App) -> None:
        # The scenario default: a fresh database, seed_sample = false.
        assert app.session.story_id is None
        assert app.store.stories.list() == []


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
