"""The providers package against a real protocol peer: what each engine
puts on the wire and reads back, one class per feature, the engines
that differ each getting their row.

The chat wire: the transcript as messages, streaming with usage, a
level on every knob the engine reads, images on the last message, one
retry without the knobs when a 400 refuses them, and the answer filed
under the request. The text wire: the prompt alone, a level on the
text knobs, the continuation as text. The listing: one pass per engine,
the rows read as far as the native API sees, capabilities decoded from
what each engine reports and None where it reports nothing, the one
model ask reading Ollama's card. The counts, the loads, the keys, the
probe, the balance, and the error family, each with its sentence.
"""

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from otaku.providers import (
    CLIENTS,
    Capabilities,
    Chunk,
    DeclinedError,
    Image,
    KeySource,
    ProbeOutcome,
    ProviderConfig,
    ProviderError,
    Registry,
    Stats,
    StatusError,
    Text,
    Thinking,
    UnauthorizedError,
    UnreachableError,
    probe,
)
from otaku.providers.clients.generic import OpenAIClient
from otaku.providers.clients.koboldcpp import KoboldCppClient
from otaku.providers.clients.llamacpp import LlamaCppClient
from otaku.providers.clients.lmstudio import LmStudioClient
from otaku.providers.clients.nanogpt import NanoGptClient
from otaku.providers.clients.ollama import OllamaClient
from otaku.providers.clients.omlx import OmlxClient
from otaku.providers.clients.openrouter import OpenRouterClient
from otaku.providers.wire import ALL_THINKING_LEVELS
from scenarios.support import server as scripted
from scenarios.support.harness import launch, set_config_provider
from scenarios.support.server import ModelServer

ALL = ALL_THINKING_LEVELS
EFFORT_NONE = {"reasoning_effort": "none"}
FLAG_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
DEAD = "http://127.0.0.1:9/v1"


@dataclass(frozen=True)
class Turn:
    role: str
    body: str


@dataclass
class Sink:
    """A request sink that keeps what it was told."""

    requests: list[dict[str, object]] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str:
        self.requests.append(body)
        return f"r{len(self.requests)}"

    def record_answer(self, provider: str, purpose: str, request_id: str, **kw: object) -> None:
        self.outcomes.append(str(kw["outcome"]))
        self.texts.append(str(kw["text"]))


class TestChatCompletion:
    def test_the_transcript_streams_with_usage_and_the_params(self, server: ModelServer) -> None:
        client = OpenAIClient(_config(server, "generic"))
        thinking, text, stats = _drain(
            client.complete_chat("m", [Turn("system", "s"), Turn("user", "u")], {"top_p": 0.5})
        )
        body = server.requests[-1]
        assert body["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        assert body["stream"] is True and body["top_p"] == 0.5
        assert text == scripted.CHAT_REPLY and thinking == ""
        assert (stats.prompt_tokens, stats.completion_tokens) == (7, 5)
        assert stats.first_token_seconds is not None and stats.duration_seconds > 0

    def test_thinking_streams_before_the_text(self, server: ModelServer) -> None:
        server.script = lambda body: ("hm", "yes")
        chunks = list(
            OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
        )
        assert isinstance(chunks[0], Thinking) and chunks[0].text == "hm"
        assert all(isinstance(c, Text) for c in chunks[1:-1])
        assert isinstance(chunks[-1], Stats)

    def test_cached_tokens_come_off_the_usage_report(self, server: ModelServer) -> None:
        server.cached_tokens = 3
        _, _, stats = _drain(
            OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
        )
        assert stats.cached_tokens == 3

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("generic", {**EFFORT_NONE, **FLAG_OFF}),
            ("llamacpp", {**EFFORT_NONE, **FLAG_OFF}),
            ("koboldcpp", {**EFFORT_NONE, **FLAG_OFF}),
            ("omlx", {**EFFORT_NONE, **FLAG_OFF}),
            ("ollama", EFFORT_NONE),
            ("openrouter", EFFORT_NONE),
            ("nanogpt", EFFORT_NONE),
            ("lmstudio", {}),
        ],
    )
    def test_a_level_goes_out_on_the_knobs_the_engine_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        # The table every engine is promised by: both knobs where a local
        # engine reads the template's flag, the effort alone on the
        # catalogs and Ollama, nothing where nothing on the wire reaches
        # the engine — and the turn plays in every case.
        client = CLIENTS[kind](_config(server, kind, api_key="k"))
        _, text, _ = _drain(client.complete_chat("m", [Turn("user", "u")], {}, think_level="off"))
        assert _knobs(_sent(server, "messages")) == expected
        assert text == scripted.CHAT_REPLY

    def test_no_level_sends_no_knob(self, server: ModelServer) -> None:
        _drain(OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {}))
        assert "reasoning_effort" not in server.requests[-1]
        assert "chat_template_kwargs" not in server.requests[-1]

    def test_a_400_to_the_knob_retries_once_without_it(self, server: ModelServer) -> None:
        server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        _, text, _ = _drain(
            OpenAIClient(_config(server, "generic")).complete_chat(
                "m", [Turn("user", "u")], {}, think_level="high"
            )
        )
        assert text == scripted.CHAT_REPLY
        knobbed, plain = server.requests[-2:]
        assert knobbed["reasoning_effort"] == "high" and "reasoning_effort" not in plain

    def test_a_400_after_words_arrived_is_a_failure_not_a_retry(self, server: ModelServer) -> None:
        # A second take would repeat what is already on someone's screen.
        server.fail_after = 1
        stream = OpenAIClient(_config(server, "generic")).complete_chat(
            "m", [Turn("user", "u")], {}, think_level="high"
        )
        with pytest.raises(UnreachableError):
            list(stream)
        assert len(server.requests) == 1

    def test_images_ride_on_the_last_message(self, server: ModelServer) -> None:
        image = Image(b"\x89PNG", "image/png")
        _drain(
            OpenAIClient(_config(server, "generic")).complete_chat(
                "m", [Turn("user", "before"), Turn("user", "look")], {}, images=[image]
            )
        )
        messages = server.requests[-1]["messages"]
        assert messages[0] == {"role": "user", "content": "before"}
        url = messages[-1]["content"][1]["image_url"]["url"]
        assert messages[-1]["content"][0] == {"type": "text", "text": "look"}
        assert url == "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()

    def test_openrouter_marks_the_cache_where_the_section_allows(self, server: ModelServer) -> None:
        marked = OpenRouterClient(_config(server, "openrouter", api_key="k", prompt_cache="1h"))
        _drain(marked.complete_chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        content = server.requests[-1]["messages"][0]["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        off = OpenRouterClient(_config(server, "openrouter", api_key="k", prompt_cache="off"))
        _drain(off.complete_chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        assert server.requests[-1]["messages"][0]["content"] == "s"

    def test_the_answer_is_filed_however_the_stream_ends(self, server: ModelServer) -> None:
        sink = Sink()
        client = OpenAIClient(_config(server, "generic"), request_sink=sink)
        _drain(client.complete_chat("m", [Turn("user", "u")], {}))
        server.chunk_delay = 0.05
        stream = client.complete_chat("m", [Turn("user", "u")], {})
        first = next(c for c in stream if isinstance(c, Text))
        stream.close()
        server.chunk_delay = 0.0
        server.refuse = lambda body: 500
        with pytest.raises(StatusError):
            _drain(client.complete_chat("m", [Turn("user", "u")], {}))
        assert sink.outcomes == ["ok", "cancelled", "failed: StatusError"]
        assert sink.texts[0] == scripted.CHAT_REPLY and sink.texts[1] == first.text


class TestTextCompletion:
    def test_the_prompt_goes_alone_and_the_continuation_comes_as_text(
        self, server: ModelServer
    ) -> None:
        client = OpenAIClient(_config(server, "generic"))
        thinking, text, stats = _drain(client.complete_text("m", "Once upon", {"stop": ["\n"]}))
        body = server.requests[-1]
        assert body["prompt"] == "Once upon" and "messages" not in body
        assert body["stop"] == ["\n"] and body["stream"] is True
        assert text == scripted.CHAT_REPLY and thinking == ""
        assert (stats.prompt_tokens, stats.completion_tokens) == (7, 5)

    def test_thinking_never_arrives_apart_on_the_text_wire(self, server: ModelServer) -> None:
        server.script = lambda body: ("hm", "yes")
        chunks = list(OpenAIClient(_config(server, "generic")).complete_text("m", "p", {}))
        assert not any(isinstance(c, Thinking) for c in chunks)

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("generic", EFFORT_NONE),
            ("koboldcpp", EFFORT_NONE),
            ("nanogpt", EFFORT_NONE),
            ("llamacpp", {}),
            ("ollama", {}),
            ("omlx", {}),
            ("lmstudio", {}),
            ("openrouter", {}),
        ],
    )
    def test_a_level_goes_out_on_the_text_knobs_the_engine_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        client = CLIENTS[kind](_config(server, kind, api_key="k"))
        _drain(client.complete_text("m", "p", {}, think_level="off"))
        assert _knobs(_sent(server, "prompt")) == expected


class TestListing:
    def test_a_plain_listing_is_ids_and_nothing_read(self) -> None:
        server = ModelServer(models=("b", "a"))
        try:
            rows = LmStudioClient(_config(server, "lmstudio")).models()
            assert [r.name for r in rows] == ["a", "b"]
            assert all(r.capabilities is None and r.context is None for r in rows)
        finally:
            server.close()

    def test_a_catalog_listing_reads_the_context_and_seeds_it(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.contexts["a"] = 32_000
        try:
            client = OpenAIClient(_config(server, "generic"))
            rows = {r.name: r for r in client.models()}
            assert rows["a"].context == 32_000 and rows["a"].loaded
            assert rows["b"].context is None
            assert client.get_context_size("a") == 32_000
            assert sum(p.endswith("/models") for p in server.gets) == 1
        finally:
            server.close()

    def test_a_keyed_catalog_refuses_without_a_key_that_opens_the_account(
        self, server: ModelServer
    ) -> None:
        server.api_key = "right"
        with pytest.raises(UnauthorizedError):
            OpenRouterClient(_config(server, "openrouter")).models()
        with pytest.raises(UnauthorizedError):
            OpenRouterClient(_config(server, "openrouter", api_key="wrong")).models()
        assert [
            r.name
            for r in OpenRouterClient(_config(server, "openrouter", api_key="right")).models()
        ] == ["test-model"]


class TestCapabilities:
    def test_llamacpp_reads_the_modalities_and_whether_the_template_takes_an_effort(
        self, server: ModelServer
    ) -> None:
        server.window = 4096
        server.props = {
            "modalities": {"vision": False},
            "chat_template_caps": {"supports_reasoning_effort": True},
        }
        row = LlamaCppClient(_config(server, "llamacpp")).models()[0]
        assert row.capabilities == Capabilities(vision=False, thinking=ALL)
        server.props = {"modalities": {"vision": True}, "chat_template_caps": {}}
        row = LlamaCppClient(_config(server, "llamacpp")).models()[0]
        assert row.capabilities == Capabilities(vision=True, thinking=frozenset({"off"}))

    def test_koboldcpp_reads_vision_off_its_version_and_knows_its_budget_levels(
        self, server: ModelServer
    ) -> None:
        server.version = {"version": "1.120", "vision": False}
        row = KoboldCppClient(_config(server, "koboldcpp")).models()[0]
        assert row.capabilities == Capabilities(
            vision=False, thinking=frozenset({"off", "low", "medium"})
        )

    def test_ollama_reads_the_card_for_one_model_and_never_in_the_listing(self) -> None:
        server = ModelServer(models=("alpha", "beta"), managed=True)
        server.capabilities["alpha"] = ["completion", "vision", "thinking"]
        server.capabilities["beta"] = ["completion"]
        try:
            client = OllamaClient(_config(server, "ollama"))
            assert all(r.capabilities is None for r in client.models())
            assert not any(b.get("model") for b in server.requests)
            alpha, beta = client.model("alpha"), client.model("beta")
            assert alpha is not None and beta is not None
            assert alpha.capabilities == Capabilities(
                vision=True, thinking=frozenset({"off", "low", "medium", "high", "max"})
            )
            assert beta.capabilities == Capabilities(vision=False, thinking=frozenset())
        finally:
            server.close()

    def test_omlx_reads_the_model_type(self) -> None:
        server = ModelServer(models=("vl", "lm", "unsaid"))
        server.status = True
        server.types = {"vl": "vlm", "lm": "llm"}
        try:
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models()}
            assert rows["vl"].capabilities == Capabilities(vision=True)
            assert rows["lm"].capabilities == Capabilities(vision=False)
            assert rows["unsaid"].capabilities == Capabilities(vision=True)
        finally:
            server.close()

    def test_openrouter_reads_the_modalities_and_the_reasoning_object(self) -> None:
        server = ModelServer(models=("free", "fixed", "mute", "plain", "bare"))
        server.extras = {
            "free": {
                "architecture": {"input_modalities": ["text", "image"]},
                "reasoning": {"mandatory": False, "supported_efforts": ["low", "high", "minimal"]},
            },
            "fixed": {
                "architecture": {"input_modalities": ["text"]},
                "reasoning": {"mandatory": True, "supported_efforts": ["low", "medium", "high"]},
            },
            "mute": {"supported_parameters": ["temperature"]},
            "plain": {"reasoning": {"mandatory": False}},
            "bare": {},
        }
        try:
            rows = {
                r.name: r
                for r in OpenRouterClient(_config(server, "openrouter", api_key="k")).models()
            }
            assert rows["free"].capabilities == Capabilities(
                vision=True, thinking=frozenset({"off", "low", "high"})
            )
            assert rows["fixed"].capabilities == Capabilities(
                vision=False, thinking=frozenset({"low", "medium", "high"})
            )
            assert rows["mute"].capabilities == Capabilities(vision=True, thinking=frozenset())
            assert rows["plain"].capabilities == Capabilities(vision=True, thinking=ALL)
            assert rows["bare"].capabilities == Capabilities(vision=True, thinking=ALL)
        finally:
            server.close()

    def test_nanogpt_reads_its_capabilities_and_efforts(self) -> None:
        server = ModelServer(models=("seeing", "fixed", "mute", "bare"))
        server.extras = {
            "seeing": {
                "capabilities": {"vision": True, "reasoning": True},
                "reasoning_efforts": ["low", "high"],
            },
            "fixed": {"capabilities": {"vision": False, "reasoning": True}},
            "mute": {"capabilities": {"vision": False, "reasoning": False}},
            "bare": {},
        }
        try:
            rows = {
                r.name: r for r in NanoGptClient(_config(server, "nanogpt", api_key="k")).models()
            }
            assert rows["seeing"].capabilities == Capabilities(
                vision=True, thinking=frozenset({"off", "low", "high"})
            )
            assert rows["fixed"].capabilities == Capabilities(vision=False, thinking=ALL)
            assert rows["mute"].capabilities == Capabilities(vision=False, thinking=frozenset())
            assert rows["bare"].capabilities is None
        finally:
            server.close()

    def test_the_generic_provider_reads_nothing(self, server: ModelServer) -> None:
        assert OpenAIClient(_config(server, "generic")).models()[0].capabilities is None


class TestTokenCounts:
    def test_llamacpp_counts_the_chat_body_and_a_raw_prompt(self, server: ModelServer) -> None:
        server.token_count = 42
        client = LlamaCppClient(_config(server, "llamacpp"))
        assert client.count_chat_tokens("m", [Turn("system", "s"), Turn("user", "u")]) == 42
        counted = server.requests[-1]
        assert counted["messages"] == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        assert "stream" not in counted
        assert client.count_text_tokens("m", "raw") == 42
        assert server.requests[-1] == {"model": "m", "content": "raw"}

    def test_koboldcpp_counts_either_shape(self, server: ModelServer) -> None:
        server.token_count = 7
        client = KoboldCppClient(_config(server, "koboldcpp"))
        assert client.count_chat_tokens("m", [Turn("user", "u")]) == 7
        assert server.requests[-1] == {"messages": [{"role": "user", "content": "u"}]}
        assert client.count_text_tokens("m", "raw") == 7
        assert server.requests[-1] == {"prompt": "raw"}

    def test_omlx_counts_a_chat_with_the_system_text_apart(self, server: ModelServer) -> None:
        server.token_count = 9
        client = OmlxClient(_config(server, "omlx"))
        assert client.count_chat_tokens("m", [Turn("system", "s"), Turn("user", "u")]) == 9
        assert server.requests[-1] == {
            "model": "m",
            "messages": [{"role": "user", "content": "u"}],
            "system": "s",
        }
        assert client.count_text_tokens("m", "raw") is None

    @pytest.mark.parametrize("kind", ["generic", "ollama", "lmstudio", "openrouter", "nanogpt"])
    def test_the_others_count_nothing(self, server: ModelServer, kind: str) -> None:
        server.token_count = 5
        client = CLIENTS[kind](_config(server, kind, api_key="k"))
        assert client.counts_tokens is False
        assert client.count_chat_tokens("m", [Turn("user", "u")]) is None
        assert client.count_text_tokens("m", "raw") is None

    def test_a_server_without_the_endpoint_counts_none(self, server: ModelServer) -> None:
        client = LlamaCppClient(_config(server, "llamacpp"))
        assert client.counts_tokens is True
        assert client.count_chat_tokens("m", [Turn("user", "u")]) is None


class TestLoadUnload:
    def test_omlx_loads_and_unloads_by_path(self) -> None:
        server = ModelServer(models=("a",))
        server.status = True
        try:
            client = OmlxClient(_config(server, "omlx"))
            assert client.manages_models
            client.load_model("a")
            assert client.models()[0].loaded
            client.unload_model("a")
            assert not client.models()[0].loaded
        finally:
            server.close()

    def test_lmstudio_loads_once_and_unloads_every_instance(self) -> None:
        server = ModelServer(models=("a",), managed=True)
        try:
            client = LmStudioClient(_config(server, "lmstudio"))
            client.load_model("a")
            assert "a" in server.loaded
        finally:
            server.close()

    def test_a_llamacpp_router_manages_its_folder_and_waits_for_the_state(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded.add("a")
        try:
            client = LlamaCppClient(_config(server, "llamacpp"))
            assert client.manages_models is False  # nothing listed yet
            rows = {r.name: r for r in client.models()}
            assert client.manages_models is True
            assert rows["a"].loaded and not rows["b"].loaded
            client.load_model("b")
            assert server.loaded == {"a", "b"}
            client.unload_model("a")
            assert server.loaded == {"b"}
        finally:
            server.close()

    def test_a_single_llamacpp_server_refuses_a_load(self, server: ModelServer) -> None:
        server.window = 4096
        client = LlamaCppClient(_config(server, "llamacpp"))
        client.models()
        assert client.manages_models is False
        with pytest.raises(ProviderError, match="cannot be loaded or unloaded"):
            client.load_model("test-model")

    @pytest.mark.parametrize("kind", ["generic", "koboldcpp", "openrouter", "nanogpt"])
    def test_the_others_refuse_a_load(self, server: ModelServer, kind: str) -> None:
        client = CLIENTS[kind](_config(server, kind))
        assert client.manages_models is False
        with pytest.raises(ProviderError):
            client.load_model("m")


class TestKeys:
    def test_the_variable_stands_in_and_a_typed_key_wins(
        self, server: ModelServer, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        registry = Registry({"openrouter": _config(server, "openrouter")})
        _drain(registry.get_client("openrouter").complete_chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
        assert registry.key_source("openrouter") is KeySource.ENV
        registry.update(_config(server, "openrouter", api_key="typed"))
        _drain(registry.get_client("openrouter").complete_chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer typed"
        assert registry.key_source("openrouter") is KeySource.SECTION
        registry.update(_config(server, "openrouter"))
        _drain(registry.get_client("openrouter").complete_chat("m", [Turn("user", "u")], {}))
        assert server.request_headers[-1]["Authorization"] == "Bearer from-env"

    def test_no_key_sends_no_authorization(self, server: ModelServer, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = OpenAIClient(_config(server, "generic"))
        _drain(client.complete_chat("m", [Turn("user", "u")], {}))
        assert "Authorization" not in server.request_headers[-1]
        assert client.key_source is None

    def test_a_cleared_key_in_the_panel_uncovers_the_variable(
        self, server: ModelServer, tmp_path, monkeypatch
    ) -> None:
        # The user's story: a key typed in the panel is forgotten, and
        # the shell's stands in again on the very next turn.
        from otaku.backend.api import providers as api_providers

        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        server.api_key = "from-env"
        set_config_provider(tmp_path / "state", server, name="openrouter", api_key="")
        app = launch(tmp_path / "state", server, spec="openrouter/test-model")
        try:
            app.play("I enter the hall.")
            assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
            api_providers.save_field(app.session, "openrouter", "api_key", "typed")
            app.play("I look around.")
            assert server.request_headers[-1]["Authorization"] == "Bearer typed"
            api_providers.clear_field(app.session, "openrouter", "api_key")
            app.play("We walk on.")
            assert server.request_headers[-1]["Authorization"] == "Bearer from-env"
        finally:
            app.close()


class TestProbe:
    def test_a_provider_that_answers(self, server: ModelServer, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        found = probe(_config(server, "generic"))
        assert found.outcome is ProbeOutcome.OK and found.models_count == 1
        assert found.key_source is None

    def test_a_provider_that_lists_nothing(self) -> None:
        server = ModelServer(models=())
        try:
            assert probe(_config(server, "generic")).outcome is ProbeOutcome.EMPTY
        finally:
            server.close()

    def test_a_dead_port(self) -> None:
        found = probe(ProviderConfig(name="llamacpp", url=DEAD))
        assert found.outcome is ProbeOutcome.UNREACHABLE
        assert found.message == "Could not reach llamacpp."

    def test_a_key_that_is_missing_or_wrong(self, server: ModelServer) -> None:
        server.api_key = "right"
        missing = probe(_config(server, "openrouter"))
        wrong = probe(_config(server, "openrouter", api_key="wrong"))
        assert missing.outcome is ProbeOutcome.UNAUTHORIZED and missing.key_source is None
        assert wrong.outcome is ProbeOutcome.UNAUTHORIZED and wrong.key_source is KeySource.SECTION
        assert probe(_config(server, "openrouter", api_key="right")).outcome is ProbeOutcome.OK

    def test_a_server_that_answers_with_an_error(self, server: ModelServer) -> None:
        server.list_status = 503
        found = probe(_config(server, "generic"))
        assert found.outcome is ProbeOutcome.ERROR
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
        client = OpenAIClient(ProviderConfig(name="generic", url=DEAD))
        with pytest.raises(UnreachableError) as listing:
            client.models()
        with pytest.raises(UnreachableError) as turn:
            _drain(client.complete_chat("m", [Turn("user", "u")], {}))
        assert str(listing.value) == str(turn.value) == "Could not reach generic."

    def test_an_error_status_carries_the_status_and_the_servers_words(
        self, server: ModelServer
    ) -> None:
        server.refuse = lambda body: 503
        with pytest.raises(StatusError) as caught:
            _drain(
                OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
            )
        assert caught.value.status == 503
        assert (
            str(caught.value)
            == "Refused by generic with HTTP 503: "
            + '{"error": {"message": "refused by the script"}}'
        )

    def test_a_rejected_key_is_its_own_failure(self, server: ModelServer) -> None:
        server.refuse = lambda body: 401
        with pytest.raises(UnauthorizedError, match="rejected by generic"):
            _drain(
                OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
            )

    def test_a_refusal_frame_is_the_model_declining(self, server: ModelServer) -> None:
        server.decline = "content filtered"
        with pytest.raises(DeclinedError, match="The model declined: content filtered"):
            _drain(
                OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
            )

    def test_a_connection_lost_mid_stream_says_so(self, server: ModelServer) -> None:
        server.fail_after = 1
        with pytest.raises(UnreachableError) as lost:
            _drain(
                OpenAIClient(_config(server, "generic")).complete_chat("m", [Turn("user", "u")], {})
            )
        assert str(lost.value) == "Lost the connection to generic."


def _config(server: ModelServer, kind: str, **fields: str) -> ProviderConfig:
    return ProviderConfig(name=kind, url=server.url, **fields)  # type: ignore[arg-type]


def _sent(server: ModelServer, shape: str) -> dict[str, object]:
    """The newest recorded request of the wire under test — the one
    carrying `shape` ("messages" or "prompt"), since a turn's own
    context ask may post a balance check after it."""
    return next(body for body in reversed(server.requests) if shape in body)


def _knobs(body: dict[str, object]) -> dict[str, object]:
    return {k: body[k] for k in ("reasoning_effort", "chat_template_kwargs") if k in body}


def _drain(stream: Iterator[Chunk]) -> tuple[str, str, Stats]:
    """A stream run to its end: the thinking, the text, the stats."""
    thinking, text, stats = [], [], None
    for chunk in stream:
        if isinstance(chunk, Thinking):
            thinking.append(chunk.text)
        elif isinstance(chunk, Text):
            text.append(chunk.text)
        else:
            stats = chunk
    assert stats is not None, "a stream ends with its stats"
    return "".join(thinking), "".join(text), stats
