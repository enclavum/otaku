"""The model and the knobs: /model, /set think, /set verbose,
/set parameter, /set notification, /set max_context — and what each
remembers across a relaunch.

The design: `/set think`, `/set verbose` and `/set notification` are
session-wide and persist in the app's own state; `/set max_context`
edits config.toml's [context] value surgically — its one home; `/set
parameter` follows the MODEL it was set on; a model switch keeps the
story context and is remembered as last used.
"""

import contextlib
import time
import tomllib

import pytest

from otaku.backend import launch as backend_launch
from otaku.backend.api import providers as api_providers
from otaku.backend.session import Refused
from otaku.encryption import unseal
from otaku.providers import Locality, ModelState
from otaku.terminal.chat import stream
from otaku.terminal.screens import models as screen_models
from otaku.terminal.tty import clipboard
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import CTRL_V, ENTER, ESC, pasted, run_screen
from scenarios.support.server import ModelServer

# Key escape sequences, for pipe input.
_DOWN = "\x1b[B"
_LEFT = "\x1b[D"
_HOME = "\x1b[H"
_END = "\x1b[F"
_DEL = "\x1b[3~"


class TestModel:
    def test_a_direct_switch_keeps_the_context_and_changes_the_wire(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/model generic/other-model")
        app.play("I look around.")
        assert app.server.requests[-1]["model"] == "other-model"
        # The story context traveled with the switch.
        assert [m.body for m in app.session.messages][:2] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]

    def test_the_switch_is_remembered_as_last_used(self, app: App) -> None:
        app.play("/model generic/other-model")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.model == "other-model"
        relaunched.close()

    def test_a_model_name_with_a_quote_survives_the_state_file(self, app: App) -> None:
        app.play('/model generic/oddly"named')
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.model == 'oddly"named'
        relaunched.close()

    def test_a_saved_parameter_the_model_rejects_is_said_at_the_switch(
        self, app: App, capsys
    ) -> None:
        # A hand-edited models.toml is the only way in. The launch says
        # such a thing before the banner; a switch has to say it where it
        # happens, or the session drops a parameter in silence.
        app.paths.models_file.write_text("[other-model]\nbogus = 1\n")
        capsys.readouterr()
        app.play("/model generic/other-model")
        assert "bogus" in capsys.readouterr().out
        assert "bogus" not in app.session.params

    def test_cancelling_the_picker_keeps_the_model(self, app: App, monkeypatch) -> None:
        monkeypatch.setattr(screen_models, "pick", lambda session, initial_spec=None: None)
        app.play("/model")
        assert app.session.model == "test-model"


class TestThink:
    def test_a_level_is_set_and_remembered(self, app: App, capsys) -> None:
        app.play("/set think high")
        assert "high" in capsys.readouterr().out
        assert app.session.think == "high"
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.think == "high"
        relaunched.close()

    def test_on_and_off_are_aliases(self, app: App) -> None:
        app.play("/set think on")
        assert app.session.think == "medium"
        app.play("/set think off")
        assert app.session.think == "none"

    def test_default_means_the_model_decides(self, app: App) -> None:
        app.play("/set think default")
        assert app.session.think is None
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.think is None
        relaunched.close()

    def test_the_level_rides_the_wire_and_default_sends_nothing(self, app: App) -> None:
        # The scripted server is a generic provider, whose url could name
        # a local engine as well as a catalog: every knob goes out.
        app.play("/set think low")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["reasoning_effort"] == "low"
        assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}
        app.play("/set think none")
        app.play("I listen.")
        body = app.server.requests[-1]
        assert body["reasoning_effort"] == "none"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        app.play("/set think default")
        app.play("I look around.")
        assert "reasoning_effort" not in app.server.requests[-1]
        assert "chat_template_kwargs" not in app.server.requests[-1]

    def test_a_400_to_the_knob_retries_once_without_it(self, app: App) -> None:
        # Providers differ on the knob: a reasoning-mandatory model refuses
        # "none", some providers reject the field outright — either way a
        # 400 before any content. The same request goes again with no
        # thinking field, and the provider's own default answers.
        app.play("/set think low")
        app.server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        app.server.refusal = "unknown field: reasoning_effort"
        app.play("I enter the hall.")
        assert app.session.messages[-1].role == "assistant"
        knobbed, knobless = app.server.requests[-2:]
        assert knobbed["reasoning_effort"] == "low"
        assert "reasoning_effort" not in knobless

    def test_thinking_streams_but_is_never_saved(self, app: App, capsys) -> None:
        app.play("/set think high")
        app.server.script = lambda body: ("Let me consider the hall.", "The door creaks open.")
        app.play("I enter the hall.")
        assert "(thinking) Let me consider the hall." in capsys.readouterr().out
        # Only the reply became part of the story...
        assert [m.body for m in app.session.messages] == [
            "I enter the hall.",
            "The door creaks open.",
        ]
        # ...so the next turn's context carries no thinking either.
        app.play("I look around.")
        assert "consider" not in str(app.server.requests[-1]["messages"])

    def test_llamacpp_gets_both_knobs_and_none_turns_the_flag_off(self, server, tmp_path) -> None:
        # llama.cpp's server reads no reasoning_effort of its own and hands
        # chat_template_kwargs to the template: the flag gates thinking,
        # and a level rides beside it as the variable a template that
        # grades its thinking reads. Its template allows thinking by
        # default, so with nothing sent the model thinks when it likes.
        set_config_provider(tmp_path / "state", server, name="llamacpp")
        app = launch(tmp_path / "state", server, spec="llamacpp/test-model")
        try:
            app.play("/set think none")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            assert "reasoning_effort" not in body
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            app.play("/set think high")
            app.play("I look around.")
            body = app.server.requests[-1]
            assert "reasoning_effort" not in body
            assert body["chat_template_kwargs"] == {
                "enable_thinking": True,
                "reasoning_effort": "high",
            }
        finally:
            app.close()

    def test_omlx_gets_the_template_its_flag_and_its_effort(self, server, tmp_path) -> None:
        # omlx forwards chat_template_kwargs into the template and drops
        # a request-level reasoning_effort unread: the flag gates thinking
        # — a level enables, off disables, default sends nothing — and the
        # effort rides beside it for the templates that grade theirs.
        set_config_provider(tmp_path / "state", server, name="omlx")
        app = launch(tmp_path / "state", server, spec="omlx/test-model")
        try:
            app.play("/set think high")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            template = {"enable_thinking": True, "reasoning_effort": "high"}
            assert body["chat_template_kwargs"] == template
            assert "reasoning_effort" not in body
            app.play("/set think off")
            app.play("I look around.")
            assert app.server.requests[-1]["chat_template_kwargs"] == {"enable_thinking": False}
            app.play("/set think default")
            app.play("We walk on.")
            assert "chat_template_kwargs" not in app.server.requests[-1]
        finally:
            app.close()

    def test_koboldcpp_gets_both_knobs(self, server, tmp_path) -> None:
        # KoboldCpp reads the effort as a thinking budget on every launch
        # and the template's flag under --jinja: both go out.
        set_config_provider(tmp_path / "state", server, name="koboldcpp")
        app = launch(tmp_path / "state", server, spec="koboldcpp/test-model")
        try:
            app.play("/set think low")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            assert body["reasoning_effort"] == "low"
            assert body["chat_template_kwargs"] == {"enable_thinking": True}
        finally:
            app.close()

    def test_ollama_gets_the_effort_alone(self, server, tmp_path) -> None:
        # Ollama reads reasoning_effort and nothing of the template.
        managed = ModelServer(managed=True)
        try:
            set_config_provider(tmp_path / "state", managed, name="ollama")
            app = launch(tmp_path / "state", managed, spec="ollama/test-model")
            try:
                app.play("/set think none")
                app.play("I enter the hall.")
                body = managed.requests[-1]
                assert body["reasoning_effort"] == "none"
                assert "chat_template_kwargs" not in body
            finally:
                app.close()
        finally:
            managed.close()

    def test_lmstudio_takes_the_level_and_sends_the_effort_alone(self, server, tmp_path) -> None:
        # LM Studio reads `reasoning_effort` and no template knob: the
        # level goes out by name and nothing else rides with it.
        set_config_provider(tmp_path / "state", server, name="lmstudio")
        plain = launch(tmp_path / "state", server, spec="lmstudio/test-model")
        try:
            plain.play("/set think high")
            assert plain.session.think == "high"
            plain.play("I enter the hall.")
            body = plain.server.requests[-1]
            assert body["reasoning_effort"] == "high"
            assert "chat_template_kwargs" not in body
        finally:
            plain.close()


class TestParameters:
    def test_a_set_parameter_reaches_the_wire(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("I enter the hall.")
        assert app.server.requests[-1]["temperature"] == 0.7

    def test_the_parameter_is_remembered_for_the_model(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        relaunched = launch(app.paths.root, app.server)
        relaunched.play("I enter the hall.")
        assert relaunched.server.requests[-1]["temperature"] == 0.7
        relaunched.close()

    def test_a_bare_name_shows_the_value_and_changes_nothing(self, app: App, capsys) -> None:
        # Asking is not setting: the bare name prints where the parameter
        # stands; only the literal `reset` resets.
        app.play("/set parameter temperature 0.7")
        capsys.readouterr()
        app.play("/set parameter temperature")
        assert "temperature = 0.7" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert app.server.requests[-1]["temperature"] == 0.7
        app.play("/set parameter top_p")
        assert "default" in capsys.readouterr().out  # unset: named as such

    def test_reset_returns_the_parameter_to_the_default(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("/set parameter temperature reset")
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_parameters_follow_their_model_across_a_switch(self, app: App) -> None:
        # Set on one model, switch away: the other model plays with ITS
        # saved parameters, not the first one's.
        app.play("/set parameter temperature 0.7")
        app.play("/model generic/other-model")
        app.play("/set parameter top_p 0.5")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["top_p"] == 0.5
        assert "temperature" not in body
        # And switching back restores the first model's own parameters.
        app.play("/model generic/test-model")
        app.play("I look around.")
        body = app.server.requests[-1]
        assert body["temperature"] == 0.7
        assert "top_p" not in body

    def test_an_invalid_value_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter temperature warm")
        assert "warm" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_an_unknown_parameter_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter charisma 18")
        assert "charisma" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "charisma" not in app.server.requests[-1]


class TestVerbose:
    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set verbose on")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.verbose is True
        relaunched.close()


class TestNotification:
    """Off by default, so nobody is rung by an upgrade; on, a landed
    reply calls the reader back. WHAT it plays is the platform's — only
    that it rang is the app's promise."""

    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set notification on")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.notification is True
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
        assert relaunched.session.max_context_setting == 32000
        relaunched.close()

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
        assert app.session.max_context_setting == 0
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


class TestManagedPicker:
    """The picker over a managed backend (a scripted ollama): load state
    on screen, l/u with a confirm, Enter loading before picking."""

    def launch_managed(self, tmp_path) -> tuple[App, ModelServer]:
        server = ModelServer(models=("alpha", "beta"), managed=True)
        set_config_provider(tmp_path / "state", server, name="ollama", keep_alive="24h")
        app = launch(tmp_path / "state", server, spec="ollama/alpha")
        return app, server

    def settled_pick(self, app: App) -> str | None:
        """`pick`, but not run until its rows are in. The picker lists
        every provider in the background now, and `run_screen` queues its
        keys before the app starts — so a key would otherwise act on an
        empty list. Built HERE, inside the app session, because
        prompt_toolkit binds an Application's input when it is built; the
        queued keys wait in the pipe meanwhile."""
        supported = api_providers.supported(app.session)
        picker = screen_models.ModelPicker(
            app.session, supported, [], initial_spec="ollama/alpha", fetch=["ollama"]
        )
        deadline = time.monotonic() + 5
        while picker.pending and time.monotonic() < deadline:
            time.sleep(0.02)
        return picker.run()

    def test_l_loads_the_model_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            with contextlib.suppress(EOFError):
                run_screen("ly" + ESC, lambda: self.settled_pick(app))
            assert server.loaded == {"alpha"}
            # The load request carried the provider's keep_alive.
            load = next(r for r in server.requests if r.get("prompt") == "")
            assert load["keep_alive"] == "24h"
        finally:
            app.close()
            server.close()

    def test_the_inventory_reports_live_windows_only(self, tmp_path) -> None:
        # The picker's context column reads these rows: a loaded model's
        # window from /api/ps, and NOTHING for an unloaded one — Ollama
        # sizes a window at load time from a server-wide default nothing
        # exposes, so the card's figure would be a ceiling, not the window.
        app, server = self.launch_managed(tmp_path)
        server.loaded = {"alpha"}
        server.contexts["alpha"] = 32768
        server.gets.clear()
        try:
            rows, _ = api_providers.get_providers(app.session)
            ollama = next(r for r in rows if r.id == "ollama")
            by_name = {m.name: m for m in ollama.models}
            assert by_name["alpha"].max_context_loaded == 32768
            assert by_name["beta"].max_context_loaded is None
            assert by_name["alpha"].size == 1_000_000
            # One pass over each native endpoint, however long the list,
            # and no per-model card lookups (the harness's generic
            # provider shares the port; its /v1/models is not ollama's).
            assert [p for p in server.gets if "/api/" in p] == ["/api/tags", "/api/ps"]
            assert all("messages" in r or "prompt" in r for r in server.requests)
        finally:
            app.close()
            server.close()

    def test_an_unloaded_models_window_is_unknown_until_it_loads(self, tmp_path) -> None:
        # The budget asks the instance, live: unknown before the load,
        # the served window after, unknown again once unloaded — nothing
        # is pinned on a model that loads a moment later.
        app, server = self.launch_managed(tmp_path)
        try:
            assert app.session.max_context() is None
            server.loaded = {"alpha"}
            server.contexts["alpha"] = 4096
            assert app.session.max_context() == 4096
            server.loaded = set()
            assert app.session.max_context() is None
        finally:
            app.close()
            server.close()

    def test_u_unloads_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        server.loaded = {"alpha"}
        try:
            with contextlib.suppress(EOFError):
                run_screen("uy" + ESC, lambda: self.settled_pick(app))
            assert server.loaded == set()
            unload = next(r for r in server.requests if r.get("keep_alive") == 0)
            assert unload["model"] == "alpha"
        finally:
            app.close()
            server.close()

    def test_rows_landing_above_the_cursor_leave_it_on_the_model(self, tmp_path) -> None:
        # Rows land in PROVIDER order, not arrival order: the generic
        # provider is first in the panel and here the last to answer, so
        # its rows land ABOVE the remembered model's — a model that is
        # not its provider's first row would otherwise lose the cursor to
        # whatever slid under it. The cursor follows the model.
        server = ModelServer(models=("alpha", "beta"), managed=True)
        server.list_delay = 0.5  # /v1/models, the generic listing, answers after /api/tags
        root = tmp_path / "state"
        set_config_provider(root, server, name="ollama", keep_alive="24h")
        set_config_provider(root, server, name="generic")
        app = launch(root, server, spec="ollama/beta")

        def settled() -> str | None:
            supported = api_providers.supported(app.session)
            picker = screen_models.ModelPicker(
                app.session, supported, [], initial_spec="ollama/beta", fetch=["ollama", "generic"]
            )
            deadline = time.monotonic() + 5
            while picker.pending and time.monotonic() < deadline:
                time.sleep(0.02)
            assert [e.full_spec for e in picker.filtered] == [
                "generic/alpha",
                "generic/beta",
                "ollama/alpha",
                "ollama/beta",
            ]
            assert picker.filtered[picker.cursor].full_spec == "ollama/beta"
            return picker.run()

        try:
            notice = run_screen(ENTER, settled)
            assert notice is not None and "ollama/beta" in notice
        finally:
            app.close()
            server.close()

    def test_enter_on_a_not_loaded_model_loads_it_first(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            notice = run_screen(ENTER, lambda: self.settled_pick(app))
            # The pick EXECUTES the switch and answers with its notice —
            # here a no-op notice, since the launch already stood on it.
            assert notice is not None and "ollama/alpha" in notice
            assert "alpha" in server.loaded  # switched only after the load
        finally:
            app.close()
            server.close()


class TestProviderPanel:
    """The picker's right side: the app's backends in a fixed order,
    each with an editable URL and API key — Tab over, ↑/↓ between
    fields, Enter to edit in place. Editing a backend that is not in
    the config yet writes its section; the first field is the Generic
    OpenAI provider's URL — a section nothing wrote yet, so every edit
    here founds it."""

    def test_an_edited_url_lands_in_config_and_the_session(self, app: App) -> None:
        # Tab to the panel; Enter edits the generic provider's URL
        # (prefilled with the configured value, here none); Ctrl+U
        # clears; the new url is typed; Enter saves.
        keys = "\t" + ENTER + "\x15" + "http://localhost:7777/v1" + ENTER + ESC + ESC
        picked = run_screen(keys, lambda: screen_models.pick(app.session))
        assert picked is None
        raw = app.paths.providers_file.read_text()
        assert 'url = "http://localhost:7777/v1"' in raw
        assert "[ollama]" in raw  # the other sections survived
        assert api_providers.section(app.session, "generic").url == "http://localhost:7777/v1"

    def test_a_section_name_cannot_write_rows_of_its_own(self, app: App) -> None:
        """The name reaches this from a request (the page PATCHes
        `/api/providers/{provider}`), so a header built by concatenation
        would let any row be written anywhere in the file — repointing a
        configured provider's url at another host, which is where the
        next turn would send its api key."""
        hostile = 'pwn]\n[openrouter]\nurl = "http://attacker"\n[x'
        before = app.paths.providers_file.read_text()
        # A section is its provider's name, so a name no supported provider answers to
        # is refused before anything is written — nothing conjured out
        # of its rows, and the file as it was.
        with pytest.raises(Refused):
            api_providers.save_field(app.session, hostile, "url", "http://evil")
        assert app.paths.providers_file.read_text() == before
        parsed = tomllib.loads(before)
        assert "openrouter" not in parsed
        assert parsed["generic"]["url"] == app.server.url

    def test_ctrl_v_sets_a_key_in_one_press(self, app: App, monkeypatch) -> None:
        # An api key is pasted, never typed: Ctrl+V on the highlighted
        # field is the whole gesture — no Enter to open it, none to save.
        monkeypatch.setattr(clipboard, "paste", lambda: "sk-pasted")
        keys = "\t" + _DOWN + CTRL_V + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "sk-pasted"

    def test_a_terminal_paste_sets_the_field_too(self, app: App) -> None:
        # On macOS the gesture is Cmd+V, which the terminal delivers as a
        # bracketed paste — the app never sees a control byte, so the key
        # binding alone would leave the field untouched.
        keys = "\t" + _DOWN + pasted("sk-from-cmd-v") + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "sk-from-cmd-v"

    def test_a_paste_inside_the_editor_stays_an_ordinary_paste(self, app: App) -> None:
        # Open, the user is composing: the clipboard goes in at the cursor
        # and Enter is still what saves — only a CLOSED field is set.
        keys = "\t" + _DOWN + ENTER + "head-" + pasted("tail") + ENTER + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "head-tail"

    def test_ctrl_v_sets_a_url_the_same_way(self, app: App, monkeypatch) -> None:
        monkeypatch.setattr(clipboard, "paste", lambda: "http://localhost:7777/v1")
        run_screen("\t" + CTRL_V + ESC + ESC, lambda: screen_models.pick(app.session))
        assert 'url = "http://localhost:7777/v1"' in app.paths.providers_file.read_text()
        assert api_providers.section(app.session, "generic").url == "http://localhost:7777/v1"

    def test_a_saved_api_key_is_sealed_never_plain(self, app: App) -> None:
        keys = "\t" + _DOWN + ENTER + "hunter-2" + ENTER + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        raw = app.paths.providers_file.read_text()
        assert "hunter-2" not in raw  # never plain text in the config
        entry = tomllib.loads(raw)["generic"]
        assert entry["url"] == app.server.url  # the key edit left the url alone
        assert entry["api_key"].startswith("sealed:")
        assert _unsealed(app, entry["api_key"]) == "hunter-2"
        # The running session got the plain key at once...
        assert api_providers.section(app.session, "generic").api_key == "hunter-2"
        # ...and the next launch resolves it back from the sealed value
        # (the session opened as the launcher opens it: the harness's own
        # launch would re-seed its section's key).
        relaunched = backend_launch.open_session(app.paths.root)
        try:
            assert api_providers.section(relaunched, "generic").api_key == "hunter-2"
        finally:
            relaunched.close()

    def test_delete_outside_the_editor_clears_the_url_too(self, app: App) -> None:
        # Two rows down is llama.cpp's URL, which the harness pre-seeds:
        # Delete forgets it in the file and the session both, and the
        # provider, with nowhere to ask, is no longer connected.
        keys = "\t" + _DOWN + _DOWN + _DEL + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        raw = app.paths.providers_file.read_text()
        assert tomllib.loads(raw)["llamacpp"]["url"] == ""
        assert api_providers.section(app.session, "llamacpp").url == ""
        _, reachable = api_providers.get_providers(app.session)
        assert "llamacpp" not in reachable

    def test_delete_outside_the_editor_clears_a_saved_key(self, app: App) -> None:
        keys = "\t" + _DOWN + ENTER + "hunter-2" + ENTER + _DEL + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.session))
        raw = app.paths.providers_file.read_text()
        assert tomllib.loads(raw)["generic"]["api_key"] == ""
        assert api_providers.section(app.session, "generic").api_key == ""


class TestGenericProvider:
    """The generic provider: the protocol alone, by url and key — first
    in the panel, and unable to say where its server runs."""

    def test_it_is_first_in_the_panel_and_cannot_say_where_it_runs(self, app: App) -> None:
        supported = api_providers.supported(app.session)
        assert supported[0].id == "generic"
        assert supported[0].locality is Locality.UNKNOWN
        # The supported providers know: the ones on this machine, the catalogs.
        by_name = {p.id: p.locality for p in supported}
        assert by_name["llamacpp"] is Locality.LOCAL
        assert by_name["openrouter"] is Locality.REMOTE

    def test_the_listing_reads_a_window_the_server_sends(self, tmp_path) -> None:
        # /models is the protocol's; `context_length` on a row is the
        # catalogs' extension — read when present, unknown otherwise, and
        # what the listing carried the budget need not refetch.
        server = ModelServer(models=("a", "b"))
        server.contexts["a"] = 32_000
        try:
            set_config_provider(tmp_path / "state", server, name="generic")
            app = launch(tmp_path / "state", server, spec="generic/a")
            try:
                rows, reachable = api_providers.get_providers(app.session)
                assert "generic" in reachable
                generic = next(r for r in rows if r.id == "generic")
                # A catalog row has no load state.
                assert [(m.name, m.max_context_catalogue, m.state) for m in generic.models] == [
                    ("a", 32_000, ModelState.UNKNOWN),
                    ("b", None, ModelState.UNKNOWN),
                ]
                assert generic.locality is Locality.UNKNOWN
                assert app.session.max_context() == 32_000
            finally:
                app.close()
        finally:
            server.close()

    def test_the_generic_section_is_the_generic_engine(self, app: App) -> None:
        # The harness's own provider is the [generic] section: a url and
        # nothing more, which cannot say where it points.
        rows, _ = api_providers.get_providers(app.session)
        mine = next(r for r in rows if r.id == "generic")
        assert mine.locality is Locality.UNKNOWN

    def test_a_section_under_any_other_name_is_passed_over_and_said_so(
        self, server, tmp_path
    ) -> None:
        # A section is its provider's name; one under any other name is
        # not served — the file keeps it, the launch names it.
        set_config_provider(tmp_path / "state", server, name="mybox")
        app = launch(tmp_path / "state", server)
        try:
            assert "mybox" not in api_providers.configured(app.session)
            assert any("[mybox]" in notice for notice in app.session.notices)
            assert tomllib.loads(app.paths.providers_file.read_text())["mybox"]
        finally:
            app.close()

    def test_the_launch_never_lists_the_generic_provider_for_its_window(self, tmp_path) -> None:
        # Its url could name a catalog across the internet: the header's
        # window comes from the cache alone, None before a listing warmed it.
        server = ModelServer(models=("a",))
        server.contexts["a"] = 32_000
        try:
            set_config_provider(tmp_path / "state", server, name="generic")
            app = launch(tmp_path / "state", server, spec="generic/a")
            try:
                asked = len(server.gets)
                assert app.session.max_context() is None
                assert server.gets[asked:] == []
                api_providers.get_providers(app.session)
                assert app.session.max_context() == 32_000
            finally:
                app.close()
        finally:
            server.close()


class TestLlamaCpp:
    def test_the_window_is_asked_once_for_every_row(self, tmp_path) -> None:
        # The server fronts one model, and its window is the server's —
        # so a listing that came back long (a catalog url pasted into the
        # section) still costs one probe, stamped on every row, not one
        # per name.
        server = ModelServer(models=("a", "b", "c"))
        server.window = 4096
        try:
            set_config_provider(tmp_path / "state", server, name="llamacpp")
            app = launch(tmp_path / "state", server, spec=None)
            try:
                rows, _ = api_providers.get_providers(app.session)
                llama = next(r for r in rows if r.id == "llamacpp")
                assert [m.max_context_loaded for m in llama.models] == [4096, 4096, 4096]
                assert sum(p.endswith("/props") for p in server.gets) == 1
            finally:
                app.close()
        finally:
            server.close()


class TestKoboldCpp:
    def test_the_window_is_asked_once_for_every_row(self, tmp_path) -> None:
        # The same rule as llama.cpp's, held separately: this provider lists
        # its own way (the prefix, admin mode's active model).
        server = ModelServer(models=("koboldcpp/a", "koboldcpp/b", "koboldcpp/c"))
        server.window = 2048
        try:
            set_config_provider(tmp_path / "state", server, name="koboldcpp")
            app = launch(tmp_path / "state", server, spec=None)
            try:
                rows, _ = api_providers.get_providers(app.session)
                kobold = next(r for r in rows if r.id == "koboldcpp")
                assert [m.max_context_loaded for m in kobold.models] == [2048, 2048, 2048]
                assert sum(p.endswith("/true_max_context_length") for p in server.gets) == 1
            finally:
                app.close()
        finally:
            server.close()

    def test_the_engines_own_prefix_leaves_the_model_name(self, tmp_path) -> None:
        server = ModelServer(models=("koboldcpp/tiny",))
        try:
            set_config_provider(tmp_path / "state", server, name="koboldcpp")
            app = launch(tmp_path / "state", server, spec="koboldcpp/tiny")
            try:
                rows, _ = api_providers.get_providers(app.session)
                kobold = next(r for r in rows if r.id == "koboldcpp")
                assert [m.name for m in kobold.models] == ["tiny"]
            finally:
                app.close()
        finally:
            server.close()


class TestCloudProviders:
    """A cloud catalog (openrouter, nanogpt): the standard listing, every
    row simply available, context windows straight from the catalog."""

    def test_the_catalog_lists_with_context_and_no_sizes(self, tmp_path) -> None:
        server = ModelServer(models=("gpt-alpha", "gpt-beta"))
        server.contexts["gpt-alpha"] = 128_000
        try:
            set_config_provider(tmp_path / "state", server, name="openrouter")
            app = launch(tmp_path / "state", server, spec="openrouter/gpt-alpha")
            try:
                rows, _ = api_providers.get_providers(app.session)
                catalog = next(r for r in rows if r.id == "openrouter")
                assert catalog.capabilities.model_management is False
                by_name = {m.name: m for m in catalog.models}
                assert by_name["gpt-alpha"].max_context_catalogue == 128_000
                assert by_name["gpt-alpha"].size is None
                assert all(m.state is ModelState.UNKNOWN for m in catalog.models)
                # The listing seeded the context cache — a chat-time
                # lookup answers without refetching the catalog.
                fetches = len(app.server.requests)
                app.play("A line for the catalog model.")
                sent = app.server.requests[fetches:]
                assert all("models" not in str(r.get("path", "")) for r in sent)
            finally:
                app.close()
        finally:
            server.close()

    def test_a_dead_catalog_exits_quietly(self, tmp_path, capsys) -> None:
        # A cloud-only setup whose catalog is down: the picker opens,
        # nothing ever arrives, and leaving says nothing — the caller
        # owns the meaning of a missing model.
        dead = ModelServer()
        dead.close()  # the port answers nobody now
        app = launch(tmp_path / "state", dead, spec=None)
        try:
            capsys.readouterr()
            # The pick's pre-screen sweep waits out the dead providers'
            # connect timeouts (up to ~5s on macOS, where a closed local
            # port hangs rather than refuses) BEFORE the screen opens —
            # the launch's own behavior; the patience covers it.
            result = run_screen(ESC, lambda: screen_models.pick(app.session), patience=8)
            assert result is None
            assert capsys.readouterr().out == ""
        finally:
            app.close()

    def test_a_panel_added_provider_is_switchable_at_once(self, app: App) -> None:
        # A provider that was not in the config at launch is added by
        # the panel's own save, and /model switches to it at once — the
        # save lands in the running registry in the same call.
        api_providers.save_field(app.session, "nanogpt", "url", app.server.url)
        app.play("/model nanogpt/test-model")
        assert app.session.provider == "nanogpt"
        assert app.session.model == "test-model"

    def test_the_picker_does_not_wait_for_the_catalogs(self, server, tmp_path) -> None:
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server)
        try:
            # The blocking pass skips the catalogs entirely...
            rows, reachable = api_providers.get_providers(app.session, skip={"openrouter"})
            assert all(row.id != "openrouter" for row in rows)
            assert "openrouter" not in reachable
            # ...and the picker fetches them after opening: the rows land
            # in the background and the pending mark drains.
            supported = api_providers.supported(app.session)
            picker = screen_models.ModelPicker(app.session, supported, [], fetch=["openrouter"])
            deadline = time.monotonic() + 5
            while picker.pending and time.monotonic() < deadline:
                time.sleep(0.02)
            assert picker.pending == set()
            assert {e.full_spec for e in picker.all} == {"openrouter/test-model"}
        finally:
            app.close()

    def test_nanogpt_asks_for_the_detailed_listing(self, tmp_path) -> None:
        server = ModelServer(models=("gpt-alpha",))
        server.contexts["gpt-alpha"] = 64_000
        try:
            set_config_provider(tmp_path / "state", server, name="nanogpt")
            app = launch(tmp_path / "state", server, spec="nanogpt/gpt-alpha")
            try:
                rows, _ = api_providers.get_providers(app.session)
                nano = next(r for r in rows if r.id == "nanogpt")
                assert [m.max_context_catalogue for m in nano.models] == [64_000]
            finally:
                app.close()
        finally:
            server.close()


def _settled(done, deadline: float = 5.0) -> None:
    """Poll until `done()` or the deadline — background picker work."""
    end = time.monotonic() + deadline
    while not done() and time.monotonic() < end:
        time.sleep(0.02)


def _unsealed(app: App, value: str) -> str:
    """A sealed providers.toml value opened over the state dir's file
    key — the way the launch opens it."""
    return unseal(value, key_file=app.paths.config_key_file, service="scenario:none")


def _record_rings(monkeypatch) -> list[str]:
    """Every sound the terminal asked for — patched where the stream
    reads it, so a test never actually makes a noise."""
    rung: list[str] = []
    monkeypatch.setattr(stream, "ring", rung.append)
    return rung
