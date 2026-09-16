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
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from otaku.providers import (
    ALL_CLIENTS,
    Chunk,
    DeclinedError,
    Image,
    KeySource,
    ModelCapabilities,
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


class Errors:
    """An error sink that keeps what it was told."""

    def __init__(self) -> None:
        self.filed: list[tuple[str, BaseException]] = []

    def record(self, context: str, exc: BaseException) -> Path:
        self.filed.append((context, exc))
        return Path()


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
            ("lmstudio", EFFORT_NONE),
        ],
    )
    def test_an_effort_goes_out_on_the_knobs_the_provider_reads(
        self, server: ModelServer, kind: str, expected: dict[str, object]
    ) -> None:
        # The table every provider is promised by: both knobs where a
        # local engine reads the template's flag, the effort alone on the
        # catalogs, Ollama and LM Studio — and the turn plays in every
        # case.
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

    def test_the_generic_provider_sends_the_effort_on_every_chat_knob(
        self, server: ModelServer
    ) -> None:
        # Permissive: the url could name any engine, so the effort goes
        # out by name, as the template's flag and as its variable, and
        # the server drops what it does not read.
        client = GenericClient(_config(server, "generic"))
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="high"))
        assert _knobs(_sent(server, "messages")) == {
            "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "high"},
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
            _drain(client.completion.chat("x:thinking", [Turn("user", "u")], {}, effort="none"))
        assert len(server.requests) == 1

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
        assert row.capabilities == ModelCapabilities(
            vision=False, reasoning=None, text_completion=True, structured_output=True
        )
        server.props = {"modalities": {"vision": True}}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=True, reasoning=None, text_completion=True, structured_output=True
        )
        # Props without modalities, an older build: still the raw wire
        # and constrained decoding, the rest unknown.
        server.props = {}
        row = LlamaCppClient(_config(server, "llamacpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=None, reasoning=None, text_completion=True, structured_output=True
        )

    def test_koboldcpp_reads_vision_off_its_version_and_knows_its_budget_efforts(
        self, server: ModelServer
    ) -> None:
        server.version = {"version": "1.120", "vision": False, "audio": True}
        row = KoboldCppClient(_config(server, "koboldcpp")).models.list()[0]
        assert row.capabilities == ModelCapabilities(
            vision=False,
            audio=True,
            reasoning=ALL_EFFORTS,
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
            assert (first.state, first.max_context_loaded) == (ModelState.LOADED, 4096)
            server.version = None
            again = client.models.list()[0]
            assert again.capabilities is not None and again.capabilities.vision is True
            server.window = None
            got = client.models.get("m")
            assert got is not None
            assert (got.state, got.max_context_loaded) == (ModelState.LOADED, 4096)
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
            assert alpha.capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                reasoning=frozenset({"none", "low", "medium", "high", "max"}),
                text_completion=False,
                structured_output=True,
            )
            assert beta.capabilities == ModelCapabilities(
                vision=False,
                audio=False,
                reasoning=frozenset(),
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
            assert [(r.name, r.max_context_catalogue) for r in rows] == [("alpha", 131_072)]
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
            assert {(r.state, r.max_context_loaded) for r in rows} == {(ModelState.UNKNOWN, None)}
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
            alpha = client.models.get("alpha")
            assert alpha is not None and alpha.capabilities is not None
            assert (alpha.capabilities.audio, alpha.capabilities.vision) == (True, False)
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
            assert rows["vl"].capabilities == ModelCapabilities(
                vision=True, reasoning=ALL_EFFORTS, text_completion=True, structured_output=True
            )
            assert rows["lm"].capabilities == ModelCapabilities(
                vision=False, reasoning=frozenset(), text_completion=True, structured_output=True
            )
            # No type on the row: whether it sees is unknown, not assumed.
            assert rows["unsaid"].capabilities == ModelCapabilities(
                vision=None, reasoning=frozenset(), text_completion=True, structured_output=True
            )
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
            assert (rows["cold"].state, rows["cold"].max_context) == (ModelState.UNLOADED, 60_000)
            assert (rows["warm"].state, rows["warm"].max_context) == (ModelState.LOADED, 131_072)
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
            assert rows["free"].capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                reasoning=frozenset({"none", "minimal", "low", "high"}),
                text_completion=None,
                structured_output=True,
            )
            assert rows["fixed"].capabilities == ModelCapabilities(
                vision=False,
                audio=False,
                reasoning=frozenset({"low", "medium", "high"}),
                text_completion=None,
                structured_output=False,
            )
            # No modalities named, no reasoning object: what a row does
            # not say is unknown, not allowed.
            assert rows["mute"].capabilities == ModelCapabilities(
                vision=None, reasoning=frozenset(), text_completion=None, structured_output=False
            )
            # A reasoning object naming no efforts: no selection among
            # them, so every effort reaches as on — and none as off, unless
            # reasoning is mandatory.
            assert rows["plain"].capabilities == ModelCapabilities(
                vision=None, reasoning=ALL_EFFORTS, text_completion=None, structured_output=False
            )
            assert rows["forced"].capabilities == ModelCapabilities(
                vision=None,
                reasoning=ALL_EFFORTS - {"none"},
                text_completion=None,
                structured_output=False,
            )
            assert rows["bare"].capabilities == ModelCapabilities(
                vision=None, reasoning=None, text_completion=None, structured_output=None
            )
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
            # The efforts as listed, "none" among them only where the
            # model takes it; the text wire is per model, unsaid.
            assert rows["seeing"].capabilities == ModelCapabilities(
                vision=True,
                audio=False,
                reasoning=frozenset({"low", "high"}),
                text_completion=None,
                structured_output=True,
            )
            # Reasons, but names no efforts: which efforts reach is unknown.
            assert rows["fixed"].capabilities == ModelCapabilities(
                vision=False, reasoning=None, text_completion=None
            )
            assert rows["mute"].capabilities == ModelCapabilities(
                vision=False, reasoning=frozenset(), text_completion=None
            )
            # A null flag is the catalog's "unknown", not a no; the output
            # limit is read whether or not the flags are there.
            assert rows["unsure"].capabilities == ModelCapabilities(text_completion=None)
            assert rows["unsure"].max_output_tokens == 4096
            assert rows["bare"].capabilities is None
            assert rows["bare"].max_output_tokens == 8192
        finally:
            server.close()

    def test_lmstudio_reads_vision_off_its_registry_and_cannot_say_of_reasoning(self) -> None:
        # Vision is the registry's word, and a registry that says nothing
        # of it leaves it unknown. Whether the effort it takes reaches a
        # given model it never says: unknown on every row.
        server = ModelServer(models=("seeing", "blind", "unsaid"), managed=True)
        server.capabilities = {"seeing": ["vision"], "blind": []}
        try:
            rows = {r.name: r for r in LmStudioClient(_config(server, "lmstudio")).models.list()}
            assert rows["seeing"].capabilities == ModelCapabilities(
                vision=True, text_completion=True, structured_output=True
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
        # the images on the last message — what the template renders.
        client.completion.count_chat_tokens(
            "m", [Turn("user", "u")], effort="none", images=[Image(b"x", "image/png")]
        )
        counted = server.requests[-1]
        assert counted["chat_template_kwargs"] == {"enable_thinking": False}
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
            assert rows["a"].max_context_loaded == 4096
            assert rows["b"].max_context_loaded is None
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
            assert (before.size, before.max_context_catalogue) == (None, None)
            client.models.load("a")
            after = client.models.get("a")
            assert after is not None
            assert (after.state, after.max_context_loaded) == (ModelState.LOADED, 4096)
            assert (after.size, after.max_context_catalogue) == (5_000_000, 32768)
            # Unloaded, the router states neither again; the listing
            # keeps what `get` learned.
            client.models.unload("a")
            relisted = {r.name: r for r in client.models.list()}
            assert (relisted["a"].size, relisted["a"].max_context_catalogue) == (5_000_000, 32768)
            assert relisted["a"].max_context_loaded is None
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
        assert rows[0].max_context_loaded == 4096 and rows[0].capabilities is not None

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

    def test_the_panel_says_where_the_key_comes_from(
        self, server: ModelServer, tmp_path, monkeypatch
    ) -> None:
        # What the field captions, the value never shown: the variable's
        # until a key is typed, the section's while one is, the
        # variable's again once it is cleared. A provider not configured
        # yet is asked off its default section, so a variable shows
        # before a section exists.
        from otaku.backend.api import providers as api_providers
        from scenarios.support.harness import launch, set_config_provider

        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        monkeypatch.setenv("NANOGPT_API_KEY", "from-env")
        monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)
        set_config_provider(tmp_path / "state", server, name="openrouter", api_key="")
        app = launch(tmp_path / "state", server, spec="openrouter/test-model")
        try:
            source = lambda name: api_providers.key_source(app.session, name)  # noqa: E731
            assert source("openrouter") is KeySource.ENV
            api_providers.save_field(app.session, "openrouter", "api_key", "typed")
            assert source("openrouter") is KeySource.CONFIG
            api_providers.clear_field(app.session, "openrouter", "api_key")
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
        _drain(client.completion.chat("m", [Turn("user", "u")], {}, effort="low"))
        counting = LlamaCppClient(ProviderConfig(name="llamacpp", url=DEAD), error_sink=errors)
        assert counting.completion.count_text_tokens("m", "p") is None
        assert errors.filed == []


def _config(server: ModelServer, kind: str, **fields: str) -> ProviderConfig:
    return ProviderConfig(name=kind, url=server.url, **fields)  # type: ignore[arg-type]


class _Interrupted(BaseException):
    """The terminal's ctrl-c, as a test can raise it: no Exception, so
    the wrapper's idle hook does not swallow it, and not the real
    KeyboardInterrupt, which would stop the run."""


def _interrupt() -> None:
    raise _Interrupted


def _filed(sink: Sink, status: str, *, within: float) -> bool:
    """Whether the take filed `status` within `within` seconds: the
    filing happens in the pump's thread, after the cut wakes it."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if sink.statuses and sink.statuses[-1] == status:
            return True
        time.sleep(0.01)
    return False


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
