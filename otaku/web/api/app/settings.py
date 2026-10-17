"""The settings, as the page reads and moves them: the installation's
own — the story last landed in, the switches, the context limit."""

from typing import Any

from otaku.backend.api import Api
from otaku.backend.api.app.settings import WebSettings as WebSettings
from otaku.web.api._request import Ask, Route, changed, plain


def notifies(api: Api) -> bool:
    """Whether a landed reply calls the reader back (`notification`) —
    asked by the stream once a reply has run to its end."""
    return api.settings.get().notification


def _update(api: Api, ask: Ask) -> dict[str, Any]:
    """The settings the body names, each as JSON types it: the story last
    landed in as a story's id or null, a switch as a boolean, the context
    limit as a count (0 none, the model's own window). A field of the
    wrong type is a malformed request; a body naming none of them is the
    backend's to refuse."""
    body = ask.body
    fields: dict[str, Any] = {}
    if "last_story" in body:
        fields["last_story"] = None if body["last_story"] is None else ask.integer("last_story")
    for name in ("verbose", "autocorrect", "notification"):
        if name in body:
            fields[name] = ask.flag(name)
    if "context_limit" in body:
        fields["context_limit"] = ask.integer("context_limit")
    return changed(api.settings.update(**fields))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/settings"): lambda api, ask: plain(api.settings.get()),
    ("PATCH", "/api/settings"): _update,
}
