"""KoboldCpp: one model per process, chosen at launch — no load or
unload. Chat rides the OpenAI protocol at /v1; the native surface adds
KoboldAI's model endpoint (the active model, admin mode or not), the
true max context length, the feature flags of `/api/extra/version`
(vision and audio among them), and a token count that renders a chat
request the way a turn would. A reasoning effort goes out on both
knobs: `reasoning_effort` becomes a reasoning budget — none is none,
minimal a tenth of the reply, low a quarter, medium half, high and
above unlimited — and the template's flag is read under `--jinja` from
1.120 on. The native reads are exempt from the server's password, the
token count apart, so the key is judged by the model endpoint, which
masks its answer for a key it does not accept.
"""

from collections.abc import Sequence
from typing import ClassVar

from otaku.providers.clients import launched_port
from otaku.providers.errors import UnauthorizedError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, Http, positive_int
from otaku.providers.openai import reasoning
from otaku.providers.openai.client import OpenAIClient
from otaku.providers.openai.completion import (
    PROTOCOL_PARAMS,
    SAMPLER_PARAMS,
    Bounds,
    OpenAICompletion,
)
from otaku.providers.openai.models import (
    Listing,
    Locality,
    ModelCapabilities,
    ModelInfo,
    ModelState,
    OpenAIModels,
)
from otaku.providers.openai.requests import Image, WireMessage
from otaku.settings.providers import ProviderConfig

# The listing's names that are no model, as the server spells them —
# never behind its "koboldcpp/" prefix, which only a model gets: its
# word for nothing loaded, and router mode's two orders.
# The rungs its budget tells apart — none 0, minimal a tenth, low a
# quarter, medium half of the context; high is unlimited, and xhigh and
# max would be high again.
_GRADED_EFFORTS: frozenset[str] = frozenset({"none", "minimal", "low", "medium", "high"})
_NOT_A_MODEL = frozenset({"inactive", "initial_model", "unload_model"})
# What the model endpoint answers, behind `--password`, a key it does
# not accept.
_PROTECTED = "koboldcpp/protected-model"


class KoboldCppModels(OpenAIModels):
    def _list(self, http: Http) -> Listing:
        """The one loaded model, the engine's own name prefix stripped;
        "inactive" is the server's name for none. The model endpoint is
        read once for two things: the key — a masked answer is a key
        the server rejects, or none configured — and the active model,
        which refines the state when the endpoint answers; the loaded
        context size and the flags are the server's, read once for
        every name. Four reads, one budget."""
        named = self._model_named(http)
        if named == _PROTECTED:
            rejected = UnauthorizedError(
                f"No api key for {self._config.name}."
                if self._auth.key_source is None
                else f"The api key was rejected by {self._config.name}."
            )
            http.record(rejected)
            raise rejected
        # KoboldCpp brands its ids "koboldcpp/<name>"; the picker shows
        # bare names under provider captions, so the prefix goes. Chat is
        # unaffected: outside router mode the engine ignores the request's
        # model field.
        names = sorted(
            listed.name.removeprefix("koboldcpp/")
            for listed in super()._list(http)
            if listed.name not in _NOT_A_MODEL
        )
        if not names:
            return []
        if named is None:
            active = None  # no model endpoint: the one model listed is the loaded one
        else:
            # "inactive" is the server's word for none: no name matches it.
            active = "" if named == "inactive" else named.removeprefix("koboldcpp/")
        max_context_loaded = self._loaded_size(http)
        # The server's feature flags: vision means a projector was loaded
        # beside the model, audio that it hears. A rung reaches the model
        # as a share of the context spent on thinking, a budget as
        # itself; the raw wire is there; decoding is constrained
        # server-side. A probe that did not answer states nothing, and
        # the cache keeps what an earlier one read.
        version = http.get(
            f"{self._config.base_url}/api/extra/version", timeout=PROBE_TIMEOUT, quiet=True
        )
        capabilities = (
            ModelCapabilities(
                vision=bool(version["vision"]) if "vision" in version else None,
                audio=bool(version["audio"]) if "audio" in version else None,
                reasoning_efforts=_GRADED_EFFORTS,
                reasoning_switch=False,
                reasoning_budget=True,
                text_completion=True,
                structured_output=True,
            )
            if isinstance(version, dict)
            else None
        )
        models = []
        for name in names:
            loaded = active is None or name == active
            models.append(
                ModelInfo(
                    name=name,
                    max_context_loaded=max_context_loaded if loaded else None,
                    capabilities=capabilities,
                    state=ModelState.LOADED if loaded else ModelState.UNLOADED,
                )
            )
        return models

    def _get(self, name: str, http: Http) -> ModelInfo | None:
        named = self._model_named(http)
        if named is None:
            return None
        if named == _PROTECTED:
            return ModelInfo(name=name, state=ModelState.UNKNOWN)  # a key it does not accept
        if named == "inactive" or name != named.removeprefix("koboldcpp/"):
            return ModelInfo(name=name, state=ModelState.UNLOADED)
        max_context_loaded = self._loaded_size(http)
        if max_context_loaded is None:
            return None  # the sizing probe missed: the last word stands
        return ModelInfo(name=name, max_context_loaded=max_context_loaded, state=ModelState.LOADED)

    # ---------- the native surface ----------

    def _model_named(self, http: Http) -> str | None:
        """What KoboldAI's model endpoint names: `koboldcpp/<name>` for
        the model loaded, "inactive" for none, `_PROTECTED` behind
        `--password` for a key it does not accept — or None when it
        does not answer. `http` is the view of the ask that is running,
        the listing's or the model's."""
        data = http.get(f"{self._config.base_url}/api/v1/model", timeout=PROBE_TIMEOUT, quiet=True)
        named = data.get("result") if isinstance(data, dict) else None
        return named if isinstance(named, str) else None

    def _loaded_size(self, http: Http) -> int | None:
        """The context size the loaded model serves, off the true max
        context length; None when the probe did not answer."""
        sizing = http.get(
            f"{self._config.base_url}/api/extra/true_max_context_length",
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        return positive_int(sizing.get("value")) if isinstance(sizing, dict) else None


class KoboldCppCompletion(OpenAICompletion):
    # One penalty sampler, presence: `frequency_penalty` is read only in
    # its place, as a presence term, so it is not advertised. The
    # repetition penalty is floored at 1 — below it, the server sends
    # its own 1.0 — and the rest is unbounded, as on every local engine.
    supported_params = (PROTOCOL_PARAMS | SAMPLER_PARAMS) - {"frequency_penalty"}
    bounds: ClassVar[dict[str, Bounds]] = {
        "temperature": (0, None),
        "presence_penalty": (None, None),
        "repetition_penalty": (1, None),
    }
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.EFFORT_KNOB, reasoning.SWITCH_TEMPLATE_KNOB, reasoning.BUDGET_TOKENS_KNOB}
    )
    # The budget is applied on every path, the raw one included.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.EFFORT_KNOB, reasoning.BUDGET_TOKENS_KNOB}
    )
    can_count_tokens = True

    def count_chat_tokens(
        self,
        model: str,
        messages: Sequence[WireMessage],
        *,
        level: str | None = None,
        images: Sequence[Image] = (),
        timeout: float = ASK_TIMEOUT,
    ) -> int | None:
        # Given messages, the count renders them through the same
        # transform a chat turn gets, jinja template included — the
        # template's default, though: the endpoint reads no template
        # kwargs, and it renders an image as a placeholder line.
        body, _ = self._chat_request(model, messages, {}, level=level, images=images)
        return self._count({"messages": body["messages"]}, timeout)

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        return self._count({"prompt": prompt}, timeout)

    def _count(self, body: dict[str, object], timeout: float) -> int | None:
        data = self._http.post(
            f"{self._config.base_url}/api/extra/tokencount", body, timeout=timeout, quiet=True
        )
        return positive_int(data.get("value")) if isinstance(data, dict) else None


class KoboldCppClient(OpenAIClient):
    id = "koboldcpp"
    label = "KoboldCpp"
    locality = Locality.LOCAL
    env_key = "KOBOLDCPP_API_KEY"
    models_class = KoboldCppModels
    completion_class = KoboldCppCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags, nothing on disk: a running server's
        # own port is read, else the standard default.
        port = launched_port("koboldcpp") or 5001
        return ProviderConfig(name=cls.id, url=f"http://localhost:{port}/v1")
