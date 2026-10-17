"""A story's messages, typed, carried out — the twin of
`api.story.messages`: the last turns shown again (`/last`), the way a
relaunch shows the scene."""

from collections.abc import Callable

from otaku.terminal.chat.chat import Chat
from otaku.terminal.tty.render import last_turns

# Turns /last shows when called bare — a turn being an exchange, the
# prompt and its reply (two message rows).
_LAST_TURNS_DEFAULT = 5


def last(chat: Chat, raw: str) -> None:
    """`/last [N]` — show the last N turns again (default 5), the way a
    relaunch shows the scene: a clean view after undos, regens, etc."""
    argument = raw.strip()
    if argument and not (argument.isdigit() and int(argument) > 0):
        chat.say("Usage: /last [N]")
        return
    count = int(argument) if argument else _LAST_TURNS_DEFAULT
    chain = chat.messages()
    if not chain:
        chat.say("No turns yet.")
        return
    rows = count * 2  # a turn is an exchange: the prompt and its reply
    chat.ledger.rule()
    # What the echo actually holds, never what was asked for: a short
    # story shows everything it has, and the report must not over-claim.
    shown = (len(chain[-rows:]) + 1) // 2
    report = f"The last {shown} turns of this story:"
    turns = last_turns(
        chain, rows, shown=chat.shown_tools, is_story_mode=chat.is_story_mode, posed=chat.posed
    )
    chat.say(f"{report}\n\n{turns}")
    chat.restore_tail(rows, above=report)


# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/last": last,
}
