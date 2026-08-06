"""The model and the knobs: /model, /set think, /set verbose,
/set parameter — and what each remembers across a relaunch.

The design: `/set think` and `/set verbose` are session-wide and persist
in the app's own state; `/set parameter` follows the MODEL it was set on;
a model switch keeps the story context and is remembered as last used.
"""

import contextlib
import secrets
import time
import tomllib

from otaku.chat.session import TUI
from otaku.paths import Paths
from otaku.providers.registry import Registry
from otaku.settings import config, sealed
from otaku.tui import models
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import ENTER, ESC, run_screen
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
        app.play("/model test/other-model")
        app.play("I look around.")
        assert app.server.requests[-1]["model"] == "other-model"
        # The story context traveled with the switch.
        assert [m.body for m in app.session.messages][:2] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]

    def test_the_switch_is_remembered_as_last_used(self, app: App) -> None:
        app.play("/model test/other-model")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.model == "other-model"
        relaunched.close()

    def test_a_model_name_with_a_quote_survives_the_state_file(self, app: App) -> None:
        app.play('/model test/oddly"named')
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.model == 'oddly"named'
        relaunched.close()

    def test_switching_to_the_same_model_says_so(self, app: App, capsys) -> None:
        app.play("/model test/test-model")
        assert "Already using test/test-model." in capsys.readouterr().out

    def test_an_unknown_provider_is_refused_with_the_known_ones(self, app: App, capsys) -> None:
        app.play("/model nowhere/some-model")
        out = capsys.readouterr().out
        assert "test" in out  # the configured providers are listed
        assert app.session.model == "test-model"

    def test_bare_model_opens_the_picker_on_the_current_model(self, app: App) -> None:
        opened_on: list[str] = []

        def pick(current: str) -> str:
            opened_on.append(current)
            return "test/other-model"

        app.session.tui = TUI(pick_model=pick)
        app.play("/model")
        assert opened_on == ["test/test-model"]
        assert app.session.model == "other-model"

    def test_cancelling_the_picker_keeps_the_model(self, app: App) -> None:
        app.session.tui = TUI(pick_model=lambda current: None)
        app.play("/model")
        assert app.session.model == "test-model"


class TestModelPicker:
    def test_enter_picks_the_highlighted_model(self, app: App) -> None:
        spec = run_screen(ENTER, lambda: models.pick(app.session.providers, paths=app.paths))
        assert spec == "test/test-model"

    def test_esc_cancels_without_picking(self, app: App) -> None:
        assert run_screen(ESC, lambda: models.pick(app.session.providers, paths=app.paths)) is None

    def test_the_cursor_restores_to_the_last_used_model(self, tmp_path) -> None:
        server = ModelServer(models=("alpha", "beta"))
        try:
            app = launch(tmp_path / "state", server)
            try:
                registry = app.session.providers
                first = run_screen(ENTER, lambda: models.pick(registry, paths=app.paths))
                assert first == "test/alpha"
                resumed = run_screen(
                    ENTER, lambda: models.pick(registry, initial_spec="test/beta", paths=app.paths)
                )
                assert resumed == "test/beta"
            finally:
                app.close()
        finally:
            server.close()

    def test_the_filter_narrows_the_list(self, tmp_path) -> None:
        server = ModelServer(models=("alpha", "beta"))
        try:
            app = launch(tmp_path / "state", server)
            try:
                registry = app.session.providers
                spec = run_screen(
                    f"/bet{ENTER}{ENTER}", lambda: models.pick(registry, paths=app.paths)
                )
                assert spec == "test/beta"
            finally:
                app.close()
        finally:
            server.close()


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

    def test_bare_shows_the_current_level(self, app: App, capsys) -> None:
        app.play("/set think low")
        capsys.readouterr()
        app.play("/set think")
        assert "low" in capsys.readouterr().out

    def test_an_unknown_level_shows_the_usage(self, app: App, capsys) -> None:
        app.play("/set think enormous")
        assert "Usage" in capsys.readouterr().out
        assert app.session.think == "none"  # unchanged from the default

    def test_the_level_rides_the_wire_and_default_sends_nothing(self, app: App) -> None:
        app.play("/set think low")
        app.play("I enter the hall.")
        assert app.server.requests[-1]["reasoning_effort"] == "low"
        app.play("/set think default")
        app.play("I look around.")
        assert "reasoning_effort" not in app.server.requests[-1]

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

    def test_omlx_translates_the_level_to_its_template_flag(self, server, tmp_path) -> None:
        # omlx ignores reasoning_effort; thinking is gated by the chat
        # template's enable_thinking flag — a level enables, off disables,
        # default sends nothing.
        set_config_provider(tmp_path / "state", server, name="omlx")
        app = launch(tmp_path / "state", server, spec="omlx/test-model")
        try:
            app.play("/set think high")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            assert body["chat_template_kwargs"] == {"enable_thinking": True}
            assert "reasoning_effort" not in body
            app.play("/set think off")
            app.play("I look around.")
            assert app.server.requests[-1]["chat_template_kwargs"] == {"enable_thinking": False}
            app.play("/set think default")
            app.play("We walk on.")
            assert "chat_template_kwargs" not in app.server.requests[-1]
        finally:
            app.close()

    def test_a_provider_without_thinking_refuses_the_knob(self, server, tmp_path, capsys) -> None:
        # KoboldCpp has no request-level thinking knob — class knowledge,
        # not configuration, so no config flag can turn it on.
        set_config_provider(tmp_path / "state", server, name="koboldcpp")
        plain = launch(tmp_path / "state", server, spec="koboldcpp/test-model")
        try:
            plain.play("/set think high")
            assert "not supported" in capsys.readouterr().out
            assert plain.session.think != "high"
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
        app.play("/model test/other-model")
        app.play("/set parameter top_p 0.5")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["top_p"] == 0.5
        assert "temperature" not in body
        # And switching back restores the first model's own parameters.
        app.play("/model test/test-model")
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

    def test_bare_set_shows_the_usage(self, app: App, capsys) -> None:
        app.play("/set")
        assert "Usage" in capsys.readouterr().out


class TestVerbose:
    def test_verbose_adds_the_stats_line_after_a_reply(self, app: App, capsys) -> None:
        app.play("/set verbose on")
        app.play("I enter the hall.")
        assert "[ total" in capsys.readouterr().out

    def test_off_removes_it(self, app: App, capsys) -> None:
        app.play("/set verbose on")
        app.play("/set verbose off")
        capsys.readouterr()
        app.play("I enter the hall.")
        assert "[ total" not in capsys.readouterr().out

    def test_the_toggle_is_remembered(self, app: App) -> None:
        app.play("/set verbose on")
        relaunched = launch(app.paths.root, app.server)
        assert relaunched.session.verbose is True
        relaunched.close()


class TestManagedPicker:
    """The picker over a managed backend (a scripted ollama): load state
    on screen, l/u with a confirm, Enter loading before picking."""

    def launch_managed(self, tmp_path) -> tuple[App, ModelServer]:
        server = ModelServer(models=("alpha", "beta"), managed=True)
        set_config_provider(tmp_path / "state", server, name="ollama", keep_alive="24h")
        app = launch(tmp_path / "state", server, spec="ollama/alpha")
        return app, server

    def test_l_loads_the_model_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            with contextlib.suppress(EOFError):
                run_screen("ly" + ESC, lambda: models.pick(app.session.providers, paths=app.paths))
            assert server.loaded == {"alpha"}
            # The load request carried the provider's keep_alive.
            load = next(r for r in server.requests if r.get("prompt") == "")
            assert load["keep_alive"] == "24h"
        finally:
            app.close()
            server.close()

    def test_the_inventory_reports_context_windows(self, tmp_path) -> None:
        # The picker's context column reads these rows: the loaded window
        # from /api/ps, the model card's from /api/show otherwise.
        app, server = self.launch_managed(tmp_path)
        server.contexts["alpha"] = 32768
        try:
            rows, _ = app.session.providers.inventory()
            ollama = next(r for r in rows if r.provider_config.name == "ollama")
            models = {m.name: m for m in ollama.models}
            assert models["alpha"].context == 32768
            assert models["beta"].context == 8192  # the scripted default
            assert models["alpha"].size == 1_000_000
        finally:
            app.close()
            server.close()

    def test_u_unloads_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        server.loaded = {"alpha"}
        try:
            with contextlib.suppress(EOFError):
                run_screen("uy" + ESC, lambda: models.pick(app.session.providers, paths=app.paths))
            assert server.loaded == set()
            unload = next(r for r in server.requests if r.get("keep_alive") == 0)
            assert unload["model"] == "alpha"
        finally:
            app.close()
            server.close()

    def test_enter_on_a_not_loaded_model_loads_it_first(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            spec = run_screen(ENTER, lambda: models.pick(app.session.providers, paths=app.paths))
            assert spec == "ollama/alpha"
            assert "alpha" in server.loaded  # picked only after the load
        finally:
            app.close()
            server.close()


class TestProviderPanel:
    """The picker's right side: the app's backends in a fixed order,
    each with an editable URL and API key — Tab over, ↑/↓ between
    fields, Enter to edit in place. Editing a backend that is not in
    the config yet writes its section; the first field is llama.cpp's
    URL."""

    def test_an_edited_url_lands_in_config_and_the_session(self, app: App) -> None:
        # Tab to the panel; Enter edits llama.cpp's URL (prefilled with
        # the configured value); Ctrl+U clears; the new url is typed;
        # Enter saves.
        keys = "\t" + ENTER + "\x15" + "http://localhost:7777/v1" + ENTER + ESC + ESC
        picked = run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
        assert picked is None
        raw = app.paths.providers_file.read_text()
        assert 'url = "http://localhost:7777/v1"' in raw
        assert "[test]" in raw  # the other sections survived
        client = app.session.providers.get_client("llamacpp")
        assert client.provider_config.url == "http://localhost:7777/v1"

    def test_the_editor_moves_the_cursor_and_takes_a_paste(self, app: App) -> None:
        # Ctrl+U, then a bracketed paste of the whole url, then the
        # cursor walks left over "/v1" and an X lands mid-string.
        paste = "\x1b[200~http://localhost:9999/v1\x1b[201~"
        keys = "\t" + ENTER + "\x15" + paste + _LEFT * 3 + "X" + ENTER + ESC + ESC
        run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
        assert 'url = "http://localhost:9999X/v1"' in app.paths.providers_file.read_text()

    def test_home_and_end_jump_the_editor_cursor(self, app: App) -> None:
        # Ctrl+U, the url typed, Home + an A at the front, End + a Z at
        # the back. Ctrl+A / Ctrl+E are bound to the same jumps.
        typed = "\x15" + "http://x/v1" + _HOME + "A" + _END + "Z"
        keys = "\t" + ENTER + typed + ENTER + ESC + ESC
        run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
        assert 'url = "Ahttp://x/v1Z"' in app.paths.providers_file.read_text()

    def test_a_saved_api_key_is_sealed_never_plain(self, app: App) -> None:
        keys = "\t" + _DOWN + ENTER + "hunter-2" + ENTER + ESC + ESC
        run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
        raw = app.paths.providers_file.read_text()
        assert "hunter-2" not in raw  # never plain text in the config
        entry = tomllib.loads(raw)["llamacpp"]
        assert entry["url"] == "http://127.0.0.1:9/v1"  # the key edit left the url alone
        assert entry["api_key"].startswith("sealed:")
        assert sealed.unseal(app.paths, entry["api_key"]) == "hunter-2"
        # The running session got the plain key at once...
        assert app.session.providers.get_client("llamacpp").provider_config.api_key == "hunter-2"
        # ...and the next launch resolves it back from the sealed value.
        resolved, warnings = sealed.resolve_api_keys(app.paths, config.load(app.paths).providers)
        assert warnings == []
        assert resolved["llamacpp"].api_key == "hunter-2"

    def test_delete_outside_the_editor_clears_a_saved_key(self, app: App) -> None:
        keys = "\t" + _DOWN + ENTER + "hunter-2" + ENTER + _DEL + ESC + ESC
        run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
        raw = app.paths.providers_file.read_text()
        assert tomllib.loads(raw)["llamacpp"]["api_key"] == ""
        assert app.session.providers.get_client("llamacpp").provider_config.api_key == ""

    def test_a_cloud_backend_offers_only_its_key_and_keeps_its_url(self, server, tmp_path) -> None:
        # openrouter pre-pointed at the scripted server so the panel's
        # refresh stays offline. Walking down from the top: the five
        # locals' url+key pairs, then the catalogs offer only their API
        # key — the url field is fixed, so the walk lands on the key.
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server)
        try:
            keys = "\t" + _DOWN * 10 + ENTER + "sk-or-abc" + ENTER + ESC + ESC
            run_screen(keys, lambda: models.pick(app.session.providers, paths=app.paths))
            entry = tomllib.loads(app.paths.providers_file.read_text())["openrouter"]
            assert entry["url"] == server.url  # the key edit left the url alone
            assert entry["api_key"].startswith("sealed:")
        finally:
            app.close()


class TestKoboldCpp:
    def test_the_engines_own_prefix_leaves_the_model_name(self, tmp_path) -> None:
        server = ModelServer(models=("koboldcpp/tiny",))
        try:
            set_config_provider(tmp_path / "state", server, name="koboldcpp")
            app = launch(tmp_path / "state", server, spec="koboldcpp/tiny")
            try:
                client = app.session.providers.get_client("koboldcpp")
                assert [m.name for m in client.models()] == ["tiny"]
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
                rows, _ = app.session.providers.inventory()
                catalog = next(r for r in rows if r.provider_config.name == "openrouter")
                assert catalog.can_load_unload is False
                by_name = {m.name: m for m in catalog.models}
                assert by_name["gpt-alpha"].context == 128_000
                assert by_name["gpt-alpha"].size is None
                assert all(m.loaded for m in catalog.models)
                # The listing seeded the context cache — chat-time
                # lookups never refetch the catalog.
                client = app.session.providers.get_client("openrouter")
                assert client._context_cache.get("gpt-alpha") == 128_000
                assert client.get_context_size("gpt-alpha") == 128_000
            finally:
                app.close()
        finally:
            server.close()

    def test_a_dead_catalog_exits_quietly(self, tmp_path, capsys) -> None:
        # A cloud-only setup whose catalog is down: the picker opens,
        # nothing ever arrives, and leaving says nothing — the caller
        # owns the meaning of a missing model.
        registry = Registry(
            {"openrouter": config.ProviderConfig(name="openrouter", url="http://127.0.0.1:9/v1")}
        )
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        result = run_screen(ESC, lambda: models.pick(registry, paths=paths))
        assert result is None
        assert capsys.readouterr().out == ""

    def test_an_empty_machine_still_opens_the_panel(self, tmp_path) -> None:
        # No provider configured, nothing reachable — the picker still
        # opens: its panel is the only door to entering an api key. Tab
        # over, walk to a key field, save one — the section now exists,
        # the key sealed.
        paths = Paths.resolve(tmp_path / "state")
        paths.ensure_tree()
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
        paths.providers_file.write_text("")  # founded empty, as bootstrap would
        keys = "\t" + _DOWN + ENTER + "key-on-empty" + ENTER + ESC + ESC
        run_screen(keys, lambda: models.pick(Registry({}), paths=paths))
        raw = paths.providers_file.read_text()
        assert "[llamacpp]" in raw  # the panel wrote the section
        assert "key-on-empty" not in raw  # sealed, never plain

    def test_a_panel_added_provider_is_switchable_at_once(self, app: App) -> None:
        # A provider that was not in the config at launch is registered
        # by the panel, and /model switches to it at once — the session's
        # config and the registry share the one providers dict.
        app.session.providers.update_provider(
            config.ProviderConfig(name="nanogpt", url=app.server.url)
        )
        app.play("/model nanogpt/test-model")
        assert app.session.provider_config is not None
        assert app.session.provider_config.name == "nanogpt"
        assert app.session.model == "test-model"

    def test_a_bad_or_deleted_key_clears_the_tick_and_rows(self, server, tmp_path) -> None:
        # OpenRouter's catalog is public, so a listing alone proves
        # nothing: the client validates the key through the balance
        # endpoint. A wrong key — or a deleted one — unlists the
        # provider, tick included.
        server.api_key = "right-key"
        set_config_provider(tmp_path / "state", server, name="openrouter", api_key="right-key")
        app = launch(tmp_path / "state", server)
        try:
            registry = app.session.providers
            picker = models.ModelPicker(registry, [], paths=app.paths, fetch=["openrouter"])
            _settled(lambda: not picker.pending)
            assert "openrouter" in picker.connected
            assert picker.all
            for bad in ("wrong-key", ""):
                registry.update_provider(
                    config.ProviderConfig(name="openrouter", url=app.server.url, api_key=bad)
                )
                picker._refresh_provider("openrouter")
                _settled(lambda: "openrouter" not in picker.connected)
                assert "openrouter" not in picker.connected
                assert not picker.all
        finally:
            app.close()

    def test_a_successful_listing_earns_the_panels_tick(self, server, tmp_path) -> None:
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server)
        try:
            picker = models.ModelPicker(
                app.session.providers, [], paths=app.paths, fetch=["openrouter"]
            )
            deadline = time.monotonic() + 5
            while picker.pending and time.monotonic() < deadline:
                time.sleep(0.02)
            assert "openrouter" in picker.connected
        finally:
            app.close()

    def test_the_picker_does_not_wait_for_the_catalogs(self, server, tmp_path) -> None:
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server)
        try:
            registry = app.session.providers
            # The blocking pass skips the catalogs entirely...
            rows, reachable = registry.inventory(skip={"openrouter"})
            assert all(row.provider_config.name != "openrouter" for row in rows)
            assert "openrouter" not in reachable
            # ...and the picker fetches them after opening: the rows land
            # in the background and the pending mark drains.
            picker = models.ModelPicker(registry, [], paths=app.paths, fetch=["openrouter"])
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
                client = app.session.providers.get_client("nanogpt")
                assert [m.context for m in client.models()] == [64_000]
            finally:
                app.close()
        finally:
            server.close()


def _settled(done, deadline: float = 5.0) -> None:
    """Poll until `done()` or the deadline — background picker work."""
    end = time.monotonic() + deadline
    while not done() and time.monotonic() < end:
        time.sleep(0.02)
