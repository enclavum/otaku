"""What `app.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.providers import ModelCapabilities


class AppInfo(Schema):
    """otaku as it runs: its version, what the launch had to say, the
    model in use (None for none), the model lore extraction asks (None
    with neither a pin nor a model in use), and whether what is typed
    leaves this machine — the model in use, or idle extraction's, on a
    hosted catalog."""

    version: str
    launch_facts: LaunchFacts
    model: ModelInUse | None
    extraction_model: ExtractionModel | None
    on_cloud: bool


class LaunchFacts(Schema):
    """What the launch that opened the app had to say: its notices, the
    hint over the resumed scene, and the newer version the last update
    check found announced ("" for none)."""

    notices: tuple[str, ...]
    hint: str
    newer_version: str


class ModelInUse(Schema):
    """The model in use: its provider and its name as state.toml names
    it — what its own settings are saved under — and as its provider
    lists it (`listed_name`, where a picker rows it), its context window
    as last listed (None where nothing is known), what it can do as its
    provider states it (None where it says nothing of the model — a flag
    unknown reads as no), whether the novel is offered on it — its
    engine completes raw text and the template in force is `plain` — and
    whether a picture may ride the first line of a story not made yet,
    read in the layout a story starts in (`Story.can_attach` once there
    is one)."""

    provider: str
    name: str
    listed_name: str
    context_window: int | None
    capabilities: ModelCapabilities | None
    can_complete_text: bool
    can_attach: bool


class ExtractionModel(Schema):
    """The model lore extraction asks: its provider and name, its name as
    its provider lists it (`listed_name`, where a picker rows it), and
    whether it is pinned for extraction — else it is the model in use,
    followed as that changes."""

    provider: str
    name: str
    listed_name: str
    pinned: bool
