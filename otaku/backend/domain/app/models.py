"""A model as otaku keeps and describes it. Its own settings, saved
under its name in models.toml: the inference parameters /set knows and
the thinking levels it takes — the vocabulary every menu is offered
through (`api.providers.models.settings`) — how a model's entry reads in
it (`read_entry`), what the vocabulary cannot read passed over and said
once, when the model becomes current (`entry_notes`); then the settings
operated: a model's as they stand on a provider (`describe`), its
thinking level and its parameters written, each write typed, held to
its bounds and refused for what it declines. And its capabilities in a
reader's words (`reasoning_words`, `capability_words`): the info
report's two rows on a model, and the picker's, on both frontends."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

from otaku.backend.errors import NO_MODEL_HINT, Conflict, Invalid, NotFound, Unavailable
from otaku.providers import (
    ModelCapabilities,
    ModelInfo,
    OpenAIClient,
    ProviderError,
    reasoning,
    templates,
)
from otaku.settings import models as models_file
from otaku.settings.state import ModelRef
from otaku.store.schema import CompletionType, Layout

if TYPE_CHECKING:
    # The App imports this module; a model's settings are operated on it.
    from otaku.backend.domain.app import App

# The /set think menu for a model nobody has described: unset first
# (the way out: no level, nothing sent), then the wire's own ladder of
# efforts in its order. What a model takes is a subset of it, or the
# switch's two words instead, or a budget besides — and that is what a
# menu offers (`api.providers.models.settings`).
THINK_UNSET: str = models_file.THINK_UNSET
THINK_MENU: tuple[str, ...] = (THINK_UNSET, *reasoning.EFFORT_LEVELS)

# The ladder's rungs in the wire's order, weakest to strongest, and the
# switch's two words, for a frontend listing what a model takes.
EFFORT_LEVELS: tuple[str, ...] = reasoning.EFFORT_LEVELS
SWITCH_LEVELS: tuple[str, ...] = reasoning.SWITCH_LEVELS


@dataclass(frozen=True)
class Parameter:
    """One inference parameter as /set takes it: how a typed word or a
    saved value is read, and the range the engines agree on — the
    protocol's own bounds for what it defines, the samplers' natural
    ones. `low` None takes any value of the kind (a seed, a stop
    string); `high` None is no ceiling. Checked once, in the setter,
    for both frontends."""

    kind: type
    low: float | None = None
    high: float | None = None


# The inference parameters otaku understands.
PARAMETERS: dict[str, Parameter] = {
    "temperature": Parameter(float, 0, 2),
    "top_p": Parameter(float, 0, 1),
    "top_k": Parameter(int, 0),
    "min_p": Parameter(float, 0, 1),
    "max_tokens": Parameter(int, 1),
    "presence_penalty": Parameter(float, -2, 2),
    "frequency_penalty": Parameter(float, -2, 2),
    "repetition_penalty": Parameter(float, 0, 2),
    "seed": Parameter(int),
    # Stop strings, several: every engine takes a list, and a stop may
    # hold a newline or a space (`parameter_text` reads
    # them back).
    "stop": Parameter(list),
}


@dataclass(frozen=True)
class SavedModelSettings:
    """A model's entry in models.toml as the vocabulary reads it: its
    thinking level — a level, "unset" (the way out: nothing sent), None
    where no row says — its parameters by name, and the text template
    its line names; what the vocabulary cannot read is passed over."""

    think: str | None
    params: dict[str, object]
    text_template: str | None

    @property
    def level(self) -> str | None:
        """The level a request sends: None where nothing is — "unset", or
        no row yet (the model decides)."""
        return None if self.think in (None, THINK_UNSET) else self.think


def read_entry(entry: Mapping[str, object]) -> SavedModelSettings:
    """A models.toml entry as the vocabulary reads it."""
    params: dict[str, object] = {}
    for name, value in entry.items():
        known = PARAMETERS.get(name)
        if known is None:
            continue
        try:
            params[name] = _saved_parameter(known, value)
        except (TypeError, ValueError):
            continue
    think = entry.get(models_file.THINK_KEY)
    template = entry.get(models_file.TEMPLATE_KEY)
    return SavedModelSettings(
        think=str(think) if _is_think(think) else None,
        params=params,
        text_template=(
            template if isinstance(template, str) and template in templates.TEXT_TEMPLATES else None
        ),
    )


def entry_notes(entry: Mapping[str, object], model: str) -> list[str]:
    """What a model made current is told about its entry, as sentences:
    each saved value the vocabulary no longer makes sense of, which
    `read_entry` passes over."""
    notes: list[str] = []
    for name, value in entry.items():
        if name == models_file.TEMPLATE_KEY:
            # The user's own word, by hand: one of the templates otaku
            # renders, or a line it cannot honour.
            if not (isinstance(value, str) and value in templates.TEXT_TEMPLATES):
                names = ", ".join(templates.TEXT_TEMPLATES)
                notes.append(
                    f"Ignoring unknown text_template {value!r} saved for {model}; "
                    f"text templates: {names}."
                )
        elif name == models_file.THINK_KEY:
            if not _is_think(value):
                notes.append(f"Ignoring invalid think value {value!r} saved for {model}.")
        elif (known := PARAMETERS.get(name)) is None:
            notes.append(f"Ignoring unknown parameter {name!r} saved for {model}.")
        else:
            try:
                _saved_parameter(known, value)
            except (TypeError, ValueError):
                notes.append(f"Ignoring invalid {name} value {value!r} saved for {model}.")
    return notes


def parameter_text(value: object) -> str:
    """A parameter's value as a reader reads and types it: a number as
    it is, the stop strings each a JSON string — so a newline shows as
    \\n — one bare where it holds no quote or space."""
    if isinstance(value, list):
        return " ".join(json.dumps(stop, ensure_ascii=False) for stop in value)
    return str(value)


def parse_stops(raw: str) -> list[str]:
    """Stop strings as typed: JSON strings separated by spaces —
    `"\\nUser:" "END"` — so a stop may hold a newline or a space; a
    text that does not open with a quote is one stop, as it is. Raises
    ValueError for a quote left open, or an empty stop."""
    text = raw.strip()
    if not text.startswith('"'):
        stops = [text] if text else []
    else:
        decoder = json.JSONDecoder()
        stops = []
        at = 0
        while at < len(text):
            if text[at].isspace():
                at += 1
                continue
            if text[at] != '"':
                raise ValueError(f"a stop string needs quotes at {text[at:]!r}")
            stop, at = decoder.raw_decode(text, at)
            stops.append(stop)
    if not stops or any(not stop for stop in stops):
        raise ValueError("an empty stop")
    return stops


def _is_think(value: object) -> bool:
    """A level the wire spells, or the way out — anything else is a hand
    edit the engine would refuse. A budget is digits, quoted or not."""
    return isinstance(value, str | int) and (
        value == models_file.THINK_UNSET or reasoning.is_level(str(value))
    )


def _saved_parameter(parameter: Parameter, value: object) -> object:
    """A saved value as the live one: the kind's own, and for a list —
    the stop strings — the array as saved, or one string from a file
    written when stop held one. Raises ValueError for anything else."""
    if parameter.kind is not list:
        return parameter.kind(value)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(value)


# ---------- a model's settings, operated ----------


@dataclass(frozen=True)
class ParameterFacts:
    """One parameter as a model holds it: its value saved for the model
    (None while unset), its kind's name ("float", "int", or "list" for the
    stop strings), whether it reaches the model on the provider serving
    it (a list, never a gate: one model is served by more than one
    provider), and the bounds a value is held to — the provider's own
    where it states them — None on a side that has none."""

    name: str
    value: object | None
    kind: str
    supported: bool
    low: float | None
    high: float | None


@dataclass(frozen=True)
class ModelSettingsFacts:
    """A model's settings on a provider: its thinking level ("unset"
    where nothing is sent), the levels it takes — "unset" first, then the
    ladder's rungs it grades or off and on where it switches — and
    whether a budget is taken beside them, every parameter the
    vocabulary knows in its order, the ones saved (in the order the file
    holds them), and the text template the text wire renders it with
    (None where none is known)."""

    provider: str
    model: str
    think: str
    levels: tuple[str, ...]
    budget: bool
    parameters: tuple[ParameterFacts, ...]
    saved: dict[str, object]
    text_template: str | None


def get_model_settings(app: App, provider: str, model: str) -> ModelSettingsFacts:
    """`model`'s settings on `provider`, as models.toml and the provider's
    description of the model stand: what it takes where the provider has
    described it, everything where it cannot say or has not listed the
    model yet — an engine that reports nothing loses nothing. Provider ""
    (no model selected) answers what any model would take. Raises
    NotFound for a provider that is not configured."""
    client = _client_of(app, provider)
    found = client.models.cached(model) if client is not None and model else None
    caps = found.capabilities if found is not None else None
    held = app._model_settings(model)
    reaching = client.supported_params_of(model) if client is not None else None
    template = (
        templates.resolve(
            held.text_template, found.text_template if found is not None else None, model
        )
        if model
        else None
    )
    levels, budget = _choices(caps)
    return ModelSettingsFacts(
        provider=provider,
        model=model,
        think=held.think if held.think is not None else THINK_UNSET,
        levels=levels,
        budget=budget,
        parameters=tuple(
            ParameterFacts(
                name=name,
                value=held.params.get(name),
                kind=parameter.kind.__name__,
                supported=reaching is None or name in reaching,
                low=_bounds(client, name, parameter)[0],
                high=_bounds(client, name, parameter)[1],
            )
            for name, parameter in PARAMETERS.items()
        ),
        saved=dict(held.params),
        text_template=template.name if template is not None else None,
    )


def set_think(app: App, provider: str, model: str, level: str) -> str:
    """A thinking level saved for a model — a rung, off or on, or a
    budget in tokens — or "unset", saved too: nothing is sent, and the
    model is not started off again; the sentence that says so. Raises
    Refused without a model, for a provider not configured, and for a
    level the model does not take — never for the provider: a level goes
    out on whatever knobs it reads, and on none where it reads none."""
    _model_client(app, provider, model)
    facts = get_model_settings(app, provider, model)
    listed = levels_text(facts.levels, facts.budget)
    value = level.strip().lower()
    budget = reasoning.budget_of(value)
    if budget is not None:
        if not facts.budget:
            raise Invalid(
                f"{model} does not take a thinking budget. Levels for this model: {listed}."
            )
        value = str(budget)  # as a number reads: no leading zeros
    elif value not in facts.levels:
        raise Invalid(f"{model} does not take {value}. Levels for this model: {listed}.")
    app._write_model_settings(model, {models_file.THINK_KEY: value})
    return f"Think: {think_word(value)}."


def set_parameter(app: App, provider: str, model: str, name: str, value: object) -> str:
    """One parameter saved for a model: `value` the kind's own (a number,
    the stop strings as a list) or as a reader types it (`parameter_text`
    reads back what this parses), held to the parameter's bounds; the
    sentence. A parameter the provider does not read is saved all the
    same — one model is served by more than one provider — and the
    sentence says so. Raises Refused without a model, for an unknown
    provider or parameter, a value it cannot read, and one out of
    bounds."""
    client = _model_client(app, provider, model)
    parameter = PARAMETERS.get(name)
    if parameter is None:
        raise _unsupported(app, provider, model, name)
    typed = _typed(parameter, value)
    low, high = _bounds(client, name, parameter)
    if isinstance(typed, int | float) and (low is not None or high is not None):
        below = low is not None and typed < low
        above = high is not None and typed > high
        if below or above:
            if high is None:
                bounds = f"at least {low}"
            elif low is None:
                bounds = f"at most {high}"
            else:
                bounds = f"between {low} and {high}"
            raise Invalid(f"{name} must be {bounds}.")
    app._write_model_settings(model, {name: typed})
    said = f"{name} = {parameter_text(typed)}."
    if name not in client.supported_params_of(model):
        said += f" Not read by {provider}: kept for the model, sent where a provider reads it."
    return said


def delete_parameter(app: App, provider: str, model: str, name: str) -> str:
    """A parameter unset for a model: the engine's own default applies
    again; the sentence. Unsetting one that is not set says so and
    changes nothing. Raises Refused without a model, and for an unknown
    provider or parameter."""
    _model_client(app, provider, model)
    if name not in PARAMETERS:
        raise _unsupported(app, provider, model, name)
    if name not in app._model_settings(model).params:
        return f"Parameter {name} is not set."
    app._write_model_settings(model, {}, drop=(name,))
    return f"Parameter {name} unset: the engine's own default applies."


def think_word(level: str) -> str:
    """A level as a sentence says it: a budget as "20 token budget", a
    word as itself."""
    return f"{level} token budget" if reasoning.budget_of(level) is not None else level


def levels_text(levels: tuple[str, ...], budget: bool) -> str:
    """The levels a model takes as one sentence's list."""
    tail = ", or a number of tokens" if budget else ""
    return ", ".join(levels) + tail


def _typed(parameter: Parameter, value: object) -> object:
    """A value as the parameter's kind holds it: text read as a reader
    types it (the stop strings as `parse_stops` reads them), a value of
    the kind taken as it is. Raises Invalid for anything else."""
    kind = parameter.kind
    if isinstance(value, str):
        try:
            return parse_stops(value) if kind is list else kind(value)
        except ValueError:
            raise Invalid(f"Could not parse {value!r} as {kind.__name__}.") from None
    if kind is list:
        if isinstance(value, list) and value and all(isinstance(s, str) and s for s in value):
            return list(value)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        return kind(value)
    raise Invalid(f"Could not parse {value!r} as {kind.__name__}.")


def _client_of(app: App, provider: str) -> OpenAIClient | None:
    """The configured provider's client; None for "" (no model
    selected). Raises NotFound for a name that is not configured."""
    if not provider:
        return None
    client = app._providers.get(provider)
    if client is None:
        raise NotFound(f"Unknown provider {provider!r}.")
    return client


def _model_client(app: App, provider: str, model: str) -> OpenAIClient:
    """The client a model's setting is written for: refused without a
    model, NotFound for a provider that is not configured."""
    client = _client_of(app, provider)
    if client is None or not model:
        raise Conflict(NO_MODEL_HINT)
    return client


def _choices(caps: ModelCapabilities | None) -> tuple[tuple[str, ...], bool]:
    """What the model takes, as its provider describes it — the levels,
    and whether a budget is taken: every rung, and no budget, where it
    cannot say."""
    if caps is None or (caps.reasoning_efforts is None and caps.reasoning_switch is None):
        return THINK_MENU, False
    levels = [THINK_UNSET]
    if caps.reasoning_efforts:
        levels += [rung for rung in EFFORT_LEVELS if rung in caps.reasoning_efforts]
    elif caps.reasoning_switch:
        levels += SWITCH_LEVELS
    return tuple(levels), bool(caps.reasoning_budget)


def _bounds(
    client: OpenAIClient | None, name: str, parameter: Parameter
) -> tuple[float | None, float | None]:
    """(low, high) a value is held to: the bounds the provider states for
    itself (`ProviderCapabilities.bounds` — a local engine takes any
    temperature), or the vocabulary's (`PARAMETERS`, the catalogs')."""
    if client is not None and name in client.capabilities.bounds:
        return client.capabilities.bounds[name]
    return parameter.low, parameter.high


def _unsupported(app: App, provider: str, model: str, name: str) -> NotFound:
    """The refusal for a name outside the vocabulary, naming what reaches
    the model — the list the completion menu shows."""
    held = get_model_settings(app, provider, model)
    reaching = ", ".join(p.name for p in held.parameters if p.supported)
    return NotFound(f"Unsupported parameter {name!r}. Supported: {reaching}.")


# ---------- a model's capabilities, in words ----------

# How the Capabilities row spells a field where the dataclass's own name
# is not the reader's word; the rest read as their name with the
# underscore dropped.
_CAPABILITY_REWORDING = {"structured_output": "json output"}


def reasoning_words(caps: ModelCapabilities | None) -> str:
    """How the model's thinking is set, as the provider says it: the
    rungs it grades in the wire's order, or "on / off" for a switch,
    and "token budget" where one holds beside; "not supported" where
    nothing reaches it, "unknown" where the provider could not say.
    The info report's row, and the picker's, on both frontends."""
    if caps is None or (caps.reasoning_efforts is None and caps.reasoning_switch is None):
        return "unknown"
    ways = []
    if caps.reasoning_efforts:
        ways.append(
            ", ".join(rung for rung in reasoning.EFFORT_LEVELS if rung in caps.reasoning_efforts)
        )
    elif caps.reasoning_switch:
        ways.append("on / off")
    if caps.reasoning_budget:
        ways.append("token budget")
    return " / ".join(ways) if ways else "not supported"


def capability_words(caps: ModelCapabilities | None) -> str:
    """The capabilities the model has, in the dataclass's order and the
    reader's words — the thinking left out, having a row of its own;
    "none" when the provider knows of none, "unknown" when it could not
    say. The info report's row, and the picker's, on both frontends."""
    if caps is None:
        return "unknown"
    facts = [
        (field.name, getattr(caps, field.name))
        for field in fields(caps)
        if not field.name.startswith(("reasoning", "supported"))
    ]
    named = ", ".join(
        _CAPABILITY_REWORDING.get(name, name.replace("_", " ")) for name, fact in facts if fact
    )
    if named:
        return named
    return "none" if any(fact is not None for _, fact in facts) else "unknown"


# ---------- the model in use ----------


@dataclass(frozen=True)
class ModelFacts:
    """The model in use as the backend reads it, per call: the model
    (None while none is selected), its client, its row as last listed or
    read (nothing asked of an engine), whether it sees pictures and has
    the raw text wire (its engine said so — unknown reads as no, the
    capability's own rule), the text template the text wire renders the
    story in (`templates.resolve`: its models.toml line, else the
    engine's word, else its name), and whether what is typed leaves
    this machine."""

    model: ModelRef | None
    client: OpenAIClient | None
    row: ModelInfo | None
    vision: bool
    text_completion: bool
    text_template: templates.TextTemplate | None
    on_cloud: bool

    @property
    def can_complete_text(self) -> bool:
        """Whether the novel is offered on the model: its engine serves
        the text wire, and the template in force is `plain` — a base or
        story-trained model, named so by hand in models.toml. An instruct
        model has a template too, but fights a bare text, so the novel
        is kept from it."""
        template = self.text_template
        return (
            self.text_completion and template is not None and template.name == templates.PLAIN.name
        )

    def completion_type(self, layout: Layout) -> CompletionType:
        """The wire a story read in `layout` goes over on the model
        (`Layout.completion_type`)."""
        return layout.completion_type(self.can_complete_text)

    def can_attach(self, layout: Layout) -> bool:
        """Whether a picture may ride the next request of a story read in
        `layout` (`can_attach`)."""
        return can_attach(vision=self.vision, completion_type=self.completion_type(layout))


def can_attach(*, vision: bool, completion_type: CompletionType) -> bool:
    """Whether a picture may ride a request: the model sees, and the wire
    has room for one — the text wire is one text, which has none. What
    shows the attach affordances in both frontends; a picture is refused
    regardless."""
    return vision and completion_type is CompletionType.CHAT


def model_facts(app: App) -> ModelFacts:
    """The model in use and what it can do, off what is cached — nothing
    asked of an engine."""
    return app._model_facts()


def extraction_pin(app: App) -> ModelRef | None:
    """The model pinned for lore extraction; None while extraction
    follows the model in use. Safe from any thread."""
    return app._extraction_pin()


def extraction_model(app: App) -> ModelRef | None:
    """The model lore extraction asks: the one pinned for it, else the
    model in use."""
    return app._extraction_model()


def listed_name(app: App, model: ModelRef) -> str:
    """A model's name as its provider lists it — where a picker rows it.
    Ollama lists a bare name under its ":latest" tag, and a name typed
    without the tag must still be found; the remembered spelling where
    the provider has not listed the model."""
    client = app._client(model)
    found = client.models.cached(model.name) if client is not None else None
    return found.name if found is not None else model.name


# ---------- a provider's models ----------


def template_of(app: App, model: ModelInfo) -> str | None:
    """The text template a listed model would render with
    (`templates.resolve`): its models.toml line, else the engine's word,
    else its name; None where none is known."""
    found = templates.resolve(
        app._model_settings(model.name).text_template, model.text_template, model.name
    )
    return found.name if found is not None else None


def list_models(app: App, provider: str) -> list[ModelInfo]:
    """A provider's models as its server lists them right now — the
    picker's read-back after a load or unload, asked of that ONE provider
    with the listing's own patience (a server that just loaded a model is
    the slowest it ever is). Raises Refused when it cannot be reached: a
    refresh that failed quietly would leave the panel claiming the
    opposite of what just happened."""
    client = app._providers.get(provider)
    if client is None:
        raise NotFound(f"Unknown provider {provider!r}.")
    try:
        return list(client.models.list())
    except ProviderError as e:
        raise Unavailable(str(e)) from e


def get_model(app: App, provider: str, name: str) -> ModelInfo:
    """One model as its provider lists it right now. Raises NotFound for
    one it does not list, Refused where it cannot be reached."""
    found = next((row for row in list_models(app, provider) if row.name == name), None)
    if found is None:
        raise NotFound(f"{provider} does not list {name}.")
    return found


def load_model(app: App, provider: str, model: str) -> str:
    """Load a model on a provider that manages its models; blocks until
    the server answers. Every failure raises Refused with the curated
    sentence (not managed, the provider unreachable, the server's own
    error text) — no transport exception type ever crosses the
    boundary. Answers the sentence."""
    models = _managed(app, provider).models
    # The app's idle hook is ticked while the engine works: the web's
    # one thread answers its reads meanwhile, and a page that left cuts
    # the wait. A failure is the provider package's own sentence, which
    # names the provider.
    try:
        models.load(model, on_idle=app._on_idle)
    except ProviderError as e:
        raise Unavailable(str(e)) from e
    return f"Loaded {model}."


def unload_model(app: App, provider: str, model: str) -> str:
    """Unload a model, as `load_model` loads one."""
    models = _managed(app, provider).models
    try:
        models.unload(model, on_idle=app._on_idle)
    except ProviderError as e:
        raise Unavailable(str(e)) from e
    return f"Unloaded {model}."


def _managed(app: App, provider: str) -> OpenAIClient:
    client = app._providers.get(provider)
    if client is None:
        raise NotFound(f"Unknown provider {provider!r}.")
    if not client.capabilities.model_management:
        raise Conflict(f"{provider} cannot load or unload models.")
    return client
