"""The installation's settings: /set verbose and /set notification, kept
in the app's own state, and /set max_context, config.toml's [context]
value edited in place, its one home — each remembered across a
relaunch.
"""

from otaku.backend.app.settings.schemas import Settings
from otaku.backend.story.messages.schemas import Done
from otaku.terminal.chat import stream
from scenarios.support.harness import App, launch, set_config, set_config_provider
from scenarios.support.server import ModelServer


class TestVerbose:
    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set verbose on")
        relaunched = launch(app.paths.root, app.server)
        assert _settings(relaunched).verbose is True
        relaunched.close()


class TestNotification:
    """Off by default, so nobody is rung by an upgrade; on, a landed
    reply calls the reader back. WHAT it plays is the platform's — only
    that it rang is the app's promise."""

    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set notification on")
        relaunched = launch(app.paths.root, app.server)
        assert _settings(relaunched).notification is True
        relaunched.close()

    def test_a_landed_reply_rings_once(self, app: App, monkeypatch) -> None:
        rung = _record_rings(monkeypatch)
        app.play("/set notification on")
        app.play("I enter the hall.")
        assert len(rung) == 1

    def test_nothing_rings_while_it_is_off(self, app: App, monkeypatch) -> None:
        rung = _record_rings(monkeypatch)
        app.play("I enter the hall.")  # off is the default
        app.play("/set notification off")
        app.play("I look around.")
        assert rung == []

    def test_the_configured_sound_is_what_goes_to_the_player(self, app: App, monkeypatch) -> None:
        # config.toml names it; the terminal only passes it along, so a
        # path the platform cannot play is the player's business, not the
        # session's.
        rung = _record_rings(monkeypatch)
        app.play("/set notification on")
        app.play("I enter the hall.")
        assert rung == ["default"]


class TestMaxContext:
    """The prompt-size cap: ONE home, config.toml's [context] — /set
    max_context edits the file surgically (backed up, comment intact)
    and the session takes the value in the same call."""

    def test_the_set_value_lands_in_config_toml_and_is_remembered(self, app: App) -> None:
        app.play("/set max_context 32000")
        migrated = app.paths.config_file.read_text()
        assert "max_context = 32000" in migrated
        assert migrated.count("max_context") == 1  # edited in place, not appended
        assert "0 = the model's own max context" in migrated  # the comment rides it
        assert any(app.paths.config_backups_dir.iterdir())  # the pre-edit file waits
        relaunched = launch(app.paths.root, app.server)
        assert _settings(relaunched).context_limit == 32000
        relaunched.close()

    def test_a_reply_is_measured_against_the_capped_context(self, tmp_path) -> None:
        # The reply's report — the verbose line's actual share — divides by
        # what the story was cut to, the cap included, not the whole window.
        engine = ModelServer(managed=True)
        engine.contexts["test-model"] = 32768
        try:
            root = tmp_path / "state"
            set_config(root, context_limit=16384)
            set_config_provider(root, engine, name="ollama")
            app = launch(root, engine, spec="ollama/test-model")
            try:
                events = app.api.stories.messages.create(
                    app.ensure_story(), line="I knock at the gate.", expected_head=app.head()
                )
                done = next(e for e in events if isinstance(e, Done))
                assert done.report is not None and done.report.context_in_force == 16384
            finally:
                app.close()
        finally:
            engine.close()

    def test_a_bare_set_reports_and_changes_nothing(self, app: App, capsys) -> None:
        # Reported against a value the story SET, not against the
        # shipped default: what this holds is that a bare `/set` reads.
        app.play("/set max_context 32000")
        capsys.readouterr()
        app.play("/set max_context")
        assert "32,000 tokens" in capsys.readouterr().out
        assert "max_context = 32000" in app.paths.config_file.read_text()

    def test_the_prompt_may_use_the_whole_window_until_a_cap_is_set(self, app: App) -> None:
        # 0 is what a fresh config carries: the window a model advertises
        # is the one it can use, and a reader who wants the prompt kept
        # smaller than that says so.
        assert _settings(app).context_limit == 0
        assert "max_context = 0" in app.paths.config_file.read_text()

    def test_a_story_over_the_cap_declines_with_the_sentence(self, app: App, capsys) -> None:
        for i in range(8):
            app.play(f"Turn number {i}. " + "x" * 8000)
        app.play("/set max_context 2000")
        before = len(app.server.requests)
        capsys.readouterr()
        app.play("We continue.")
        assert len(app.server.requests) == before  # nothing was sent
        assert "does not fit the context limit" in capsys.readouterr().out


def _record_rings(monkeypatch) -> list[str]:
    """Every sound the terminal asked for — patched where the stream
    reads it, so a test never actually makes a noise."""
    rung: list[str] = []
    monkeypatch.setattr(stream, "ring", rung.append)
    return rung


def _settings(app: App) -> Settings:
    return app.api.settings.get()
