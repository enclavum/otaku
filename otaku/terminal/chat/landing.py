"""A story landed in — from a screen, or caught up with before a prompt —
echoed under the break rule: its line and its last turns, handed to the
ledger so /undo and /regen can take them back."""

from otaku.terminal.chat.chat import RESUME_TURNS, Chat
from otaku.terminal.tty.render import last_turns

# What the terminal says when the story it plays was deleted under it.
_STORY_DELETED = "The story was deleted — continuing in a new one."


def land_from_screen(chat: Chat, landed: tuple[int, str] | None) -> None:
    """Play the story a screen landed in, echoed under the break rule —
    or, when the reader only left, carry on with the one played, read
    again: the screen may have changed its premise or its settings."""
    if landed is None:
        chat.refresh()
        return
    story_id, line = landed
    chat.land(story_id)
    echo_landing(chat, line)


def catch_up(chat: Chat) -> None:
    """Before each prompt: the story as it stands, echoed under the break
    rule where it moved on elsewhere since this terminal drew it, or said
    gone where it was deleted under it."""
    played = chat.story_id
    if chat.catch_up():
        assert chat.story is not None
        echo_landing(chat, chat.story.landed_line("Caught up"))
    elif played is not None and chat.story_id is None:
        chat.say(_STORY_DELETED)


def echo_landing(chat: Chat, line: str) -> None:
    """Echo a landing under the break rule: its line and the story's
    last turns, handed to the ledger."""
    chat.ledger.rule()
    turns = last_turns(
        chat.messages(),
        RESUME_TURNS,
        shown=chat.shown_tools,
        is_story_mode=chat.is_story_mode,
        posed=chat.posed,
    )
    # A story with nothing played echoes no turns, and no blank for them.
    chat.say(f"{line}\n\n{turns}" if turns else line)
    if turns:
        chat.restore_tail(RESUME_TURNS)
