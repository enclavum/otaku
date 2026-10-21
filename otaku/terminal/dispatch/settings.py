"""A story's settings, typed, carried out — the twin of
`backend.story.settings`: the look a story is read in (`/set mode`), the
terminal's own word for its two looks, written as the story's layout."""

from otaku.terminal.api import LAYOUT_SETTING, Layout
from otaku.terminal.chat.chat import Chat
from otaku.terminal.dispatch import messages

# The words /set mode takes and the layout each stores: the terminal draws
# a story as one text or as turns, so it names two of the four the story's
# layout setting holds (the page sets all four).
_MODES = {"story": Layout.CANVAS, "chat": Layout.MANUSCRIPT}


def set_mode(chat: Chat, raw: str) -> None:
    """`/set mode` — the terminal's own word for its two looks, written as
    the story's layout setting (`api.stories.LAYOUT_SETTING`), so it forks and
    dies with the story; "" reports it: story for a layout drawn as one
    text, chat otherwise. A switch that changed the look shows the last
    turns in the new one, as `/last` does."""
    value = raw.strip().lower()
    before = chat.layout.mode
    if value:
        chosen = _MODES.get(value)
        if chosen is None:
            chat.say("Usage: /set mode chat|story")
            return
        if chat.story_id is None:
            chat.draft_layout = chosen
        else:
            chat.api.stories.settings.update(chat.story_id, LAYOUT_SETTING, layout=chosen)
            chat.refresh()
    chat.say(f"Mode: {chat.layout.mode.value}")
    if chat.layout.mode is not before and chat.messages():
        messages.last(chat, "")
