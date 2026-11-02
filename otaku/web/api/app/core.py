"""The app itself, as the page reads and moves it — the twin of
`backend.app.core`: the running otaku, its version, what its launch had
to say, the model in use and what it can do, the model each role asks,
and what is said, written down by the model saved for dictation. What
the background worker is doing (`/api/app/status`) is the server's own
to answer: it never takes the app's thread."""

import base64
from typing import Any

from otaku.backend.api import Api, ModelRef, ModelRole
from otaku.web.api._request import Ask, Route, changed, plain, unqueued


def _update(api: Api, ask: Ask) -> dict[str, Any]:
    """The models the body names (`models`), keyed by role, each as an
    object of its provider and its name — `writing` the model played
    with, a role's null unpinning it. A field of the wrong type, or a
    role otaku has not, is a malformed request; a body naming none is
    the backend's to refuse."""
    body = ask.body
    fields: dict[str, Any] = {}
    if "models" in body:
        pinned = body["models"]
        if not isinstance(pinned, dict):
            raise TypeError("models is not an object of roles")
        fields["models"] = {
            ModelRole(role): None if model is None else _model_ref(model)
            for role, model in pinned.items()
        }
    return changed(api.app.update(**fields))


@unqueued
def _dictate(api: Api, ask: Ask) -> Any:
    """A recording written down — its bytes as base64 (`audio`) and its
    extension (`audio_format`) — or, `?validate_only=true`, what would
    write it down and the recordings it takes. Off the app's thread: a
    model writing down a minute of speech holds nothing else up."""
    if ask.query_flag("validate_only", False):
        return plain(api.app.dictation.create(validate_only=True))
    audio = base64.b64decode(ask.need("audio"), validate=True)
    return changed(api.app.dictation.create(audio=audio, audio_format=ask.need("audio_format")))


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
    ("POST", "/api/app/dictation"): _dictate,
}
