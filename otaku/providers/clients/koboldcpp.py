"""KoboldCpp: one model per process, chosen at launch — no load or
unload. Chat rides the OpenAI protocol at /v1; the native surface adds
admin mode's active model, the true max context length, the feature flags
of `/api/extra/version` (vision among them), and a token count that
renders a chat request the way a turn would. A reasoning effort goes out
on both knobs: `reasoning_effort` becomes a reasoning budget — none is
none, minimal a tenth of the reply, low a quarter, medium half, high
and above unlimited — and the template's flag is read under `--jinja`.
Every native read is exempt from the server's password, so the key is
verified against admin's model endpoint, which masks its answer for a
wrong one.
"""

from collections.abc import Sequence
from typing import ClassVar

from otaku.providers import http
from otaku.providers.clients import launched_port
from otaku.providers.errors import UnauthorizedError
from otaku.providers.http import ASK_TIMEOUT, PROBE_TIMEOUT, positive_int
from otaku.providers.openai import reasoning, requests
from otaku.providers.openai.auth import OpenAIAuth
from otaku.providers.openai.client import Locality, OpenAIClient
from otaku.providers.openai.completion import OpenAICompletion
from otaku.providers.openai.models import Capabilities, Listing, ModelInfo, ModelState, OpenAIModels
from otaku.providers.openai.requests import WireMessage
from otaku.settings.providers import ProviderConfig

# The listing's names that are no model: the server's word for nothing
# loaded, and router mode's two orders.
_NOT_A_MODEL = frozenset({"inactive", "initial_model", "unload_model"})


class KoboldCppAuth(OpenAIAuth):
    def verify_key(self, timeout: float) -> None:
        # Admin's model endpoint answers everyone, but masks the name as
        # "protected-model" when a password is set and this key is not
        # it — the one place a wrong key shows before a turn. Quiet: a
        # server without the endpoint has nothing to verify, and a dead
        # one fails the listing that follows.
        data = http.get_json(
            f"{self._config.base_url}/api/v1/model",
            name=self._config.name,
            headers=self.headers,
            timeout=timeout,
            quiet=True,
        )
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, str) and result.endswith("protected-model"):
            raise UnauthorizedError(f"The api key was rejected by {self._config.name}.")


class KoboldCppModels(OpenAIModels):
    def _list(self, timeout: float) -> Listing:
        """The one loaded model, the engine's own name prefix stripped;
        "inactive" is the server's name for none. Admin mode's active
        model refines the state when that surface answers; the loaded
        context size and the flags are the server's, read once for every
        name."""
        self._auth.verify_key(timeout)
        names = sorted(
            bare
            for listed in super()._list(timeout)
            if (bare := _bare(listed.name)) not in _NOT_A_MODEL
        )
        if not names:
            return []
        active = self._active_model(timeout)
        sizing = http.get_json(
            f"{self._config.base_url}/api/extra/true_max_context_length",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=min(timeout, PROBE_TIMEOUT),
            quiet=True,
        )
        max_context_loaded = positive_int(sizing.get("value")) if isinstance(sizing, dict) else None
        # The server's feature flags: vision means a projector was loaded
        # beside the model. Every effort reaches the model as a budget;
        # the raw wire is there; decoding is constrained server-side.
        version = http.get_json(
            f"{self._config.base_url}/api/extra/version",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=min(timeout, PROBE_TIMEOUT),
            quiet=True,
        )
        capabilities = Capabilities(
            vision=bool(version["vision"])
            if isinstance(version, dict) and "vision" in version
            else None,
            reasoning=reasoning.ALL_EFFORTS,
            text_completion=True,
            structured_output=True,
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
                    checked=True,
                )
            )
        return models

    def _state(self, name: str) -> tuple[ModelState, int | None] | None:
        active = self._active_model()
        if active is not None and name != active:
            return ModelState.UNLOADED, None
        sizing = http.get_json(
            f"{self._config.base_url}/api/extra/true_max_context_length",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=PROBE_TIMEOUT,
            quiet=True,
        )
        max_context_loaded = positive_int(sizing.get("value")) if isinstance(sizing, dict) else None
        if active is None and max_context_loaded is None:
            return None
        return ModelState.LOADED, max_context_loaded

    # ---------- the native surface ----------

    def _active_model(self, timeout: float = PROBE_TIMEOUT) -> str | None:
        """The model admin mode reports as active; "" when none is
        ("inactive"), None when the endpoint does not answer or will not
        say — behind `--password` a request without the key is told only
        that a model is protected."""
        data = http.get_json(
            f"{self._config.base_url}/api/v1/model",
            name=self._config.name,
            headers=self._auth.headers,
            timeout=min(timeout, PROBE_TIMEOUT),
            quiet=True,
        )
        if isinstance(data, dict):
            model = data.get("result")
            if isinstance(model, str) and not model.endswith("protected-model"):
                return "" if model == "inactive" else _bare(model)
        return None


class KoboldCppCompletion(OpenAICompletion):
    chat_reasoning_knobs: ClassVar[frozenset[str]] = frozenset(
        {reasoning.EFFORT_KNOB, reasoning.FLAG_KNOB}
    )
    # The budget is applied on every path, the raw one included.
    text_reasoning_knobs: ClassVar[frozenset[str]] = frozenset({reasoning.EFFORT_KNOB})
    can_count_tokens = True

    def count_chat_tokens(
        self, model: str, messages: Sequence[WireMessage], timeout: float = ASK_TIMEOUT
    ) -> int | None:
        # Given messages, the count renders them through the same
        # transform a chat turn gets, jinja template included.
        body = {"messages": requests.chat_completion_body(model, messages, {})["messages"]}
        return self._count(body, timeout)

    def count_text_tokens(
        self, model: str, prompt: str, timeout: float = ASK_TIMEOUT
    ) -> int | None:
        return self._count({"prompt": prompt}, timeout)

    def _count(self, body: dict[str, object], timeout: float) -> int | None:
        data = http.post_json(
            f"{self._config.base_url}/api/extra/tokencount",
            body,
            name=self._config.name,
            headers=self._auth.headers,
            timeout=timeout,
            quiet=True,
        )
        count = data.get("value") if isinstance(data, dict) else None
        return count if isinstance(count, int) else None


class KoboldCppClient(OpenAIClient):
    id = "koboldcpp"
    label = "KoboldCpp"
    locality = Locality.LOCAL
    env_key = "KOBOLDCPP_API_KEY"
    auth_class = KoboldCppAuth
    models_class = KoboldCppModels
    completion_class = KoboldCppCompletion

    @classmethod
    def autoconfigure(cls) -> ProviderConfig:
        # Configured by launch flags, nothing on disk: a running server's
        # own flag is read, else the standard default.
        port = launched_port("koboldcpp") or 5001
        return ProviderConfig(name=cls.id, url=f"http://localhost:{port}/v1")


def _bare(name: str) -> str:
    """KoboldCpp reports its model as "koboldcpp/<name>" — its own brand
    on the id. The picker shows bare names under provider captions, so
    the prefix goes; chat is unaffected, the engine ignores the
    request's model field outside router mode, where the field names
    the model to swap to."""
    return name.removeprefix("koboldcpp/")
