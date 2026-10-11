"""The providers package against a real protocol peer: what each provider
puts on the wire and reads back, one class per feature, the providers
that differ each getting their row.

The chat wire: the transcript as messages, streaming with usage, an
effort on every knob the provider reads, images on the last message, one
retry without the knobs when a 400 refuses them, and the answer filed
under the request. The text wire: the prompt alone, an effort on the
text knobs, the continuation as text. The listing: one pass per provider,
the rows read as far as the native API sees, capabilities decoded from
what each provider reports and None where it reports nothing, the one
model ask reading Ollama's card. The counts, the loads, the keys, the
probe, the balance, and the error family, each with its sentence — and
the provider panel: a section's url and key saved where they live, the
generic provider, and what the panel says of each engine.
"""

import base64
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, Refused
from otaku.encryption import unseal
from otaku.providers import (
    ALL_CLIENTS,
    Chunk,
    DeclinedError,
    Image,
    KeySource,
    Locality,
    ModelCapabilities,
    ModelState,
    ProbeStatus,
    ProviderConfig,
    ProviderError,
    ProviderFailure,
    Reasoning,
    Registry,
    Stats,
    StatusError,
    Text,
    UnauthorizedError,
    UnreachableError,
    probe,
)
from otaku.providers.clients.generic import GenericClient
from otaku.providers.clients.koboldcpp import KoboldCppClient
from otaku.providers.clients.llamacpp import LlamaCppClient
from otaku.providers.clients.lmstudio import LmStudioClient
from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.providers.clients.ollama import OllamaClient
from otaku.providers.clients.omlx import OmlxClient
from otaku.providers.clients.openrouter import OpenRouterClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS, SAMPLER_PARAMS
from otaku.providers.openai.reasoning import ALL_EFFORT_LEVELS
from otaku.terminal.screens import models as screen_models
from otaku.terminal.tty import clipboard
from otaku.web import cert
from scenarios.api.app.test_app import _DOWN, _in_use, _model, _window
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import CTRL_V, ENTER, ESC, pasted, run_screen
from scenarios.support.server import ModelServer

_DEL = "\x1b[3~"

EFFORT_NONE = {"reasoning_effort": "none"}

FLAG_OFF = {"chat_template_kwargs": {"enable_thinking": False}}

TEMPLATE_OFF = {"chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "none"}}

BUDGETS_OFF = {"thinking_budget_tokens": 0, "thinking_budget": 0}

DEAD = "http://127.0.0.1:9/v1"


class TestProviderPanel:
    """The picker's right side: the app's backends in the panel's order,
    each with an editable URL and API key — Tab over, ↑/↓ between
    fields, Enter to edit in place. The cursor opens on the first field;
    `_panel_walk` takes it to a provider's field wherever the panel puts
    it. Editing a backend that is not in the config yet writes its
    section."""

    def test_an_edited_url_lands_in_config_and_the_session(self, app: App) -> None:
        # Walk to the generic provider's URL; Enter edits it (prefilled
        # with the configured value); Ctrl+U clears; the new url is
        # typed; Enter saves.
        keys = _panel_walk(app, "generic", "url") + ENTER + "\x15" + "http://localhost:7777/v1"
        keys += ENTER + ESC + ESC
        picked = run_screen(keys, lambda: screen_models.pick(app.api))
        assert picked is None
        raw = app.paths.providers_file.read_text()
        assert 'url = "http://localhost:7777/v1"' in raw
        assert "[ollama]" in raw  # the other sections survived
        assert app.api.providers.get("generic").url == "http://localhost:7777/v1"

    def test_a_url_saved_as_it_stands_is_saved_with_nothing_to_write(self, app: App) -> None:
        provider = app.api.app.get().model.provider
        url = app.api.providers.get(provider).url
        before = app.paths.providers_file.read_bytes()
        app.api.providers.update(provider, url=url)  # an answer, never "could not be written"
        assert app.paths.providers_file.read_bytes() == before
        assert app.api.providers.get(provider).url == url

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
            app.api.providers.update(hostile, url="http://evil")
        assert app.paths.providers_file.read_text() == before
        parsed = tomllib.loads(before)
        assert "openrouter" not in parsed
        assert parsed["generic"]["url"] == app.server.url

    def test_ctrl_v_sets_a_key_in_one_press(self, app: App, monkeypatch) -> None:
        # An api key is pasted, never typed: Ctrl+V on the highlighted
        # field is the whole gesture — no Enter to open it, none to save.
        monkeypatch.setattr(clipboard, "paste", lambda: "sk-pasted")
        keys = _panel_walk(app, "generic", "api_key") + CTRL_V + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "sk-pasted"

    def test_a_terminal_paste_sets_the_field_too(self, app: App) -> None:
        # On macOS the gesture is Cmd+V, which the terminal delivers as a
        # bracketed paste — the app never sees a control byte, so the key
        # binding alone would leave the field untouched.
        keys = _panel_walk(app, "generic", "api_key") + pasted("sk-from-cmd-v") + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "sk-from-cmd-v"

    def test_a_paste_inside_the_editor_stays_an_ordinary_paste(self, app: App) -> None:
        # Open, the user is composing: the clipboard goes in at the cursor
        # and Enter is still what saves — only a CLOSED field is set.
        keys = _panel_walk(app, "generic", "api_key") + ENTER + "head-" + pasted("tail")
        keys += ENTER + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        entry = tomllib.loads(app.paths.providers_file.read_text())["generic"]
        assert _unsealed(app, entry["api_key"]) == "head-tail"

    def test_ctrl_v_sets_a_url_the_same_way(self, app: App, monkeypatch) -> None:
        monkeypatch.setattr(clipboard, "paste", lambda: "http://localhost:7777/v1")
        keys = _panel_walk(app, "generic", "url") + CTRL_V + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        assert 'url = "http://localhost:7777/v1"' in app.paths.providers_file.read_text()
        assert app.api.providers.get("generic").url == "http://localhost:7777/v1"

    def test_a_saved_api_key_is_sealed_never_plain(self, app: App) -> None:
        keys = _panel_walk(app, "generic", "api_key") + ENTER + "hunter-2" + ENTER + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        raw = app.paths.providers_file.read_text()
        assert "hunter-2" not in raw  # never plain text in the config
        entry = tomllib.loads(raw)["generic"]
        assert entry["url"] == app.server.url  # the key edit left the url alone
        assert entry["api_key"].startswith("sealed:")
        assert _unsealed(app, entry["api_key"]) == "hunter-2"
        # The running app sends the plain key at once...
        app.play("I enter the hall.")
        assert app.server.request_headers[-1]["Authorization"] == "Bearer hunter-2"
        # ...and the next launch resolves it back from the sealed value
        # (the app opened as the launcher opens it: the harness's own
        # launch would re-seed its section's key).
        relaunched = backend_launch.open_app(app.paths.root)
        try:
            story = Api(relaunched).stories.create().value.id
            list(
                Api(relaunched).stories.messages.create(
                    story,
                    line="I look around.",
                    expected_head=Api(relaunched).stories.get(story).head,
                )
            )
            assert app.server.request_headers[-1]["Authorization"] == "Bearer hunter-2"
        finally:
            relaunched.close()

    def test_delete_outside_the_editor_clears_the_url_too(self, app: App) -> None:
        # llama.cpp's URL, which the harness pre-seeds: Delete forgets it
        # in the file and the session both, and the provider, with
        # nowhere to ask, is no longer connected.
        keys = _panel_walk(app, "llamacpp", "url") + _DEL + ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        raw = app.paths.providers_file.read_text()
        assert tomllib.loads(raw)["llamacpp"]["url"] == ""
        assert app.api.providers.get("llamacpp").url == ""
        panel = app.api.providers.list()
        assert "llamacpp" not in panel.reachable
        failed = next(row for row in panel.providers if row.id == "llamacpp")
        assert failed.failure  # and the picker is told why

    def test_delete_outside_the_editor_clears_a_saved_key(self, app: App) -> None:
        keys = _panel_walk(app, "generic", "api_key") + ENTER + "hunter-2" + ENTER + _DEL
        keys += ESC + ESC
        run_screen(keys, lambda: screen_models.pick(app.api))
        raw = app.paths.providers_file.read_text()
        assert tomllib.loads(raw)["generic"]["api_key"] == ""
        assert app.api.providers.get("generic").key_source is None


class TestGenericProvider:
    """The generic provider: the protocol alone, by url and key —
    between the engines on this machine and the catalogs in the panel,
    and unable to say where its server runs."""

    def test_it_sits_between_the_engines_and_the_catalogs_and_cannot_say(self, app: App) -> None:
        listed = list(app.api.providers.list(probe=False).providers)
        ids = [p.id for p in listed]
        assert ids.index("lmstudio") < ids.index("generic") < ids.index("openrouter")
        assert listed[ids.index("generic")].locality is Locality.UNKNOWN
        # The supported providers know: the ones on this machine, the catalogs.
        by_name = {p.id: p.locality for p in listed}
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
                panel = app.api.providers.list()
                rows, reachable = panel.providers, panel.reachable
                assert "generic" in reachable
                generic = next(r for r in rows if r.id == "generic")
                # A catalog row has no load state.
                assert [(m.name, m.context_length, m.state) for m in generic.models] == [
                    ("a", 32_000, ModelState.UNKNOWN),
                    ("b", None, ModelState.UNKNOWN),
                ]
                assert generic.locality is Locality.UNKNOWN
                assert _window(app) == 32_000
            finally:
                app.close()
        finally:
            server.close()

    def test_the_generic_section_is_the_generic_engine(self, app: App) -> None:
        # The harness's own provider is the [generic] section: a url and
        # nothing more, which cannot say where it points.
        rows = app.api.providers.list().providers
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
            listed = app.api.providers.list(probe=False).providers
            assert "mybox" not in {row.id for row in listed if row.configured}
            assert any("[mybox]" in notice for notice in app.api.app.get().launch.notices)
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
                assert _window(app) is None
                assert server.gets[asked:] == []
                app.api.providers.list()
                assert _window(app) == 32_000
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
                rows = app.api.providers.list().providers
                llama = next(r for r in rows if r.id == "llamacpp")
                assert [m.context_loaded for m in llama.models] == [4096, 4096, 4096]
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
                rows = app.api.providers.list().providers
                kobold = next(r for r in rows if r.id == "koboldcpp")
                assert [m.context_loaded for m in kobold.models] == [2048, 2048, 2048]
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
                rows = app.api.providers.list().providers
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
                rows = app.api.providers.list().providers
                catalog = next(r for r in rows if r.id == "openrouter")
                assert catalog.capabilities.model_management is False
                by_name = {m.name: m for m in catalog.models}
                assert by_name["gpt-alpha"].context_length == 128_000
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
            result = run_screen(ESC, lambda: screen_models.pick(app.api), patience=8)
            assert result is None
            assert capsys.readouterr().out == ""
        finally:
            app.close()

    def test_a_panel_added_provider_is_switchable_at_once(self, app: App) -> None:
        # A provider that was not in the config at launch is added by
        # the panel's own save, and /model switches to it at once — the
        # save lands in the running registry in the same call.
        app.api.providers.update("nanogpt", url=app.server.url)
        app.play("/model nanogpt/test-model")
        assert _in_use(app).provider == "nanogpt"
        assert _model(app) == "test-model"

    def test_the_picker_does_not_wait_for_the_catalogs(self, server, tmp_path) -> None:
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server)
        try:
            # The blocking pass skips the catalogs entirely...
            panel = app.api.providers.list(scope="local")
            assert not next(row for row in panel.providers if row.id == "openrouter").asked
            assert "openrouter" not in panel.reachable
            # ...and the picker fetches them after opening: the rows land
            # in the background and the pending mark drains.
            providers = list(app.api.providers.list(probe=False).providers)
            picker = screen_models.ModelPicker(app.api, providers, [], fetch=["openrouter"])
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
                rows = app.api.providers.list().providers
                nano = next(r for r in rows if r.id == "nanogpt")
                assert [m.context_length for m in nano.models] == [64_000]
            finally:
                app.close()
        finally:
            server.close()


class TestChatCompletion:
    def test_the_transcript_streams_with_usage_and_the_params(self, server: ModelServer) -> None:
        client = GenericClient(_config(server, "generic"))
        reasoning, text, stats = _drain(
            client.completion.chat("m", [Turn("system", "s"), Turn("user", "u")], {"top_p": 0.5})
        )
        body = server.requests[-1]
        assert body["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        assert body["stream"] is True and body["top_p"] == 0.5
        assert text == scripted.CHAT_REPLY and reasoning == ""
        assert (stats.prompt_tokens, stats.completion_tokens) == (7, 5)
        assert stats.first_token_seconds is not None and stats.total_seconds > 0

    def test_reasoning_streams_before_the_text(self, server: ModelServer) -> None:
        server.script = lambda body: ("hm", "yes")
        chunks = list(
            GenericClient(_config(server, "generic")).completion.chat("m", [Turn("user", "u")], {})
        )
        assert isinstance(chunks[0], Reasoning) and chunks[0].text == "hm"
        assert all(isinstance(c, Text) for c in chunks[1:-1])
        assert isinstance(chunks[-1], Stats)

    def test_cached_tokens_come_off_the_usage_report(self, server: ModelServer) -> None:
        server.cached_tokens = 3
        _, _, stats = _drain(
            GenericClient(_config(server, "generic")).completion.chat("m", [Turn("user", "u")], {})
        )
        assert stats.cached_tokens == 3

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("generic", {**EFFORT_NONE, **TEMPLATE_OFF, **BUDGETS_OFF}),
            ("llamacpp", {**TEMPLATE_OFF, "thinking_budget_tokens": 0}),
            ("koboldcpp", {**EFFORT_NONE, **FLAG_OFF, "thinking_budget_tokens": 0}),
            ("omlx", {**TEMPLATE_OFF, "thinking_budget": 0}),
            ("ollama", EFFORT_NONE),
            ("openrouter", EFFORT_NONE),
            ("nanogpt", EFFORT_NONE),
            ("lmstudio", EFFORT_NONE),
        ],
    )
    def test_an_effort_goes_out_on_the_knobs_the_provider_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        # The table every provider is promised by: the template's flag
        # and word where a local engine forwards them, its own budget
        # where it holds one, the effort alone on the catalogs, Ollama
        # and LM Studio — and the turn plays in every case.
        client = ALL_CLIENTS[kind](_config(server, kind, api_key="k"))
        _, text, _ = _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="none"))
        assert _knobs(_sent(server, "messages")) == expected
        assert text == scripted.CHAT_REPLY

    @pytest.mark.parametrize("kind", ["llamacpp", "omlx"])
    def test_an_engine_that_forwards_the_template_gets_the_effort_as_a_variable(
        self, server: ModelServer, kind: str
    ) -> None:
        # llama.cpp and omlx read no reasoning_effort of their own and
        # hand chat_template_kwargs to the template verbatim: the effort
        # rides there, beside the flag, for the templates that grade
        # their reasoning.
        client = ALL_CLIENTS[kind](_config(server, kind))
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="high"))
        body = _sent(server, "messages")
        assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "high"}
        assert "reasoning_effort" not in body

    def test_the_generic_provider_sends_the_level_on_every_knob(self, server: ModelServer) -> None:
        # Permissive: the url could name any engine, so a rung goes out
        # by name, as the template's flag and as its variable, and none
        # as both budgets too — the off that holds on a template that
        # reads neither — and the server drops what it does not read.
        client = GenericClient(_config(server, "generic"))
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="high"))
        assert _knobs(_sent(server, "messages")) == {
            "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "high"},
        }
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="none"))
        assert _knobs(_sent(server, "messages")) == {
            **EFFORT_NONE,
            **TEMPLATE_OFF,
            **BUDGETS_OFF,
        }

    def test_the_generic_provider_spells_the_penalty_both_ways(self, server: ModelServer) -> None:
        # llama.cpp and LM Studio behind the url read only `repeat_penalty`.
        client = GenericClient(_config(server, "generic"))
        _drain(client.completion.chat("m", [Turn("user", "u")], {"repetition_penalty": 1.1}))
        sent = _sent(server, "messages")
        assert (sent["repetition_penalty"], sent["repeat_penalty"]) == (1.1, 1.1)

    def test_no_effort_sends_no_knob(self, server: ModelServer) -> None:
        _drain(
            GenericClient(_config(server, "generic")).completion.chat("m", [Turn("user", "u")], {})
        )
        assert "reasoning_effort" not in server.requests[-1]
        assert "chat_template_kwargs" not in server.requests[-1]

    def test_a_400_echoing_a_models_name_is_not_a_knob_refusal(self, server: ModelServer) -> None:
        # A model id may spell "thinking" (NanoGPT's `:thinking` models);
        # a 400 that echoes it — a context overflow — names no knob and
        # goes out once, its knobs kept.
        server.refuse = lambda body: 400
        server.refusal = "Your prompt to x:thinking exceeds the model's context length"
        client = GenericClient(_config(server, "generic"))
        with pytest.raises(StatusError):
            _drain(client.completion.chat("x:thinking", [Turn("user", "u")], {}, level="none"))
        assert len(server.requests) == 1

    def test_a_400_naming_the_knob_retries_once_without_it(self, server: ModelServer) -> None:
        # The retry is for a 400 about the knob, as the server words it;
        # one about anything else stands.
        server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        server.refusal = "unknown field: reasoning_effort"
        client = GenericClient(_config(server, "generic"))
        _, text, _ = _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="high"))
        assert text == scripted.CHAT_REPLY
        knobbed, plain = server.requests[-2:]
        assert knobbed["reasoning_effort"] == "high" and "reasoning_effort" not in plain
        server.refusal = "bad request"
        with pytest.raises(StatusError):
            _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="high"))
        assert len(server.requests) == 3

    def test_a_400_after_words_arrived_is_a_failure_not_a_retry(self, server: ModelServer) -> None:
        # A second take would repeat what is already on someone's screen.
        server.fail_after = 1
        stream = GenericClient(_config(server, "generic")).completion.chat(
            "m", [Turn("user", "u")], {}, level="high"
        )
        with pytest.raises(UnreachableError):
            list(stream)
        assert len(server.requests) == 1

    def test_pictures_ride_their_own_message(self, server: ModelServer) -> None:
        image = Image(b"\x89PNG", "image/png")
        _drain(
            GenericClient(_config(server, "generic")).completion.chat(
                "m", [Turn("user", "before"), Turn("user", "look", (image,))], {}
            )
        )
        messages = server.requests[-1]["messages"]
        assert messages[0] == {"role": "user", "content": "before"}
        url = messages[-1]["content"][1]["image_url"]["url"]
        assert messages[-1]["content"][0] == {"type": "text", "text": "look"}
        assert url == "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()

    def test_openrouter_marks_the_cache_where_the_section_allows(self, server: ModelServer) -> None:
        marked = OpenRouterClient(_config(server, "openrouter", api_key="k", prompt_cache="1h"))
        _drain(marked.completion.chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        content = server.requests[-1]["messages"][0]["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        off = OpenRouterClient(_config(server, "openrouter", api_key="k", prompt_cache="off"))
        _drain(off.completion.chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        assert server.requests[-1]["messages"][0]["content"] == "s"

    def test_a_cancel_while_smoothing_cuts_the_wait_for_the_first_token(
        self, server: ModelServer
    ) -> None:
        # With smoothing on the reader waits in a pump thread, and the
        # consumer's cancel must reach the socket — before the first
        # token above all, where llama.cpp and Ollama have not even sent
        # their headers — or the engine finishes the prefill for nobody
        # and the next turn queues behind it. The interrupt arrives the
        # way the terminal's ctrl-c does: in the consumer's thread, while
        # the wrapper ticks.
        sink = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=sink, smooth=True)
        server.headers_delay = 3.0
        try:
            started = time.monotonic()
            stream = client.completion.chat("m", [Turn("user", "u")], {}, on_idle=_interrupt)
            with pytest.raises(_Interrupted):
                next(stream)
            assert _filed(sink, "cancelled", within=1.0)
            assert time.monotonic() - started < 2.0  # never the server's three seconds
        finally:
            server.headers_delay = 0.0

    def test_a_route_gets_only_the_parameters_it_honours(self) -> None:
        # OpenRouter states per route; the wire sends the route's own,
        # the client answers the same set for the menus, and a model
        # not listed gets the wire's whole set.
        server = ModelServer(models=("narrow", "wide"))
        server.extras = {
            "narrow": {"supported_parameters": ["temperature", "max_completion_tokens"]},
            "wide": {},
        }
        try:
            client = OpenRouterClient(_config(server, "openrouter", api_key="k"))
            client.models.list()
            assert client.supported_params_of("narrow") == frozenset({"temperature", "max_tokens"})
            assert client.supported_params_of("wide") == PROTOCOL_PARAMS | SAMPLER_PARAMS
            assert client.supported_params_of("unlisted") == PROTOCOL_PARAMS | SAMPLER_PARAMS
            params = {"temperature": 0.5, "top_k": 4, "max_tokens": 50}
            _drain(client.completion.chat("narrow", [Turn("user", "u")], params))
            body = _sent(server, "messages")
            assert body["temperature"] == 0.5 and "top_k" not in body
            # The reply cap under both its names: a few routes take only the newer.
            assert (body["max_tokens"], body["max_completion_tokens"]) == (50, 50)
            _drain(client.completion.chat("wide", [Turn("user", "u")], params))
            assert _sent(server, "messages")["top_k"] == 4
        finally:
            server.close()

    def test_a_strict_server_refusing_a_sampler_gets_the_take_again_without_them(
        self, server: ModelServer
    ) -> None:
        # OpenAI's own endpoint, behind the generic provider, refuses a
        # field it does not know by name: sent again without the samplers,
        # the protocol's own kept, and the take filed as sent again.
        sink = Sink()
        server.refuse = lambda body: 400 if "top_k" in body else None
        server.refusal = "Unrecognized request argument supplied: top_k"
        client = GenericClient(_config(server, "generic"), request_sink=sink)
        _, text, _ = _drain(
            client.completion.chat(
                "m", [Turn("user", "u")], {"temperature": 0.5, "top_k": 4, "min_p": 0.1}
            )
        )
        assert text == scripted.CHAT_REPLY
        first, second = [r for r in server.requests if "messages" in r][-2:]
        assert first["top_k"] == 4 and "top_k" not in second and "min_p" not in second
        assert second["temperature"] == 0.5
        assert _filed(sink, "ok, sent again without the samplers", within=1.0)

    def test_koboldcpp_sends_one_penalty(self, server: ModelServer) -> None:
        # It has one penalty sampler, presence; frequency is read only
        # in its place, so it is neither advertised nor sent.
        client = KoboldCppClient(_config(server, "koboldcpp"))
        assert "frequency_penalty" not in client.capabilities.supported_params
        params = {"presence_penalty": 0.5, "frequency_penalty": 0.3}
        _drain(client.completion.chat("m", [Turn("user", "u")], params))
        body = _sent(server, "messages")
        assert body["presence_penalty"] == 0.5 and "frequency_penalty" not in body

    def test_the_stats_say_why_the_model_stopped(self, server: ModelServer) -> None:
        # The wire's own word on its last frame: "stop" for the model's
        # end, "length" for a reply cut at the limit — the fact a reader
        # is told of, and the request log files.
        client = GenericClient(_config(server, "generic"))
        _, _, stats = _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert stats.finish_reason == "stop"
        server.finish = "length"
        _, _, stats = _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert stats.finish_reason == "length"
        _, _, stats = _drain(client.completion.text("m", "p", {}))
        assert stats.finish_reason == "length"

    def test_a_closed_reply_keeps_what_its_stats_came_to(self, server: ModelServer) -> None:
        # A reader that lets go mid-reply still holds the stream's stats
        # so far — the wait it spent — whole the moment close() returns,
        # and the stream ended without a word on why.
        server.chunk_delay = 0.3
        client = GenericClient(_config(server, "generic"), smooth=True)
        try:
            reply = client.completion.chat("m", [Turn("user", "u")], {}, on_idle=lambda: None)
            next(c for c in reply if isinstance(c, Text))
            reply.close()
            assert reply.stats.first_token_seconds is not None
            assert reply.stats.total_seconds >= reply.stats.first_token_seconds
            assert reply.stats.finish_reason is None
        finally:
            server.chunk_delay = 0.0

    def test_a_rate_limit_says_who_and_when(self, server: ModelServer) -> None:
        # OpenRouter's 429: the upstream's own words and its name out of
        # the metadata, and the header's wait on the error and in the
        # sentence.
        server.refuse = lambda body: 429
        server.refusal_body = {
            "error": {
                "code": 429,
                "message": "Provider returned error",
                "metadata": {
                    "raw": "temporarily rate-limited upstream",
                    "provider_name": "DeepInfra",
                },
            }
        }
        server.retry_after = "2"
        client = OpenRouterClient(_config(server, "openrouter", api_key="k"))
        with pytest.raises(StatusError) as caught:
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert caught.value.status == 429 and caught.value.retry_after == 2.0
        assert "temporarily rate-limited upstream (DeepInfra)" in caught.value.detail
        assert "Try again in" in str(caught.value)

    def test_a_server_behind_its_own_certificate_is_named_as_such(self, tmp_path) -> None:
        # KoboldCpp's --ssl, a fronted omlx: a certificate the client
        # does not trust is refused as TLS, not as a dead port.
        secured = ModelServer(tls=cert.get_context(tmp_path))
        try:
            client = GenericClient(_config(secured, "generic"))
            with pytest.raises(UnreachableError, match="TLS certificate"):
                client.models.list()
        finally:
            secured.close()

    def test_a_cancel_cuts_an_unwatched_wait_for_the_first_token(self, server: ModelServer) -> None:
        # The background pass's path: unwatched, unpaced, and told. The
        # hook it hands over is ticked while the wait lasts, and a cancel
        # cuts the prefill rather than waiting out the engine's next word.
        sink = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=sink, smooth=False)
        server.headers_delay = 3.0
        try:
            started = time.monotonic()
            stream = client.completion.chat(
                "m", [Turn("user", "u")], {}, watched=False, on_idle=lambda: False
            )
            assert list(stream) == []
            assert _filed(sink, "cancelled", within=1.0)
            assert time.monotonic() - started < 2.0  # never the server's three seconds
        finally:
            server.headers_delay = 0.0

    def test_with_the_pacing_off_a_watched_reply_is_still_ticked(self, server: ModelServer) -> None:
        # The web with smoothing off: the tick is the caller's contract,
        # not the pacing's — a reader that left is still noticed in the
        # wait for the first token.
        sink = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=sink, smooth=False)
        server.headers_delay = 3.0
        try:
            started = time.monotonic()
            stream = client.completion.chat("m", [Turn("user", "u")], {}, on_idle=_interrupt)
            with pytest.raises(_Interrupted):
                next(stream)
            assert _filed(sink, "cancelled", within=1.0)
            assert time.monotonic() - started < 2.0
        finally:
            server.headers_delay = 0.0

    def test_a_cancel_while_smoothing_cuts_a_gap_between_words(self, server: ModelServer) -> None:
        # The web's path: the consumer closes the stream after words
        # came, in a gap the pump is blocked in.
        sink = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=sink, smooth=True)
        server.chunk_delay = 1.5
        try:
            stream = client.completion.chat("m", [Turn("user", "u")], {})
            started = time.monotonic()
            next(c for c in stream if isinstance(c, Text))
            stream.close()
            assert _filed(sink, "cancelled", within=1.0)
            assert time.monotonic() - started < 3.0  # one chunk's wait, not two
        finally:
            server.chunk_delay = 0.0

    def test_the_answer_is_filed_however_the_stream_ends(self, server: ModelServer) -> None:
        sink = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=sink)
        _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        server.chunk_delay = 0.05
        stream = client.completion.chat("m", [Turn("user", "u")], {})
        first = next(c for c in stream if isinstance(c, Text))
        stream.close()
        server.chunk_delay = 0.0
        server.refuse = lambda body: 500
        with pytest.raises(StatusError):
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert sink.statuses[:2] == ["ok", "cancelled"]
        assert sink.statuses[2].startswith("failed: Refused by generic with HTTP 500")
        assert sink.texts[0] == scripted.CHAT_REPLY and sink.texts[1] == first.text


class TestTextCompletion:
    def test_the_prompt_goes_alone_and_the_continuation_comes_as_text(
        self, server: ModelServer
    ) -> None:
        client = GenericClient(_config(server, "generic"))
        reasoning, text, stats = _drain(client.completion.text("m", "Once upon", {"stop": ["\n"]}))
        body = server.requests[-1]
        assert body["prompt"] == "Once upon" and "messages" not in body
        assert body["stop"] == ["\n"] and body["stream"] is True
        assert text == scripted.CHAT_REPLY and reasoning == ""
        assert (stats.prompt_tokens, stats.completion_tokens) == (7, 5)

    def test_nanogpt_counts_the_text_wire_off_its_pricing_block(self, server: ModelServer) -> None:
        # NanoGPT's text wire reports no usage: the counts ride its
        # pricing block on the last frame, and nothing else does.
        server.pricing = (41, 9)
        client = NanoGptClient(_config(server, "nanogpt", api_key="k"))
        _, text, stats = _drain(client.completion.text("m", "Once upon", {}))
        assert text == scripted.CHAT_REPLY
        assert (stats.prompt_tokens, stats.completion_tokens, stats.cached_tokens) == (41, 9, None)

    def test_reasoning_never_arrives_apart_on_the_text_wire(self, server: ModelServer) -> None:
        server.script = lambda body: ("hm", "yes")
        chunks = list(GenericClient(_config(server, "generic")).completion.text("m", "p", {}))
        assert not any(isinstance(c, Reasoning) for c in chunks)

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("generic", {**EFFORT_NONE, **BUDGETS_OFF}),
            ("koboldcpp", {**EFFORT_NONE, "thinking_budget_tokens": 0}),
            ("nanogpt", EFFORT_NONE),
            ("llamacpp", {"thinking_budget_tokens": 0}),
            ("ollama", {}),
            ("omlx", {"thinking_budget": 0}),
            ("lmstudio", {}),
            ("openrouter", {}),
        ],
    )
    def test_an_effort_goes_out_on_the_text_knobs_the_engine_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        client = ALL_CLIENTS[kind](_config(server, kind, api_key="k"))
        _drain(client.completion.text("m", "p", {}, level="none"))
        assert _knobs(_sent(server, "prompt")) == expected


class TestListing:
    def test_koboldcpp_with_nothing_loaded_lists_nothing(self) -> None:
        # "inactive" is the server's own word for no model loaded, listed
        # bare; a model is listed behind the "koboldcpp/" prefix — one
        # whose file is so named included.
        server = ModelServer(models=("inactive",))
        try:
            client = KoboldCppClient(_config(server, "koboldcpp"))
            assert client.models.list() == []
            server.models = ["koboldcpp/inactive"]
            assert [r.name for r in client.models.list()] == ["inactive"]
        finally:
            server.close()

    def test_koboldcpp_judges_the_key_by_the_model_endpoint(self, monkeypatch) -> None:
        # Behind --password the endpoint masks its answer for a key it
        # does not accept: no key configured is one sentence, a wrong
        # one another, and a model whose name merely ends the same way
        # is a model. Masked, `get` can only say unknown.
        monkeypatch.delenv("KOBOLDCPP_API_KEY", raising=False)
        server = ModelServer(models=("koboldcpp/m",))
        server.kobold_model = "koboldcpp/protected-model"
        try:
            with pytest.raises(UnauthorizedError) as bare:
                KoboldCppClient(_config(server, "koboldcpp")).models.list()
            assert str(bare.value) == "No api key for koboldcpp."
            keyed = KoboldCppClient(_config(server, "koboldcpp", api_key="wrong"))
            with pytest.raises(UnauthorizedError) as wrong:
                keyed.models.list()
            assert str(wrong.value) == "The api key was rejected by koboldcpp."
            server.kobold_model = "koboldcpp/m"
            assert keyed.models.get("m") is not None
            server.kobold_model = "koboldcpp/protected-model"
            masked = keyed.models.get("m")
            assert masked is not None and masked.state is ModelState.UNKNOWN
            server.models = ["koboldcpp/my-protected-model"]
            server.kobold_model = "koboldcpp/my-protected-model"
            rows = KoboldCppClient(_config(server, "koboldcpp")).models.list()
            assert [(r.name, r.state) for r in rows] == [("my-protected-model", ModelState.LOADED)]
        finally:
            server.close()

    def test_llamacpp_names_its_one_model_by_the_file_whatever_the_build_lists(self) -> None:
        # A build past b9290 lists the model's whole path; the file name
        # is what every build agrees on, and a single server serves its
        # one model whatever a request names.
        server = ModelServer(models=("/models/qwen.gguf",))
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            assert [r.name for r in client.models.list()] == ["qwen.gguf"]
            assert client.models.get("qwen.gguf") is not None
        finally:
            server.close()

    def test_a_plain_listing_is_ids_and_nothing_read(self) -> None:
        server = ModelServer(models=("b", "a"))
        try:
            rows = LmStudioClient(_config(server, "lmstudio")).models.list()
            assert [r.name for r in rows] == ["a", "b"]
            assert all(r.capabilities is None for r in rows)
            assert all(r.context_length is None and r.context_loaded is None for r in rows)
        finally:
            server.close()

    def test_a_catalog_listing_reads_the_context_and_seeds_it(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.contexts["a"] = 32_000
        try:
            client = GenericClient(_config(server, "generic"))
            rows = {r.name: r for r in client.models.list()}
            assert rows["a"].context_length == 32_000
            assert rows["a"].state is ModelState.UNKNOWN  # a catalog has no load state
            assert rows["b"].context_length is None
            found = client.models.get("a")
            assert found is not None and found.context_length == 32_000
            assert sum(p.endswith("/models") for p in server.gets) == 1
        finally:
            server.close()

    def test_a_keyed_catalog_refuses_without_a_key_that_opens_the_account(
        self, server: ModelServer
    ) -> None:
        server.api_key = "right"
        with pytest.raises(UnauthorizedError):
            OpenRouterClient(_config(server, "openrouter")).models.list()
        with pytest.raises(UnauthorizedError):
            OpenRouterClient(_config(server, "openrouter", api_key="wrong")).models.list()
        assert [
            r.name
            for r in OpenRouterClient(_config(server, "openrouter", api_key="right")).models.list()
        ] == ["test-model"]


class TestCapabilities:
    def test_llamacpp_reads_the_modalities_off_props(self, server: ModelServer) -> None:
        # A build without /apply-template says nothing of the thinking.
        server.window = 4096
        server.props = {"modalities": {"vision": False}}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=False, text_completion=True, structured_output=True
        )
        server.props = {"modalities": {"vision": True}}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=True, text_completion=True, structured_output=True
        )
        # Props without modalities, an older build: still the raw wire
        # and constrained decoding, the rest unknown.
        server.props = {}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=None, text_completion=True, structured_output=True
        )

    def test_llamacpp_asks_the_template_how_the_thinking_is_set(self, server: ModelServer) -> None:
        # The server states nowhere what its template reads, so the
        # template is asked: rendered with the flag on and off and with
        # two efforts, what changes the prompt is what it reads. A flag
        # switches; an effort grades — every rung, none being the
        # budget's off; the server's own budget holds on either. Neither
        # read, the server cannot say — a thinking template it does not
        # switch or grade is still stopped by the budget. Asked once per
        # model, not per listing.
        server.window = 4096
        server.template_reads = {"enable_thinking"}
        client = LlamaCppClient(_config(server, "llamacpp"))
        row = client.models.list()[0]
        assert row.capabilities == ModelCapabilities(
            reasoning_efforts=frozenset(),
            reasoning_switch=True,
            reasoning_budget=True,
            text_completion=True,
            structured_output=True,
        )
        renders = server.posts.count("/apply-template")
        assert renders == 3
        client.models.list()
        assert server.posts.count("/apply-template") == renders
        server.template_reads = {"enable_thinking", "reasoning_effort"}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            reasoning_efforts=ALL_EFFORT_LEVELS,
            reasoning_switch=False,
            reasoning_budget=True,
            text_completion=True,
            structured_output=True,
        )
        server.template_reads = set()
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(text_completion=True, structured_output=True)

    def test_llamacpp_router_asks_the_template_of_a_running_model_only(self) -> None:
        # A render needs the model loaded, and a probe must not load it:
        # a listing asks for the running models, and a model read after
        # its load is asked then.
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded = {"a"}
        server.template_reads = {"enable_thinking"}
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            rows = {r.name: r for r in client.models.list()}
            assert rows["a"].capabilities is not None
            assert rows["a"].capabilities.reasoning_switch is True
            assert rows["b"].capabilities is not None
            assert rows["b"].capabilities.reasoning_switch is None
            server.loaded.add("b")
            fresh = client.models.get("b")
            assert fresh is not None and fresh.capabilities is not None
            assert fresh.capabilities.reasoning_switch is True
        finally:
            server.close()

    def test_koboldcpp_reads_vision_off_its_version_and_knows_its_graded_budget(
        self, server: ModelServer
    ) -> None:
        # Five rungs its budget tells apart, and the budget itself.
        server.version = {"version": "1.120", "vision": False, "audio": True}
        row = KoboldCppClient(_config(server, "koboldcpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=False,
            audio=True,
            reasoning_efforts=frozenset({"none", "minimal", "low", "medium", "high"}),
            reasoning_switch=False,
            reasoning_budget=True,
            text_completion=True,
            structured_output=True,
        )

    def test_koboldcpp_keeps_what_a_missed_probe_cannot_read(self) -> None:
        # The flags and the loaded size are probes: one that does not
        # answer states nothing, and what an earlier one read stands.
        server = ModelServer(models=("koboldcpp/m",))
        server.kobold_model = "koboldcpp/m"
        server.version = {"vision": True}
        server.window = 4096
        try:
            client = KoboldCppClient(_config(server, "koboldcpp"))
            first = client.models.list()[0]
            assert first.capabilities is not None and first.capabilities.vision is True
            assert (first.state, first.context_loaded) == (ModelState.LOADED, 4096)
            server.version = None
            again = client.models.list()[0]
            assert again.capabilities is not None and again.capabilities.vision is True
            server.window = None
            got = client.models.get("m")
            assert got is not None
            assert (got.state, got.context_loaded) == (ModelState.LOADED, 4096)
        finally:
            server.close()

    def test_ollama_reads_the_card_for_one_model_never_in_the_listing_and_lists_no_embedder(
        self,
    ) -> None:
        # The registry rows name capabilities too, but not the card's
        # (a server we have leaves vision off Gemma 4's row): only the
        # one-model ask reads the card, once, and a listing asks none. A
        # row without "completion" (an embedder) plays no story and is
        # not listed. No raw text wire: the completion is a chat turn.
        server = ModelServer(models=("alpha", "beta", "embed"), managed=True)
        server.capabilities["alpha"] = ["completion", "vision", "thinking"]
        server.capabilities["beta"] = ["completion"]
        server.capabilities["embed"] = ["embedding"]
        try:
            client = OllamaClient(_config(server, "ollama"))
            rows = client.models.list()
            assert [r.name for r in rows] == ["alpha", "beta"]
            assert all(r.capabilities is None for r in rows)
            assert not any(b.get("model") for b in server.requests)
            alpha, beta = client.models.get("alpha"), client.models.get("beta")
            assert alpha is not None and beta is not None
            # The card's "thinking" is a switch: no rung, no budget.
            assert alpha.capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                reasoning_efforts=frozenset(),
                reasoning_switch=True,
                reasoning_budget=False,
                text_completion=False,
                structured_output=True,
            )
            assert beta.capabilities == ModelCapabilities(
                vision=False,
                audio=False,
                reasoning_efforts=frozenset(),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=False,
                structured_output=True,
            )
            assert client.models.get("alpha") == alpha  # the row is kept, the card asked once
            assert len([b for b in server.requests if set(b) == {"model"}]) == 2
        finally:
            server.close()

    def test_ollama_lists_the_trained_length_off_the_row_and_no_loaded_embedder(self) -> None:
        # A recent pull records the trained length on the registry row,
        # so the listing states the model's own max context without a
        # card read; an embedder plays no story even while it is loaded,
        # so ps does not bring it back.
        server = ModelServer(models=("alpha", "embed"), managed=True)
        server.capabilities = {"alpha": ["completion"], "embed": ["embedding"]}
        server.contexts["alpha"] = 131_072
        server.loaded.add("embed")
        try:
            rows = OllamaClient(_config(server, "ollama")).models.list()
            assert [(r.name, r.context_length) for r in rows] == [("alpha", 131_072)]
            assert server.requests == []  # no card read
        finally:
            server.close()

    def test_ollama_leaves_every_state_unknown_while_ps_will_not_answer(self) -> None:
        # A missed probe is not an empty runner: nothing is unloaded on
        # its word, and the listing says it cannot tell.
        server = ModelServer(models=("alpha", "beta"), managed=True)
        server.loaded.add("alpha")
        server.ps_status = 503
        try:
            client = OllamaClient(_config(server, "ollama"))
            rows = client.models.list()
            assert {(r.state, r.context_loaded) for r in rows} == {(ModelState.UNKNOWN, None)}
            server.ps_status = None
            states = {r.name: r.state for r in client.models.list()}
            assert states == {"alpha": ModelState.LOADED, "beta": ModelState.UNLOADED}
        finally:
            server.close()

    def test_ollama_reads_audio_off_the_card_and_lists_no_cloud_embedder(self) -> None:
        # ollama.com's rows keep their own capability words and an
        # unknown state; one without "completion" plays no story there
        # either.
        server = ModelServer(models=("alpha", "cloud", "cloud-embed"), managed=True)
        server.capabilities = {
            "alpha": ["completion", "audio"],
            "cloud": ["completion", "vision"],
            "cloud-embed": ["embedding"],
        }
        server.remote = {"cloud", "cloud-embed"}
        try:
            client = OllamaClient(_config(server, "ollama"))
            rows = {r.name: r for r in client.models.list()}
            assert set(rows) == {"alpha", "cloud"}
            assert rows["cloud"].state is ModelState.UNKNOWN
            assert rows["cloud"].capabilities is not None and rows["cloud"].capabilities.vision
            # Served by ollama.com: the row says so, the client answers it
            # for the model, and a one-model read keeps it.
            assert (rows["cloud"].locality, rows["alpha"].locality) == (Locality.REMOTE, None)
            assert client.locality_of("cloud") is Locality.REMOTE
            assert client.locality_of("alpha") is Locality.LOCAL
            assert client.locality_of("unlisted") is Locality.LOCAL
            cloud = client.models.get("cloud")
            assert cloud is not None and cloud.locality is Locality.REMOTE
            assert client.locality_of("cloud") is Locality.REMOTE
            alpha = client.models.get("alpha")
            assert alpha is not None and alpha.capabilities is not None
            assert (alpha.capabilities.audio, alpha.capabilities.vision) == (True, False)
        finally:
            server.close()

    def test_omlx_reads_the_type_and_the_toggle_and_lists_only_language_models(self) -> None:
        # `thinking_default`, a bool, is the template's thinking toggle:
        # on or off, under the server's budget; absent, nothing reaches.
        server = ModelServer(models=("vl", "lm", "unsaid", "emb", "rerank"))
        server.status = True
        server.types = {"vl": "vlm", "lm": "llm", "emb": "embedding", "rerank": "reranker"}
        server.thinking = {"vl": False}
        try:
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert set(rows) == {"vl", "lm", "unsaid"}  # an embedder plays no story
            assert rows["vl"].capabilities == ModelCapabilities(
                vision=True,
                reasoning_efforts=frozenset(),
                reasoning_switch=True,
                reasoning_budget=True,
                text_completion=True,
                structured_output=True,
            )
            # "vlm" is what mlx-vlm serves, which includes two text
            # families and Gemma 4's unified models, seeing or not: the
            # config's own type tells a text model (no) from an untold
            # one (unknown).
            server.config_types = {"vl": "cohere2_moe"}
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert rows["vl"].capabilities is not None and rows["vl"].capabilities.vision is False
            server.config_types = {"vl": "gemma4_unified"}
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert rows["vl"].capabilities is not None and rows["vl"].capabilities.vision is None
            server.config_types = {"vl": "gemma3"}
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert rows["vl"].capabilities is not None and rows["vl"].capabilities.vision is True
            silent = ModelCapabilities(
                reasoning_efforts=frozenset(),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=True,
                structured_output=True,
            )
            assert rows["lm"].capabilities == replace(silent, vision=False)
            # No type on the row: whether it sees is unknown, not assumed.
            assert rows["unsaid"].capabilities == silent
        finally:
            server.close()

    def test_omlx_states_the_cap_a_request_gets_in_every_state(self) -> None:
        # omlx loads on demand under its serving cap, which the status
        # states loaded or not: a story played on an unloaded model is
        # budgeted by the cap, never by a default.
        server = ModelServer(models=("cold", "warm"))
        server.status = True
        server.contexts = {"cold": 60_000, "warm": 131_072}
        server.loaded.add("warm")
        try:
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert (rows["cold"].state, rows["cold"].context_window) == (
                ModelState.UNLOADED,
                60_000,
            )
            assert (rows["warm"].state, rows["warm"].context_window) == (ModelState.LOADED, 131_072)
        finally:
            server.close()

    def test_omlx_lists_nothing_while_its_status_will_not_answer(self) -> None:
        # A 503 while omlx starts is the listing's sentence, never the
        # plain /v1/models names, which offer the embedders the status
        # filters out. A 404 alone is a server without the surface — not
        # omlx — and the names stand.
        server = ModelServer(models=("lm", "emb"))
        server.status = True
        server.types = {"emb": "embedding"}
        server.status_code = 503
        try:
            client = OmlxClient(_config(server, "omlx"))
            with pytest.raises(StatusError) as refused:
                client.models.list()
            assert refused.value.status == 503
            server.status_code = 404
            assert [r.name for r in client.models.list()] == ["emb", "lm"]
        finally:
            server.close()

    def test_openrouter_reads_the_modalities_and_the_reasoning_object(self) -> None:
        server = ModelServer(models=("free", "fixed", "mute", "plain", "forced", "bare"))
        server.extras = {
            "free": {
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["reasoning", "structured_outputs", "temperature"],
                "reasoning": {"mandatory": False, "supported_efforts": ["low", "high", "minimal"]},
            },
            "fixed": {
                "architecture": {"input_modalities": ["text"]},
                "supported_parameters": ["reasoning", "temperature"],
                "reasoning": {"mandatory": True, "supported_efforts": ["low", "medium", "high"]},
            },
            "mute": {"supported_parameters": ["temperature"]},
            "plain": {"supported_parameters": ["reasoning"], "reasoning": {"mandatory": False}},
            "forced": {"supported_parameters": ["reasoning"], "reasoning": {"mandatory": True}},
            "bare": {},
        }
        try:
            rows = {
                r.name: r
                for r in OpenRouterClient(_config(server, "openrouter", api_key="k")).models.list()
            }
            # The text wire is per model and the catalog does not say
            # which take it: unknown on every row.
            # The rungs as named, "none" among them unless reasoning is
            # mandatory, and a budget on every model that reasons —
            # OpenRouter spends either as the model takes it.
            # The route's own parameters, in the app's names: what the
            # wire sends for this model, and what a menu offers.
            assert rows["free"].capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                supported_params=frozenset({"temperature"}),
                reasoning_efforts=frozenset({"none", "minimal", "low", "high"}),
                reasoning_switch=False,
                reasoning_budget=True,
                text_completion=None,
                structured_output=True,
            )
            assert rows["fixed"].capabilities == ModelCapabilities(
                vision=False,
                audio=False,
                supported_params=frozenset({"temperature"}),
                reasoning_efforts=frozenset({"low", "medium", "high"}),
                reasoning_switch=False,
                reasoning_budget=True,
                text_completion=None,
                structured_output=False,
            )
            # No modalities named, no reasoning among the parameters:
            # nothing reaches; what a row does not say is unknown.
            assert rows["mute"].capabilities == ModelCapabilities(
                supported_params=frozenset({"temperature"}),
                reasoning_efforts=frozenset(),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=None,
                structured_output=False,
            )
            # A reasoning object naming no rungs: every rung reaches, as
            # a share of the budget — and not "none" where reasoning is
            # mandatory.
            assert rows["plain"].capabilities == ModelCapabilities(
                supported_params=frozenset(),
                reasoning_efforts=ALL_EFFORT_LEVELS,
                reasoning_switch=False,
                reasoning_budget=True,
                text_completion=None,
                structured_output=False,
            )
            assert rows["forced"].capabilities == ModelCapabilities(
                supported_params=frozenset(),
                reasoning_efforts=ALL_EFFORT_LEVELS - {"none"},
                reasoning_switch=False,
                reasoning_budget=True,
                text_completion=None,
                structured_output=False,
            )
            assert rows["bare"].capabilities == ModelCapabilities()
        finally:
            server.close()

    def test_nanogpt_reads_its_capabilities_and_efforts(self) -> None:
        server = ModelServer(models=("seeing", "fixed", "mute", "unsure", "bare"))
        server.extras = {
            "seeing": {
                "capabilities": {
                    "vision": True,
                    "audio_input": False,
                    "reasoning": True,
                    "structured_output": True,
                },
                "reasoning_efforts": ["low", "high"],
            },
            "fixed": {"capabilities": {"vision": False, "reasoning": True}},
            "mute": {"capabilities": {"vision": False, "reasoning": False}},
            "unsure": {
                "capabilities": {"vision": None, "reasoning": None},
                "max_output_tokens": 4096,
            },
            "bare": {"max_output_tokens": 8192},
        }
        try:
            rows = {
                r.name: r
                for r in NanoGptClient(_config(server, "nanogpt", api_key="k")).models.list()
            }
            # The rungs as listed, "none" among them only where the
            # model takes it; no budget on the catalog; the text wire is
            # per model, unsaid.
            assert rows["seeing"].capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                reasoning_efforts=frozenset({"low", "high"}),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=None,
                structured_output=True,
            )
            # Reasons, but names no rung: on or off.
            assert rows["fixed"].capabilities == ModelCapabilities(
                vision=False,
                reasoning_efforts=frozenset(),
                reasoning_switch=True,
                reasoning_budget=False,
                text_completion=None,
            )
            assert rows["mute"].capabilities == ModelCapabilities(
                vision=False,
                reasoning_efforts=frozenset(),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=None,
            )
            # A null flag is the catalog's "unknown", not a no; the output
            # limit is read whether or not the flags are there.
            assert rows["unsure"].capabilities == ModelCapabilities(text_completion=None)
            assert rows["unsure"].max_output_tokens == 4096
            assert rows["bare"].capabilities is None
            assert rows["bare"].max_output_tokens == 8192
        finally:
            server.close()

    def test_lmstudio_reads_vision_and_the_reasoning_options_off_its_registry(self) -> None:
        # Vision is the registry's word, and a registry that says nothing
        # of it leaves it unknown; the rungs are its `allowed_options`,
        # and a row without the reasoning object says nothing of them.
        server = ModelServer(models=("seeing", "blind", "unsaid"), managed=True)
        server.capabilities = {"seeing": ["vision"], "blind": []}
        server.reasoning_options = {"seeing": ["low", "medium", "high", "unheard-of"]}
        try:
            rows = {r.name: r for r in LmStudioClient(_config(server, "lmstudio")).models.list()}
            assert rows["seeing"].capabilities == ModelCapabilities(
                vision=True,
                reasoning_efforts=frozenset({"low", "medium", "high"}),
                reasoning_switch=False,
                reasoning_budget=False,
                text_completion=True,
                structured_output=True,
            )
            assert rows["blind"].capabilities == ModelCapabilities(
                vision=False, text_completion=True, structured_output=True
            )
            assert rows["unsaid"].capabilities == ModelCapabilities(
                vision=None, text_completion=True, structured_output=True
            )
        finally:
            server.close()

    def test_lmstudio_lists_nothing_while_its_registry_will_not_answer(self) -> None:
        # A refused key or a failing registry is the listing's sentence,
        # never the bare names; a 404 alone is a server without the
        # surface, and the names stand.
        server = ModelServer(models=("a",), managed=True)
        server.api_key = "right"
        try:
            with pytest.raises(UnauthorizedError):
                LmStudioClient(_config(server, "lmstudio", api_key="wrong")).models.list()
            server.api_key = None
            server.managed = False  # the registry is gone: a 404
            assert [r.name for r in LmStudioClient(_config(server, "lmstudio")).models.list()] == [
                "a"
            ]
        finally:
            server.close()

    def test_the_generic_provider_reads_nothing(self, server: ModelServer) -> None:
        assert GenericClient(_config(server, "generic")).models.list()[0].capabilities is None


class TestTokenCounts:
    def test_llamacpp_counts_the_chat_body_and_a_raw_prompt(self, server: ModelServer) -> None:
        server.token_count = 42
        client = LlamaCppClient(_config(server, "llamacpp"))
        assert (
            client.completion.count_chat_tokens("m", [Turn("system", "s"), Turn("user", "u")]) == 42
        )
        counted = server.requests[-1]
        assert counted["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        assert "stream" not in counted
        # Counted as the turn would send it: the knobs beside the body,
        # the pictures on their message — what the template renders.
        client.completion.count_chat_tokens(
            "m", [Turn("user", "u", (Image(b"x", "image/png"),))], level="none"
        )
        counted = server.requests[-1]
        assert counted["chat_template_kwargs"] == {
            "enable_thinking": False,
            "reasoning_effort": "none",
        }
        assert counted["thinking_budget_tokens"] == 0
        assert counted["messages"][-1]["content"] == [
            {"type": "text", "text": "u"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}},
        ]
        assert client.completion.count_text_tokens("m", "raw") == 42
        # As the text wire tokenizes: the special tokens (BOS) counted in.
        assert server.requests[-1] == {"model": "m", "content": "raw", "add_special": True}

    def test_koboldcpp_counts_either_shape(self, server: ModelServer) -> None:
        server.token_count = 7
        client = KoboldCppClient(_config(server, "koboldcpp"))
        assert client.completion.count_chat_tokens("m", [Turn("user", "u")]) == 7
        assert server.requests[-1] == {"messages": [{"role": "user", "content": "u"}]}
        assert client.completion.count_text_tokens("m", "raw") == 7
        assert server.requests[-1] == {"prompt": "raw"}

    def test_omlx_counts_a_loaded_chat_with_the_system_text_apart(
        self, server: ModelServer
    ) -> None:
        # The count resolves the engine, which loads the model: an
        # unloaded one is not counted.
        server.token_count = 9
        server.status = True
        client = OmlxClient(_config(server, "omlx"))
        turns = [Turn("system", "s"), Turn("user", "u")]
        assert client.completion.count_chat_tokens("test-model", turns) is None
        server.loaded.add("test-model")
        assert client.completion.count_chat_tokens("test-model", turns) == 9
        assert server.requests[-1] == {
            "model": "test-model",
            "messages": [{"role": "user", "content": "u"}],
            "system": "s",
        }
        assert client.completion.count_text_tokens("test-model", "raw") is None

    @pytest.mark.parametrize("kind", ["generic", "ollama", "lmstudio", "openrouter", "nanogpt"])
    def test_the_others_count_nothing(self, server: ModelServer, kind: str) -> None:
        server.token_count = 5
        client = ALL_CLIENTS[kind](_config(server, kind, api_key="k"))
        assert client.completion.can_count_tokens is False
        assert client.completion.count_chat_tokens("m", [Turn("user", "u")]) is None
        assert client.completion.count_text_tokens("m", "raw") is None

    def test_a_server_without_the_endpoint_counts_none(self, server: ModelServer) -> None:
        client = LlamaCppClient(_config(server, "llamacpp"))
        assert client.completion.can_count_tokens is True
        assert client.completion.count_chat_tokens("m", [Turn("user", "u")]) is None


class TestLoadUnload:
    def test_omlx_orders_nothing_its_state_already_satisfies(self) -> None:
        # omlx evicts on its own and refuses an unload of what is not
        # loaded: a stale row's Unload is a no-op, not a 400.
        server = ModelServer(models=("a",))
        server.status = True
        try:
            client = OmlxClient(_config(server, "omlx"))
            posted = len(server.requests)
            client.models.unload("a")
            client.models.load("a")
            client.models.load("a")
            assert len(server.requests) == posted + 1  # the one load that was needed
            assert server.loaded == {"a"}
        finally:
            server.close()

    def test_omlx_loads_and_unloads_by_path(self) -> None:
        server = ModelServer(models=("a",))
        server.status = True
        try:
            client = OmlxClient(_config(server, "omlx"))
            assert client.models.can_manage
            client.models.load("a")
            assert client.models.list()[0].state is ModelState.LOADED
            client.models.unload("a")
            assert client.models.list()[0].state is ModelState.UNLOADED
        finally:
            server.close()

    def test_lmstudio_loads_once_and_unloads_every_instance(self) -> None:
        server = ModelServer(models=("a",), managed=True)
        try:
            client = LmStudioClient(_config(server, "lmstudio"))
            client.models.load("a")
            assert "a" in server.loaded
            client.models.unload("a")
            assert "a" not in server.loaded
        finally:
            server.close()

    def test_lmstudio_takes_an_unload_of_what_is_already_gone(self) -> None:
        # LM Studio unloads idle instances on its own: an instance gone
        # between the read and the order is a 404, and the state asked for.
        server = ModelServer(models=("a",), managed=True)
        server.loaded.add("a")
        server.unload_status = 404
        try:
            LmStudioClient(_config(server, "lmstudio")).models.unload("a")
        finally:
            server.close()

    def test_a_llamacpp_router_manages_its_folder_and_waits_for_the_state(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded.add("a")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            assert client.models.can_manage is False  # nothing listed yet
            rows = {r.name: r for r in client.models.list()}
            assert client.models.can_manage is True
            assert rows["a"].state is ModelState.LOADED and rows["b"].state is ModelState.UNLOADED
            client.models.load("b")
            assert server.loaded == {"a", "b"}
            client.models.unload("a")
            assert server.loaded == {"b"}
        finally:
            server.close()

    def test_a_llamacpp_router_reports_a_load_that_failed(self) -> None:
        # The router takes the order and the model's own server dies:
        # the row ends unloaded with the exit code, and so does the call.
        server = ModelServer(models=("a", "broken"))
        server.router = True
        server.router_failed.add("broken")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            client.models.list()
            with pytest.raises(ProviderError) as failed:
                client.models.load("broken")
            assert str(failed.value) == "broken did not load on llamacpp (exit code 1)."
        finally:
            server.close()

    def test_a_llamacpp_router_waits_for_a_load_already_in_flight(self) -> None:
        # The router autoloads on any request for a model, its props
        # probe included; a load order on a row saying "loading" would
        # be refused, so it is waited for instead.
        server = ModelServer(models=("a",))
        server.router = True
        server.router_loading.add("a")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            client.models.list()
            posted = len(server.requests)
            client.models.load("a")
            assert len(server.requests) == posted  # no order, only the wait
            assert server.loaded == {"a"}
        finally:
            server.close()

    def test_a_llamacpp_router_waits_out_a_download(self) -> None:
        # A build past b9290 fetches a model first: the order waits
        # through "downloading" and "loading" alike.
        server = ModelServer(models=("a",))
        server.router = True
        server.router_downloading.add("a")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            client.models.list()
            client.models.load("a")
            assert server.loaded == {"a"}
        finally:
            server.close()

    def test_a_llamacpp_router_row_carries_the_window_the_props_gave(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded.add("a")
        server.window = 4096
        try:
            rows = {r.name: r for r in LlamaCppClient(_config(server, "llamacpp")).models.list()}
            assert rows["a"].context_loaded == 4096
            assert rows["b"].context_loaded is None
        finally:
            server.close()

    def test_a_llamacpp_router_hides_a_projector(self) -> None:
        # A top-level mmproj file is listed by the router as a model,
        # and loading it fails with an exit code; its name is llama.cpp's
        # own rule for a projector.
        server = ModelServer(models=("a", "mmproj-a.gguf"))
        server.router = True
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            assert [r.name for r in client.models.list()] == ["a"]
            assert client.models.get("mmproj-a.gguf") is None
            # Nor is it loadable — nor any name the router does not front:
            # refused before an order that would spawn a doomed child.
            posted = len(server.requests)
            for name in ("mmproj-a.gguf", "nope"):
                with pytest.raises(ProviderError) as refused:
                    client.models.load(name)
                assert str(refused.value) == f"{name} is not offered by llamacpp."
            assert len(server.requests) == posted
        finally:
            server.close()
        # A folder of nothing but a projector is still a router.
        alone = ModelServer(models=("mmproj-a.gguf",))
        alone.router = True
        try:
            client = LlamaCppClient(_config(alone, "llamacpp"))
            assert client.models.list() == []
            assert client.models.can_manage is True
        finally:
            alone.close()

    def test_a_load_ends_when_its_budget_is_spent(self) -> None:
        # A router whose load never ends — a download on demand that
        # stalls — is polled under the order's budget, never forever.
        server = ModelServer(models=("m",))
        server.router = True
        server.router_stuck = {"m"}
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            client.models.list()
            started = time.monotonic()
            with pytest.raises(UnreachableError):
                client.models.load("m", timeout=0.6)
            assert time.monotonic() - started < 2.0
        finally:
            server.close()

    def test_a_load_nobody_waits_for_is_cut(self) -> None:
        # The hook says nobody waits any more — a page that left: the
        # order's request is cut where it blocks, and the load raises as
        # cut short rather than holding the caller for the engine's time.
        server = ModelServer(models=("alpha",), managed=True)
        server.load_delay = 3.0
        try:
            client = OllamaClient(_config(server, "ollama"))
            started = time.monotonic()
            with pytest.raises(ProviderError, match="cut short"):
                client.models.load("alpha", on_idle=lambda: False)
            assert time.monotonic() - started < 2.0
        finally:
            server.close()

    def test_a_llamacpp_router_get_learns_the_models_own_facts_after_a_load(self) -> None:
        # The router states a model's size and max context only while it
        # runs, so a listing before the load reads none; the first `get`
        # after it carries both, without another listing.
        server = ModelServer(models=("a",))
        server.router = True
        server.window = 4096
        server.contexts["a"] = 32768
        server.sizes["a"] = 5_000_000
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            before = client.models.get("a")
            assert before is not None
            assert (before.size, before.context_length) == (None, None)
            client.models.load("a")
            after = client.models.get("a")
            assert after is not None
            assert (after.state, after.context_loaded) == (ModelState.LOADED, 4096)
            assert (after.size, after.context_length) == (5_000_000, 32768)
            # Unloaded, the router states neither again; the listing
            # keeps what `get` learned.
            client.models.unload("a")
            relisted = {r.name: r for r in client.models.list()}
            assert (relisted["a"].size, relisted["a"].context_length) == (5_000_000, 32768)
            assert relisted["a"].context_loaded is None
        finally:
            server.close()

    def test_a_llamacpp_router_counts_a_sleeping_model_as_loaded(self) -> None:
        # Put to sleep idle, woken by the next request: a server is
        # behind it, so it is loaded, and a load order would be refused.
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded.add("a")
        server.router_sleeping.add("a")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            rows = {r.name: r for r in client.models.list()}
            assert rows["a"].state is ModelState.LOADED and rows["b"].state is ModelState.UNLOADED
            posted = len(server.requests)
            client.models.load("a")  # nothing to do, nothing sent
            assert len(server.requests) == posted
            client.models.unload("a")
            assert server.loaded == set()
        finally:
            server.close()

    def test_a_single_llamacpp_server_refuses_a_load(self, server: ModelServer) -> None:
        server.window = 4096
        client = LlamaCppClient(_config(server, "llamacpp"))
        client.models.list()
        assert client.models.can_manage is False
        with pytest.raises(ProviderError, match="cannot be loaded or unloaded"):
            client.models.load("test-model")

    @pytest.mark.parametrize("kind", ["generic", "koboldcpp", "openrouter", "nanogpt"])
    def test_the_others_refuse_a_load(self, server: ModelServer, kind: str) -> None:
        client = ALL_CLIENTS[kind](_config(server, kind))
        assert client.models.can_manage is False
        with pytest.raises(ProviderError):
            client.models.load("m")


class TestUrls:
    def test_a_local_engines_url_is_the_servers_and_chats_at_v1(self, server: ModelServer) -> None:
        # Ollama's and LM Studio's roots answer a listing, so a url typed
        # without /v1 listed the models and then failed every turn with
        # a 404. The engine's OpenAI surface is /v1 under the url, and
        # the client says so; the file keeps what was typed.
        root = server.url.removesuffix("/v1")
        for url in (root, root + "/", server.url + "/"):
            client = OllamaClient(ProviderConfig(name="ollama", url=url))
            assert client.config.url == server.url
        _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert server.posts[-1].endswith("/v1/chat/completions")
        # A url that is only a url stays as typed: nothing hangs off it
        # that the client could know of.
        assert GenericClient(ProviderConfig(name="generic", url=root)).config.url == root


class TestKeys:
    def test_the_variable_stands_in_and_a_typed_key_wins(
        self, server: ModelServer, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        registry = Registry({"openrouter": _config(server, "openrouter")})
        _drain(registry.get("openrouter").completion.chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
        assert registry.get("openrouter").auth.key_source is KeySource.ENV
        registry.update(_config(server, "openrouter", api_key="typed"))
        _drain(registry.get("openrouter").completion.chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer typed"
        assert registry.get("openrouter").auth.key_source is KeySource.CONFIG
        registry.update(_config(server, "openrouter"))
        _drain(registry.get("openrouter").completion.chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer from-env"

    def test_llamacpp_says_a_missing_key_at_the_listing(
        self, server: ModelServer, monkeypatch
    ) -> None:
        # /v1/models is exempt from the server's key check and /props is
        # not, so the listing's props read is where a key missing or
        # wrong shows — not the first turn.
        monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)
        server.window = 4096
        server.api_key = "secret"
        with pytest.raises(UnauthorizedError):
            LlamaCppClient(_config(server, "llamacpp")).models.list()
        with pytest.raises(UnauthorizedError):
            LlamaCppClient(_config(server, "llamacpp", api_key="wrong")).models.list()
        rows = LlamaCppClient(_config(server, "llamacpp", api_key="secret")).models.list()
        assert rows[0].context_loaded == 4096 and rows[0].capabilities is not None

    def test_no_key_sends_no_authorization(self, server: ModelServer, monkeypatch) -> None:
        monkeypatch.delenv("GENERIC_API_KEY", raising=False)
        client = GenericClient(_config(server, "generic"))
        _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert "Authorization" not in server.request_headers[-1]
        assert client.auth.key_source is None

    def test_a_cleared_key_in_the_panel_uncovers_the_variable(
        self, server: ModelServer, tmp_path, monkeypatch
    ) -> None:
        # The user's story: a key typed in the panel is forgotten, and
        # the shell's stands in again on the very next turn.
        from scenarios.support.harness import launch, set_config_provider

        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        server.api_key = "from-env"
        set_config_provider(tmp_path / "state", server, name="openrouter", api_key="")
        app = launch(tmp_path / "state", server, spec="openrouter/test-model")
        try:
            app.play("I enter the hall.")
            assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
            app.api.providers.update("openrouter", api_key="typed")
            app.play("I look around.")
            assert server.request_headers[-1]["Authorization"] == "Bearer typed"
            app.api.providers.update("openrouter", api_key="")
            app.play("We walk on.")
            assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
        finally:
            app.close()

    def test_the_panel_says_where_the_key_comes_from(
        self, server: ModelServer, tmp_path, monkeypatch
    ) -> None:
        # What the field captions, the value never shown: the variable's
        # until a key is typed, the section's while one is, the
        # variable's again once it is cleared. A provider not configured
        # yet is asked off its default section, so a variable shows
        # before a section exists.
        from scenarios.support.harness import launch, set_config_provider

        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        monkeypatch.setenv("NANOGPT_API_KEY", "from-env")
        monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)
        set_config_provider(tmp_path / "state", server, name="openrouter", api_key="")
        app = launch(tmp_path / "state", server, spec="openrouter/test-model")
        try:
            source = lambda name: app.api.providers.get(name).key_source  # noqa: E731
            assert source("openrouter") is KeySource.ENV
            app.api.providers.update("openrouter", api_key="typed")
            assert source("openrouter") is KeySource.CONFIG
            app.api.providers.update("openrouter", api_key="")
            assert source("openrouter") is KeySource.ENV
            assert source("nanogpt") is KeySource.ENV  # no section yet
            assert source("llamacpp") is None
        finally:
            app.close()


class TestProbe:
    def test_a_provider_that_answers(self, server: ModelServer, monkeypatch) -> None:
        monkeypatch.delenv("GENERIC_API_KEY", raising=False)
        found = probe(_config(server, "generic"))
        assert found.status is ProbeStatus.OK and found.models_count == 1
        assert found.key_source is None

    def test_a_provider_that_lists_nothing(self) -> None:
        server = ModelServer(models=())
        try:
            assert probe(_config(server, "generic")).status is ProbeStatus.EMPTY
        finally:
            server.close()

    def test_a_dead_port(self) -> None:
        found = probe(ProviderConfig(name="llamacpp", url=DEAD))
        assert found.status is ProbeStatus.UNREACHABLE
        assert found.message == "Could not reach llamacpp."

    def test_a_key_that_is_missing_or_wrong(
        self, server: ModelServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A shell that carries the catalog's key would stand in for the
        # missing one; the story is about a section with none.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server.api_key = "right"
        missing = probe(_config(server, "openrouter"))
        wrong = probe(_config(server, "openrouter", api_key="wrong"))
        assert missing.status is ProbeStatus.UNAUTHORIZED and missing.key_source is None
        assert wrong.status is ProbeStatus.UNAUTHORIZED and wrong.key_source is KeySource.CONFIG
        assert probe(_config(server, "openrouter", api_key="right")).status is ProbeStatus.OK

    def test_a_keyed_catalog_that_cannot_be_reached_is_unreachable_not_rejected(self) -> None:
        # The key is checked against the account first; a dead network
        # there is a dead network, never a wrong key.
        found = probe(ProviderConfig(name="openrouter", url=DEAD, api_key="k"))
        assert found.status is ProbeStatus.UNREACHABLE
        assert found.message == "Could not reach openrouter."

    def test_a_url_that_cannot_be_spelled_is_unreachable(self) -> None:
        found = probe(ProviderConfig(name="llamacpp", url="http://localhost:8o80/v1"))
        assert found.status is ProbeStatus.UNREACHABLE
        assert found.message == "Could not reach llamacpp."

    def test_a_name_no_supported_provider_answers_to_is_an_error(self) -> None:
        found = probe(ProviderConfig(name="mybox", url="http://localhost:1/v1"))
        assert found.status is ProbeStatus.ERROR
        assert found.message == "No supported provider is named mybox."

    def test_a_server_that_answers_with_an_error(self, server: ModelServer) -> None:
        server.list_status = 503
        found = probe(_config(server, "generic"))
        assert found.status is ProbeStatus.ERROR
        assert "HTTP 503" in found.message

    def test_the_probe_never_touches_the_environment_of_another_engine(
        self, server: ModelServer, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        server.api_key = "from-env"
        assert probe(_config(server, "openrouter")).key_source is KeySource.ENV


class TestBalance:
    def test_openrouter_reports_what_is_left(self, server: ModelServer) -> None:
        server.credits = (10.0, 2.5)
        money = OpenRouterClient(_config(server, "openrouter", api_key="k")).balance()
        assert money is not None and float(money.amount) == 7.5 and money.currency == "USD"

    def test_nanogpt_reports_the_dollar_figure(self, server: ModelServer) -> None:
        server.balances = {"usd_balance": "3.25", "nano_balance": "99"}
        money = NanoGptClient(_config(server, "nanogpt", api_key="k")).balance()
        assert money is not None and float(money.amount) == 3.25

    def test_a_rejected_key_is_none_and_a_local_engine_has_no_account(
        self, server: ModelServer
    ) -> None:
        server.api_key = "right"
        assert OpenRouterClient(_config(server, "openrouter", api_key="wrong")).balance() is None
        assert OllamaClient(_config(server, "ollama")).balance() is None


class TestFailures:
    def test_a_dead_host_cannot_be_reached(self) -> None:
        client = GenericClient(ProviderConfig(name="generic", url=DEAD))
        with pytest.raises(UnreachableError) as listing:
            client.models.list()
        with pytest.raises(UnreachableError) as turn:
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert str(listing.value) == str(turn.value) == "Could not reach generic."

    def test_an_error_status_carries_the_status_and_the_servers_words(
        self, server: ModelServer
    ) -> None:
        server.refuse = lambda body: 503
        with pytest.raises(StatusError) as caught:
            _drain(
                GenericClient(_config(server, "generic")).completion.chat(
                    "m", [Turn("user", "u")], {}
                )
            )
        assert caught.value.status == 503
        # The server's own message, out of its envelope.
        assert str(caught.value) == "Refused by generic with HTTP 503: refused by the script"

    def test_a_rejected_key_is_its_own_failure(self, server: ModelServer) -> None:
        server.refuse = lambda body: 401
        with pytest.raises(UnauthorizedError, match="rejected by generic"):
            _drain(
                GenericClient(_config(server, "generic")).completion.chat(
                    "m", [Turn("user", "u")], {}
                )
            )

    def test_a_refusal_frame_is_the_model_declining(self, server: ModelServer) -> None:
        server.decline = "content filtered"
        with pytest.raises(DeclinedError, match="The reply broke off: content filtered"):
            _drain(
                GenericClient(_config(server, "generic")).completion.chat(
                    "m", [Turn("user", "u")], {}
                )
            )

    def test_a_connection_lost_mid_stream_says_so(self, server: ModelServer) -> None:
        server.fail_after = 1
        with pytest.raises(UnreachableError) as lost:
            _drain(
                GenericClient(_config(server, "generic")).completion.chat(
                    "m", [Turn("user", "u")], {}
                )
            )
        assert str(lost.value) == "Lost the connection to generic."

    def test_a_stream_that_ends_without_done_is_a_lost_connection(
        self, server: ModelServer
    ) -> None:
        # Every provider sends [DONE] on a clean end; Ollama closes without
        # it after a runner's error mid-reply — the words that came are
        # not an answer, and the log must not say ok.
        server.no_done = True
        log = Sink()
        client = GenericClient(_config(server, "generic"), request_sink=log)
        with pytest.raises(UnreachableError) as lost:
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert str(lost.value) == "Lost the connection to generic."
        assert log.statuses == ["failed: Lost the connection to generic."]

    def test_a_wrong_path_answered_with_an_error_object_says_so(self, server: ModelServer) -> None:
        # LM Studio answers an unknown path with a 200 and a bare error
        # object, no stream: the sentence is the server's, not a lost
        # connection — the one provider whose url is typed by hand.
        server.flat_error = "Unexpected endpoint or method. (POST /v2/chat/completions)"
        client = GenericClient(_config(server, "generic"))
        with pytest.raises(DeclinedError) as told:
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert str(told.value) == "The reply broke off: " + server.flat_error

    def test_a_failure_is_filed_under_the_provider_and_its_purpose(
        self, server: ModelServer
    ) -> None:
        # Filed before it raises, a listing's and a stream's alike, with
        # the exception itself so a traceback can follow it.
        errors = Errors()
        dead = GenericClient(ProviderConfig(name="generic", url=DEAD), error_sink=errors)
        with pytest.raises(UnreachableError):
            dead.models.list()
        client = GenericClient(_config(server, "generic"), error_sink=errors)
        server.refuse = lambda body: 500
        with pytest.raises(StatusError):
            _drain(client.completion.chat("m", [Turn("user", "u")], {}))
        assert [context for context, _ in errors.filed] == ["generic [listing]", "generic [chat]"]
        assert [type(exc) for _, exc in errors.filed] == [UnreachableError, StatusError]

    def test_a_retried_knob_and_a_quiet_read_file_nothing(self, server: ModelServer) -> None:
        # The 400 a take sends again without the knobs was no failure,
        # and a best-effort read answers None without a word.
        errors = Errors()
        client = GenericClient(_config(server, "generic"), error_sink=errors)
        server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        server.refusal = "unknown field: reasoning_effort"
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, level="low"))
        counting = LlamaCppClient(ProviderConfig(name="llamacpp", url=DEAD), error_sink=errors)
        assert counting.completion.count_text_tokens("m", "p") is None
        assert errors.filed == []


class TestPanel:
    def test_the_registry_says_why_a_provider_is_not_listed(self, server: ModelServer) -> None:
        # The pickers get the package's sentence, not a bare None: a
        # dead server, a rejected key, an error status — each its own
        # verdict; one not configured is None still.
        registry = Registry(
            {
                "generic": ProviderConfig(name="generic", url=DEAD),
                "openrouter": ProviderConfig(name="openrouter", url=server.url, api_key="wrong"),
            }
        )
        dead = registry.info("generic")
        assert isinstance(dead, ProviderFailure)
        assert dead.status is ProbeStatus.UNREACHABLE and "generic" in dead.message
        server.api_key = "right"  # the catalog demands one, and this is not it
        try:
            rejected = registry.info("openrouter")
        finally:
            server.api_key = None
        assert isinstance(rejected, ProviderFailure)
        assert rejected.status is ProbeStatus.UNAUTHORIZED and rejected.message
        assert registry.info("ollama") is None
        server.list_status = 500
        try:
            failed = Registry({"generic": ProviderConfig(name="generic", url=server.url)}).info(
                "generic"
            )
            assert isinstance(failed, ProviderFailure) and failed.status is ProbeStatus.ERROR
        finally:
            server.list_status = None


class TestWindowsAndState:
    def test_a_cold_model_is_loaded_before_its_row_is_read(self) -> None:
        # `ready` is what a turn reads: an engine that manages loads and
        # does not run the model loads it first, so the window a prompt
        # is cut to is the runner's — Ollama sizes one as it loads. A
        # model already running costs no load; a provider that manages
        # nothing answers as `get` does.
        server = ModelServer(models=("alpha",), managed=True)
        server.contexts["alpha"] = 32768
        try:
            client = OllamaClient(_config(server, "ollama"))
            cold = client.models.get("alpha")
            assert cold is not None and cold.state is ModelState.UNLOADED
            assert cold.context_window is None
            row = client.models.ready("alpha")
            assert row is not None and row.state is ModelState.LOADED
            assert row.context_window == 32768
            assert server.posts.count("/api/generate") == 1
            client.models.ready("alpha")
            assert server.posts.count("/api/generate") == 1
            plain = GenericClient(_config(server, "generic")).models.ready("alpha")
            assert plain is not None and plain.state is ModelState.UNKNOWN
            assert server.posts.count("/api/generate") == 1
        finally:
            server.close()

    def test_ollama_lists_a_runner_without_a_window_as_loading(self) -> None:
        # ps lists a runner from the moment its load starts and states
        # its context length only once it serves; a turn on one waits
        # for it as on a cold model.
        server = ModelServer(models=("alpha", "beta"), managed=True)
        server.loaded = {"alpha"}
        server.loading = {"beta"}
        server.contexts["beta"] = 16384
        try:
            client = OllamaClient(_config(server, "ollama"))
            rows = {r.name: r for r in client.models.list()}
            assert rows["alpha"].state is ModelState.LOADED
            assert (rows["beta"].state, rows["beta"].context_window) == (ModelState.LOADING, None)
            row = client.models.ready("beta")
            assert row is not None and (row.state, row.context_window) == (ModelState.LOADED, 16384)
        finally:
            server.close()

    def test_ollama_reads_the_card_again_when_the_digest_changes(self) -> None:
        # A re-pull replaces a tag's content in place; the listing's
        # digest says so, and the next read asks the card again.
        server = ModelServer(models=("alpha",), managed=True)
        server.capabilities["alpha"] = ["completion"]
        try:
            client = OllamaClient(_config(server, "ollama"))
            client.models.get("alpha")
            client.models.get("alpha")
            assert server.posts.count("/api/show") == 1
            server.capabilities["alpha"] = ["completion", "vision"]
            client.models.list()
            row = client.models.get("alpha")
            assert server.posts.count("/api/show") == 1  # the same weights: the card stands
            assert row is not None and row.capabilities is not None
            assert row.capabilities.vision is False
            server.digests["alpha"] = "sha256:other"
            client.models.list()
            row = client.models.get("alpha")
            assert server.posts.count("/api/show") == 2
            assert row is not None and row.capabilities is not None
            assert row.capabilities.vision is True
        finally:
            server.close()

    def test_llamacpp_single_mode_reads_its_entry_again(self) -> None:
        # A one-model read re-reads the one entry: a server restarted
        # with another window is noticed; one serving another model is
        # not answered for, and the cached word stands until a listing
        # drops it.
        server = ModelServer(models=("model.gguf",))
        server.window = 4096
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            assert client.models.list()[0].context_window == 4096
            server.window = 2048
            row = client.models.get("model.gguf")
            assert row is not None and row.context_window == 2048
            server.models = ["other.gguf"]
            row = client.models.get("model.gguf")
            assert row is not None and row.context_window == 2048
            assert [r.name for r in client.models.list()] == ["other.gguf"]
            assert client.models.get("model.gguf") is None
        finally:
            server.close()

    def test_the_generic_provider_reads_a_served_window_where_one_is_listed(self) -> None:
        # The vLLM extension, and llama.cpp's meta: a window from a
        # server the generic provider cannot otherwise ask.
        server = ModelServer(models=("vllm", "llama", "plain"))
        server.extras = {
            "vllm": {"max_model_len": 4096},
            "llama": {"meta": {"n_ctx": 2048, "n_ctx_train": 8192}},
        }
        try:
            rows = {r.name: r for r in GenericClient(_config(server, "generic")).models.list()}
            assert (rows["vllm"].context_loaded, rows["vllm"].context_window) == (4096, 4096)
            assert (rows["llama"].context_length, rows["llama"].context_window) == (8192, 2048)
            assert rows["plain"].context_window is None
        finally:
            server.close()

    def test_omlx_takes_a_cap_stated_alone_as_a_guess(self) -> None:
        # The serving cap is the window only beside the model's own
        # length; alone it is the server's default for a length it could
        # not discover, and the budget stays unknown.
        server = ModelServer(models=("known", "guessed"))
        server.status = True
        server.unstated = {"guessed"}
        try:
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert rows["known"].context_window == 8192
            assert (rows["guessed"].context_window, rows["guessed"].context_length) == (
                None,
                None,
            )
        finally:
            server.close()


def _panel_walk(app: App, provider: str, attr: str) -> str:
    """The keys that take the panel's cursor from its first field to
    `provider`'s `attr`: Tab into the panel, then Down past every field
    above — two per provider in the panel's order, the cloud catalogs'
    fixed url excepted (`ModelPicker.fields`)."""
    fields = [
        (p.id, a)
        for p in app.api.providers.list(probe=False).providers
        for a in ("url", "api_key")
        if a != "url" or p.locality is not Locality.REMOTE
    ]
    return "\t" + _DOWN * fields.index((provider, attr))


def _unsealed(app: App, value: str) -> str:
    """A sealed providers.toml value opened over the state dir's file
    key — the way the launch opens it."""
    return unseal(value, key_file=app.paths.config_key_file, service="scenario:none")


@dataclass(frozen=True)
class Turn:
    role: str
    body: str
    images: tuple[Image, ...] = ()
    volatile: bool = False


@dataclass
class Sink:
    """A request sink that keeps what it was told."""

    requests: list[dict[str, object]] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str:
        self.requests.append(body)
        return f"r{len(self.requests)}"

    def record_answer(self, provider: str, purpose: str, request_id: str, **kw: object) -> None:
        self.statuses.append(str(kw["status"]))
        self.texts.append(str(kw["text"]))


class Errors:
    """An error sink that keeps what it was told."""

    def __init__(self) -> None:
        self.filed: list[tuple[str, BaseException]] = []

    def record(self, context: str, exc: BaseException) -> Path:
        self.filed.append((context, exc))
        return Path()


def _config(server: ModelServer, kind: str, **fields: str) -> ProviderConfig:
    return ProviderConfig(name=kind, url=server.url, **fields)  # type: ignore[arg-type]


class _Interrupted(BaseException):
    """The terminal's ctrl-c, as a test can raise it: no Exception, so
    the wrapper's idle hook does not swallow it, and not the real
    KeyboardInterrupt, which would stop the run."""


def _interrupt() -> None:
    raise _Interrupted


def _filed(sink: Sink, status: str, *, within: float) -> bool:
    """Whether the take filed a status opening with `status` within
    `within` seconds: the filing happens in the pump's thread, after
    the cut wakes it."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if sink.statuses and sink.statuses[-1].startswith(status):
            return True
        time.sleep(0.01)
    return False


def _sent(server: ModelServer, shape: str) -> dict[str, object]:
    """The newest recorded request of the wire under test — the one
    carrying `shape` ("messages" or "prompt"), since a turn's own
    context ask may post a balance check after it."""
    return next(body for body in reversed(server.requests) if shape in body)


def _knobs(body: dict[str, object]) -> dict[str, object]:
    knobs = (
        "reasoning_effort",
        "chat_template_kwargs",
        "thinking_budget_tokens",
        "thinking_budget",
    )
    return {k: body[k] for k in knobs if k in body}


def _drain(stream: Iterator[Chunk]) -> tuple[str, str, Stats]:
    """A stream run to its end: the reasoning, the text, the stats."""
    reasoning, text, stats = [], [], None
    for chunk in stream:
        if isinstance(chunk, Reasoning):
            reasoning.append(chunk.text)
        elif isinstance(chunk, Text):
            text.append(chunk.text)
        else:
            stats = chunk
    assert stats is not None, "a stream ends with its stats"
    return "".join(reasoning), "".join(text), stats
