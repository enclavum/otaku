"""The settings, typed, carried out where a command does more than
call the door: the look a story is read in (`/set mode`, the twin of
`backend.story.settings`), the terminal's own word for its two looks,
written as the story's layout; and the page served beside the chat
(`/set serve`, the terminal's slice of `backend.app.settings`), the
setting written and the serving started or stopped with it."""

from otaku.terminal.api import LAYOUT_SETTING, Layout
from otaku.terminal.api import settings as api_settings
from otaku.terminal.chat.chat import Chat
from otaku.terminal.dispatch import messages
from otaku.terminal.tty import error_line

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


def set_serve(chat: Chat, raw: str) -> None:
    """`/set serve [on|off]` — the setting written, then the page served
    or stopped to match it; a page that cannot be served is said, the
    setting standing. Bare reports where it stands."""
    chat.say(api_settings.set_serve(chat.api, raw))
    if not raw.strip():
        return
    if chat.api.settings.terminal.get().serve:
        failure = chat.serve()
        if failure is not None:
            chat.say(error_line(failure))
    else:
        chat.stop_serving()
