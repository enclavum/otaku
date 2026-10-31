"""What `story.settings.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.backend.story.settings.services import StorySettingLayout
from otaku.store.schema import InjectionPosition, Layout

# The layout setting: the name a story stores it under, and the layout a
# story is read in until it says otherwise — a story not made yet's too.
LAYOUT_SETTING = StorySettingLayout.name


DEFAULT_LAYOUT = StorySettingLayout.default_layout


class StorySetting(Schema):
    """One setting of a story as a frontend draws it: the name the story
    stores it under and its label; the tool it switches on, by the tool's
    name (None for none); whether it is on; the text it injects as
    `/context` names it and the prompts.toml key of otaku's prompt it
    sends (None where it injects nothing, or the text is the reader's
    own); where it rides and the closed list a frontend offers (None and
    empty where it takes none — each position names itself, `.text`);
    and the story's own reminder, whether the notes are shown and the
    layout — each None where the setting has none."""

    name: str
    label: str
    tool: str | None
    enabled: bool
    injection_label: str | None
    prompt_name: str | None
    injection_position: InjectionPosition | None
    allowed_positions: tuple[InjectionPosition, ...]
    reminder_text: str | None
    display_notes: bool | None
    layout: Layout | None
