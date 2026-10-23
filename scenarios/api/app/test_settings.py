"""The installation's settings: /set verbose and /set notification, kept
in the app's own state, and /set max_context, config.toml's [context]
value edited in place, its one home — each remembered across a
relaunch; and /set serve, the page served beside the chat, its switch
in config.toml's [web]. The environment's variables over the file's:
the web's host, port, password and https, and the terminal's serving.
"""

import socket
import urllib.request

import pytest

from otaku.backend.app.settings.schemas import Settings
from otaku.backend.errors import Invalid
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
        migrated = app.paths.config_file.read_text(encoding="utf-8")
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
        assert "max_context = 32000" in app.paths.config_file.read_text(encoding="utf-8")

    def test_the_prompt_may_use_the_whole_window_until_a_cap_is_set(self, app: App) -> None:
        # 0 is what a fresh config carries: the window a model advertises
        # is the one it can use, and a reader who wants the prompt kept
        # smaller than that says so.
        assert _settings(app).context_limit == 0
        assert "max_context = 0" in app.paths.config_file.read_text(encoding="utf-8")

    def test_a_story_over_the_cap_declines_with_the_sentence(self, app: App, capsys) -> None:
        for i in range(8):
            app.play(f"Turn number {i}. " + "x" * 8000)
        app.play("/set max_context 2000")
        before = len(app.server.requests)
        capsys.readouterr()
        app.play("We continue.")
        assert len(app.server.requests) == before  # nothing was sent
        assert "does not fit the context limit" in capsys.readouterr().out


class TestServe:
    """`/set serve`: the page served beside the chat, on the address
    config.toml's [web] names — a free port here, so no test ever reaches
    a real otaku's — and the switch kept there (`terminal_serve`), off
    until asked."""

    def test_on_serves_the_page_and_off_stops_it(self, app: App, monkeypatch) -> None:
        port = _free_port(monkeypatch)
        app.play("/set serve on")
        assert _answers(port)
        app.play("/set serve off")
        assert not _listening(port)

    def test_the_switch_lands_in_config_toml(self, app: App, monkeypatch) -> None:
        _free_port(monkeypatch)
        app.play("/set serve on")
        assert "terminal_serve = true" in app.paths.config_file.read_text(encoding="utf-8")
        app.play("/set serve off")
        assert "terminal_serve = false" in app.paths.config_file.read_text(encoding="utf-8")

    def test_the_page_is_served_again_right_after_off(self, app: App, monkeypatch) -> None:
        # the closed connections still linger on the port when it is asked
        # for again
        port = _free_port(monkeypatch)
        app.play("/set serve on")
        assert _answers(port)
        app.play("/set serve off")
        app.play("/set serve on")
        assert _answers(port)

    def test_a_port_already_held_serves_nothing_and_keeps_the_switch(
        self, app: App, monkeypatch
    ) -> None:
        port = _free_port(monkeypatch)
        with socket.socket() as holder:
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            holder.bind(("127.0.0.1", port))
            holder.listen()
            app.play("/set serve on")
        assert not _listening(port)  # nothing of otaku's took it
        assert app.api.settings.terminal.get().terminal_serve is True

    def test_a_config_from_before_the_switch_gets_it_at_the_launch(self, app: App) -> None:
        config = app.paths.config_file
        config.write_text(
            "".join(
                row
                for row in config.read_text(encoding="utf-8").splitlines(True)
                if "terminal_serve" not in row
            ),
            encoding="utf-8",
        )
        relaunched = launch(app.paths.root, app.server)
        assert "terminal_serve = false" in config.read_text(encoding="utf-8")
        assert relaunched.api.settings.terminal.get().terminal_serve is False
        relaunched.close()


class TestVariables:
    """Each web setting is the environment's where a variable sets it —
    read with every call — and config.toml's otherwise; the file is
    never written."""

    def test_the_web_settings_are_the_variables_over_the_file(self, app: App, monkeypatch) -> None:
        before = app.api.settings.web.get().config
        assert (before.host, before.port, before.https) == ("localhost", 9600, False)
        monkeypatch.setenv("OTAKU_WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("OTAKU_WEB_PORT", "9601")
        monkeypatch.setenv("OTAKU_WEB_HTTPS", "on")
        after = app.api.settings.web.get().config
        assert (after.host, after.port, after.https) == ("0.0.0.0", 9601, True)
        config = app.paths.root / "configs" / "config.toml"
        assert 'host = "localhost"' in config.read_text(encoding="utf-8")

    def test_off_turns_off_what_the_file_turns_on(self, app: App, monkeypatch) -> None:
        set_config(app.paths.root, web_https=True, web_terminal_serve=True)
        monkeypatch.setenv("OTAKU_WEB_HTTPS", "off")
        monkeypatch.setenv("OTAKU_TERMINAL_SERVE", "off")
        assert app.api.settings.web.get().config.https is False
        assert app.api.settings.terminal.get().terminal_serve is False

    def test_the_terminal_serves_where_the_variable_says_so(self, app: App, monkeypatch) -> None:
        monkeypatch.setenv("OTAKU_TERMINAL_SERVE", "on")
        assert app.api.settings.terminal.get().terminal_serve is True

    def test_the_variables_password_signs_in_and_stays_out_of_the_file(
        self, app: App, monkeypatch
    ) -> None:
        monkeypatch.setenv("OTAKU_WEB_PASSWORD", "under the stairs")
        assert app.api.settings.web.get().config.password
        app.api.settings.check_password("under the stairs")
        with pytest.raises(Invalid):
            app.api.settings.check_password("over the hills")
        config = app.paths.root / "configs" / "config.toml"
        assert "under the stairs" not in config.read_text(encoding="utf-8")

    def test_a_value_a_variable_cannot_hold_is_passed_over_and_said(
        self, app: App, monkeypatch
    ) -> None:
        monkeypatch.setenv("OTAKU_WEB_PORT", "99999")
        monkeypatch.setenv("OTAKU_WEB_HTTPS", "yes")
        monkeypatch.setenv("OTAKU_TERMINAL_SERVE", "sometimes")
        relaunched = launch(app.paths.root, app.server)
        web = relaunched.api.settings.web.get().config
        assert (web.port, web.https) == (9600, False)
        assert relaunched.api.settings.terminal.get().terminal_serve is False
        notices = " ".join(relaunched.api.app.get().launch_facts.notices)
        for name in ("OTAKU_WEB_PORT", "OTAKU_WEB_HTTPS", "OTAKU_TERMINAL_SERVE"):
            assert name in notices, name
        relaunched.close()


def _free_port(monkeypatch) -> int:
    """A port nothing listens on, made the one the page is served at —
    `OTAKU_WEB_PORT`, the override a development server moves by."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    monkeypatch.setenv("OTAKU_WEB_PORT", str(port))
    return port


def _answers(port: int) -> bool:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/app/status", timeout=5) as reply:
        return bool(reply.status == 200)


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _record_rings(monkeypatch) -> list[str]:
    """Every sound the terminal asked for — patched where the stream
    reads it, so a test never actually makes a noise."""
    rung: list[str] = []
    monkeypatch.setattr(stream, "ring", rung.append)
    return rung


def _settings(app: App) -> Settings:
    return app.api.settings.get()
