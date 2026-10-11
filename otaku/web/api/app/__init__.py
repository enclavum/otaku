"""The installation, as the page reads and moves it — the twin of
`backend.api.app`, each module of this package the twin of the
interface's of its name. Here the running otaku: its version, what its
launch had to say, the model in use and what it can do, and the model
lore extraction asks. What the background worker is doing
(`/api/app/status`) is the server's own to answer: it never takes the
app's thread."""

from typing import Any

from otaku.backend.api import Api, ModelRef
from otaku.web.api._request import Ask, Route, changed, plain


def _update(api: Api, ask: Ask) -> dict[str, Any]:
    """The models the body names, each as an object of its provider and
    its name — the extraction model's null following the model in use
    again. A field of the wrong type is a malformed request; a body
    naming neither is the backend's to refuse."""
    body = ask.body
    fields: dict[str, Any] = {}
    if "model" in body:
        fields["model"] = _model_ref(body["model"])
    if "extraction_model" in body:
        pinned = body["extraction_model"]
        fields["extraction_model"] = None if pinned is None else _model_ref(pinned)
    return changed(api.app.update(**fields))


def _model_ref(value: object) -> ModelRef:
    """A model as the body names it: an object of its provider and its
    name, both texts."""
    if not isinstance(value, dict):
        raise TypeError("a model is not an object of provider and name")
    provider, name = value["provider"], value["name"]
    if not isinstance(provider, str) or not isinstance(name, str):
        raise TypeError("a model's provider and name are not texts")
    return ModelRef(provider, name)


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/app"): lambda api, ask: plain(api.app.get()),
    ("PATCH", "/api/app"): _update,
}
