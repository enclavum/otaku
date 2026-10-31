"""What `story.prompts.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema


class Prompt(Schema):
    """A prompt otaku sends: its name — the prompts.toml key it is kept
    under — and its text as it stands."""

    name: str
    text: str


class SharedReminder(Schema):
    """The shared reminder: its text, "" for none."""

    text: str
