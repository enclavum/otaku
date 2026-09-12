"""The providers package against a real protocol peer: what each provider
puts on the wire and reads back, one class per feature, the providers
that differ each getting their row.

The chat wire: the transcript as messages, streaming with usage, a
effort on every knob the provider reads, images on the last message, one
retry without the knobs when a 400 refuses them, and the answer filed
under the request. The text wire: the prompt alone, an effort on the
text knobs, the continuation as text. The listing: one pass per provider,
the rows read as far as the native API sees, capabilities decoded from
what each provider reports and None where it reports nothing, the one
model ask reading Ollama's card. The counts, the loads, the keys, the
probe, the balance, and the error family, each with its sentence.
"""

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from otaku.providers import (
    ALL_CLIENTS,
    Capabilities,
    Chunk,
    DeclinedError,
    Image,
    KeySource,
    ModelState,
    ProbeStatus,
    ProviderConfig,
    ProviderError,
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
from otaku.providers.openai.reasoning import ALL_EFFORTS
from scenarios.support import server as scripted
from scenarios.support.server import ModelServer

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
    statuses: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str:
        self.requests.append(body)
        return f"r{len(self.requests)}"

    def record_answer(self, provider: str, purpose: str, request_id: str, **kw: object) -> None:
        self.statuses.append(str(kw["status"]))
        self.texts.append(str(kw["text"]))


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
            ("generic", {**EFFORT_NONE, **FLAG_OFF}),
            ("llamacpp", FLAG_OFF),
            ("koboldcpp", {**EFFORT_NONE, **FLAG_OFF}),
            ("omlx", FLAG_OFF),
            ("ollama", EFFORT_NONE),
            ("openrouter", EFFORT_NONE),
            ("nanogpt", EFFORT_NONE),
            ("lmstudio", {}),
        ],
    )
    def test_an_effort_goes_out_on_the_knobs_the_provider_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        # The table every provider is promised by: both knobs where a
        # local engine reads the template's flag, the effort alone on the
        # catalogs and Ollama, nothing where nothing on the wire reaches
        # the provider — and the turn plays in every case.
        client = ALL_CLIENTS[kind](_config(server, kind, api_key="k"))
        _, text, _ = _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="none"))
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
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="high"))
        body = _sent(server, "messages")
        assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "high"}
        assert "reasoning_effort" not in body

    def test_no_effort_sends_no_knob(self, server: ModelServer) -> None:
        _drain(
            GenericClient(_config(server, "generic")).completion.chat("m", [Turn("user", "u")], {})
        )
        assert "reasoning_effort" not in server.requests[-1]
        assert "chat_template_kwargs" not in server.requests[-1]

    def test_a_400_naming_the_knob_retries_once_without_it(self, server: ModelServer) -> None:
        # The retry is for a 400 about the knob, as the server words it;
        # one about anything else stands.
        server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        server.refusal = "unknown field: reasoning_effort"
        client = GenericClient(_config(server, "generic"))
        _, text, _ = _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="high"))
        assert text == scripted.CHAT_REPLY
        knobbed, plain = server.requests[-2:]
        assert knobbed["reasoning_effort"] == "high" and "reasoning_effort" not in plain
        server.refusal = "bad request"
        with pytest.raises(StatusError):
            _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="high"))
        assert len(server.requests) == 3

    def test_a_400_after_words_arrived_is_a_failure_not_a_retry(self, server: ModelServer) -> None:
        # A second take would repeat what is already on someone's screen.
        server.fail_after = 1
        stream = GenericClient(_config(server, "generic")).completion.chat(
            "m", [Turn("user", "u")], {}, effort="high"
        )
        with pytest.raises(UnreachableError):
            list(stream)
        assert len(server.requests) == 1

    def test_images_ride_on_the_last_message(self, server: ModelServer) -> None:
        image = Image(b"\x89PNG", "image/png")
        _drain(
            GenericClient(_config(server, "generic")).completion.chat(
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
        _drain(marked.completion.chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        content = server.requests[-1]["messages"][0]["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        off = OpenRouterClient(_config(server, "openrouter", api_key="k", prompt_cache="off"))
        _drain(off.completion.chat("m", [Turn("system", "s"), Turn("user", "u")], {}))
        assert server.requests[-1]["messages"][0]["content"] == "s"

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
        assert sink.statuses == ["ok", "cancelled", "failed: StatusError"]
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

    def test_reasoning_never_arrives_apart_on_the_text_wire(self, server: ModelServer) -> None:
        server.script = lambda body: ("hm", "yes")
        chunks = list(GenericClient(_config(server, "generic")).completion.text("m", "p", {}))
        assert not any(isinstance(c, Reasoning) for c in chunks)

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
    def test_an_effort_goes_out_on_the_text_knobs_the_engine_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        client = ALL_CLIENTS[kind](_config(server, kind, api_key="k"))
        _drain(client.completion.text("m", "p", {}, effort="none"))
        assert _knobs(_sent(server, "prompt")) == expected


class TestListing:
    def test_koboldcpp_with_nothing_loaded_lists_nothing(self) -> None:
        # "inactive" is the server's own name for no model loaded.
        server = ModelServer(models=("koboldcpp/inactive",))
        try:
            assert KoboldCppClient(_config(server, "koboldcpp")).models.list() == []
        finally:
            server.close()

    def test_a_plain_listing_is_ids_and_nothing_read(self) -> None:
        server = ModelServer(models=("b", "a"))
        try:
            rows = LmStudioClient(_config(server, "lmstudio")).models.list()
            assert [r.name for r in rows] == ["a", "b"]
            assert all(r.capabilities is None for r in rows)
            assert all(
                r.max_context_catalogue is None and r.max_context_loaded is None for r in rows
            )
        finally:
            server.close()

    def test_a_catalog_listing_reads_the_context_and_seeds_it(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.contexts["a"] = 32_000
        try:
            client = GenericClient(_config(server, "generic"))
            rows = {r.name: r for r in client.models.list()}
            assert rows["a"].max_context_catalogue == 32_000
            assert rows["a"].state is ModelState.UNKNOWN  # a catalog has no load state
            assert rows["b"].max_context_catalogue is None
            found = client.models.get("a")
            assert found is not None and found.max_context_catalogue == 32_000
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
    def test_llamacpp_reads_the_modalities_and_cannot_say_which_efforts_reach(
        self, server: ModelServer
    ) -> None:
        # The effort is a template variable only some templates read;
        # the server has no word on which, so the efforts are unknown.
        server.window = 4096
        server.props = {"modalities": {"vision": False}}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == Capabilities(
            vision=False, reasoning=None, text_completion=True, structured_output=True
        )
        server.props = {"modalities": {"vision": True}}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == Capabilities(
            vision=True, reasoning=None, text_completion=True, structured_output=True
        )
        # Props without modalities, an older build: still the raw wire
        # and constrained decoding, the rest unknown.
        server.props = {}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == Capabilities(
            vision=None, reasoning=None, text_completion=True, structured_output=True
        )

    def test_koboldcpp_reads_vision_off_its_version_and_knows_its_budget_efforts(
        self, server: ModelServer
    ) -> None:
        server.version = {"version": "1.120", "vision": False}
        row = KoboldCppClient(_config(server, "koboldcpp")).models.list()[0]
        assert row.capabilities == Capabilities(
            vision=False, reasoning=ALL_EFFORTS, text_completion=True, structured_output=True
        )

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
            assert alpha.capabilities == Capabilities(
                vision=True,
                reasoning=frozenset({"none", "low", "medium", "high", "max"}),
                text_completion=False,
                structured_output=True,
            )
            assert beta.capabilities == Capabilities(
                vision=False, reasoning=frozenset(), text_completion=False, structured_output=True
            )
            assert client.models.get("alpha") == alpha  # the row is kept, the card asked once
            assert len([b for b in server.requests if set(b) == {"model"}]) == 2
        finally:
            server.close()

    def test_omlx_reads_the_type_and_the_toggle_and_lists_only_language_models(self) -> None:
        # `thinking_default`, a bool, is the template's thinking toggle:
        # every effort reaches; absent, none does.
        server = ModelServer(models=("vl", "lm", "unsaid", "emb", "rerank"))
        server.status = True
        server.types = {"vl": "vlm", "lm": "llm", "emb": "embedding", "rerank": "reranker"}
        server.thinking = {"vl": False}
        try:
            rows = {r.name: r for r in OmlxClient(_config(server, "omlx")).models.list()}
            assert set(rows) == {"vl", "lm", "unsaid"}  # an embedder plays no story
            assert rows["vl"].capabilities == Capabilities(
                vision=True, reasoning=ALL_EFFORTS, text_completion=True
            )
            assert rows["lm"].capabilities == Capabilities(
                vision=False, reasoning=frozenset(), text_completion=True
            )
            # No type on the row: whether it sees is unknown, not assumed.
            assert rows["unsaid"].capabilities == Capabilities(
                vision=None, reasoning=frozenset(), text_completion=True
            )
        finally:
            server.close()

    def test_openrouter_reads_the_modalities_and_the_reasoning_object(self) -> None:
        server = ModelServer(models=("free", "fixed", "mute", "plain", "bare"))
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
            "bare": {},
        }
        try:
            rows = {
                r.name: r
                for r in OpenRouterClient(_config(server, "openrouter", api_key="k")).models.list()
            }
            # The text wire is per model and the catalog does not say
            # which take it: unknown on every row.
            assert rows["free"].capabilities == Capabilities(
                vision=True,
                audio=False,
                reasoning=frozenset({"none", "minimal", "low", "high"}),
                text_completion=None,
                structured_output=True,
            )
            assert rows["fixed"].capabilities == Capabilities(
                vision=False,
                audio=False,
                reasoning=frozenset({"low", "medium", "high"}),
                text_completion=None,
                structured_output=False,
            )
            # No modalities named, no reasoning object: what a row does
            # not say is unknown, not allowed.
            assert rows["mute"].capabilities == Capabilities(
                vision=None, reasoning=frozenset(), text_completion=None, structured_output=False
            )
            # A reasoning object naming no efforts: no effort selection,
            # so only off reaches.
            assert rows["plain"].capabilities == Capabilities(
                vision=None,
                reasoning=frozenset({"none"}),
                text_completion=None,
                structured_output=False,
            )
            assert rows["bare"].capabilities == Capabilities(
                vision=None, reasoning=None, text_completion=None, structured_output=None
            )
        finally:
            server.close()

    def test_nanogpt_reads_its_capabilities_and_efforts(self) -> None:
        server = ModelServer(models=("seeing", "fixed", "mute", "bare"))
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
            "bare": {},
        }
        try:
            rows = {
                r.name: r
                for r in NanoGptClient(_config(server, "nanogpt", api_key="k")).models.list()
            }
            # The efforts as listed, "none" among them only where the
            # model takes it; the text wire is per model, unsaid.
            assert rows["seeing"].capabilities == Capabilities(
                vision=True,
                audio=False,
                reasoning=frozenset({"low", "high"}),
                text_completion=None,
                structured_output=True,
            )
            # Reasons, but names no efforts: which efforts reach is unknown.
            assert rows["fixed"].capabilities == Capabilities(
                vision=False, reasoning=None, text_completion=None
            )
            assert rows["mute"].capabilities == Capabilities(
                vision=False, reasoning=frozenset(), text_completion=None
            )
            assert rows["bare"].capabilities is None
        finally:
            server.close()

    def test_lmstudio_reads_vision_off_its_registry_and_takes_no_effort(self) -> None:
        # Its endpoint has no reasoning knob: nothing sent reaches the
        # model, a known "none". Vision is the registry's word, and a
        # registry that says nothing of it leaves it unknown.
        server = ModelServer(models=("seeing", "blind", "unsaid"), managed=True)
        server.capabilities = {"seeing": ["vision"], "blind": []}
        try:
            rows = {r.name: r for r in LmStudioClient(_config(server, "lmstudio")).models.list()}
            assert rows["seeing"].capabilities == Capabilities(
                vision=True, reasoning=frozenset(), text_completion=True, structured_output=True
            )
            assert rows["blind"].capabilities == Capabilities(
                vision=False, reasoning=frozenset(), text_completion=True, structured_output=True
            )
            assert rows["unsaid"].capabilities == Capabilities(
                vision=None, reasoning=frozenset(), text_completion=True, structured_output=True
            )
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

    def test_a_llamacpp_router_row_carries_the_window_the_props_gave(self) -> None:
        server = ModelServer(models=("a", "b"))
        server.router = True
        server.loaded.add("a")
        server.window = 4096
        try:
            rows = {r.name: r for r in LlamaCppClient(_config(server, "llamacpp")).models.list()}
            assert rows["a"].max_context_loaded == 4096
            assert rows["b"].max_context_loaded is None
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
        from otaku.backend.api import providers as api_providers
        from scenarios.support.harness import launch, set_config_provider

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

    def test_a_key_that_is_missing_or_wrong(self, server: ModelServer) -> None:
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
        assert (
            str(caught.value)
            == "Refused by generic with HTTP 503: "
            + '{"error": {"message": "refused by the script"}}'
        )

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
        with pytest.raises(DeclinedError, match="The model declined: content filtered"):
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
        assert log.statuses == ["failed: UnreachableError"]


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
