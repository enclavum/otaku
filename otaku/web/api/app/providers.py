"""The providers, as the picker asks for them: the listing — a slice of
it asked at a time, the picker's two phases — and a provider's own
section and its fields saved. A provider's models and a model's own
settings are the models twin's."""

from typing import Any

from otaku.backend.api import UNCHANGED, Api
from otaku.web.api._request import Ask, Route, changed, plain


def _list(api: Api, ask: Ask) -> Any:
    """Every provider in the panel's order, the slice `scope` names asked
    for its models — "" every one, "local" all but the hosted catalogs,
    "cloud" the catalogs and the generic provider, a provider's name
    that one alone (a Test connection) — and `?probe=false` asking
    nobody: the vocabulary, and the models in use as last listed. An api
    key's VALUE never crosses; only where it comes from does."""
    return plain(
        api.providers.list(scope=ask.query.get("scope", ""), probe=ask.query_flag("probe", True))
    )


def _update(api: Api, ask: Ask) -> dict[str, Any]:
    """A provider's url or api key, whichever the body names. The key's
    value goes IN here and never comes back out through any read. An
    EMPTY value forgets the field, file and app both."""
    return changed(
        api.providers.update(
            ask.params["provider"],
            url=ask.need("url") if "url" in ask.body else UNCHANGED,
            api_key=ask.need("api_key") if "api_key" in ask.body else UNCHANGED,
        )
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/providers"): _list,
    ("GET", "/api/providers/{provider}"): lambda api, ask: plain(
        api.providers.get(ask.params["provider"])
    ),
    ("PATCH", "/api/providers/{provider}"): _update,
}
