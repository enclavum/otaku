"""What `app.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.providers import ModelCapabilities
from otaku.settings.state import ModelRole


class AppInfo(Schema):
    """otaku as it runs: its version, what the launch had to say, the
    model in use (None for none), the model each role asks — a role
    with none absent: extraction with neither its own nor a model in
    use, dictation without one saved — and whether what is typed or said leaves
    this machine: the model in use, idle extraction's or dictation's on
    a hosted catalog."""

    version: str
    launch_facts: LaunchFacts
    model: ModelInUse | None
    models: dict[ModelRole, ListedModel]
    on_cloud: bool


class LaunchFacts(Schema):
    """What the launch that opened the app had to say: its notices, the
    hint over the resumed scene, and the newer version the last update
    check found announced ("" for none)."""

    notices: tuple[str, ...]
    hint: str
    newer_version: str


class ListedModel(Schema):
    """A model as the app names it: its provider, its name as state.toml
    names it — what its own settings are saved under — and its name as
    its provider lists it (`listed_name`, where a picker rows it)."""

    provider: str
    name: str
    listed_name: str


class ModelInUse(ListedModel):
    """The model in use, named, with its facts: its context window as
    last listed (None where nothing is known), what it can do as its
    provider states it (None where it says nothing of the model — a flag
    unknown reads as no), whether the novel is offered on it — its
    engine completes raw text and the template in force is `plain` — and
    whether a picture may ride the first line of a story not made yet,
    read in the layout a story starts in (`Story.can_attach` once there
    is one)."""

    context_window: int | None
    capabilities: ModelCapabilities | None
    can_complete_text: bool
    can_attach: bool


class Dictation(Schema):
    """What was said, written down."""

    text: str


class DictationCheck(Schema):
    """What a recording would be written down by: the model saved for
    dictation, and the recordings its engine takes, by extension ("wav",
    "webm", "m4a", …) — a WAV always among them."""

    model: ListedModel
    audio_formats: tuple[str, ...]
