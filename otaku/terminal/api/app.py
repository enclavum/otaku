"""The installation as the terminal reaches it — the twin of
`backend.app.core`: everything it answers, re-exported, and the models in
use as the terminal spells them, "provider/name" — read off the app
(`specs`) and typed back (`model`, `/model PROVIDER/MODEL`)."""

from otaku.backend.api import Api, Changed, Invalid, ModelRef
from otaku.backend.app.core.schemas import AppInfo as AppInfo
from otaku.backend.app.core.schemas import ExtractionModel as ExtractionModel
from otaku.backend.app.core.schemas import LaunchFacts as LaunchFacts
from otaku.backend.app.core.schemas import ModelInUse as ModelInUse


def model(api: Api, raw: str) -> Changed[AppInfo]:
    """`/model PROVIDER/MODEL`: the ONE split rule (the first slash —
    model names may carry more) and the refusal that lists the known
    providers, then the switch. A bare `/model` is the frontend's own
    (its picker)."""
    listing = api.providers.list(probe=False)
    known = sorted(provider.id for provider in listing.providers if provider.configured)
    head, _, rest = raw.strip().partition("/")
    if head not in known or not rest:
        names = ", ".join(known)
        raise Invalid(f"Use PROVIDER/MODEL (providers: {names}), or /model with no args to pick.")
    return api.app.update(model=ModelRef(head, rest))


def specs(api: Api) -> tuple[str, str]:
    """The model in use and the one pinned for lore extraction as a
    picker rows them — "provider/name", the name as the provider lists
    it, "" for none; extraction following the model in use is no pin, and
    carries no mark of its own."""
    facts = api.app.get()
    model, extraction = facts.model, facts.extraction_model
    in_use = f"{model.provider}/{model.listed_name}" if model is not None else ""
    pinned = (
        f"{extraction.provider}/{extraction.listed_name}"
        if extraction is not None and extraction.pinned
        else ""
    )
    return in_use, pinned
