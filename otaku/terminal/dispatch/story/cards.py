"""A character card brought into the story, typed, carried out — the
twin of `api.story.cards` (`/card FILE [NAME]`): the file resolved on
this side, vetted, the persona asked, then imported — the exchange
echoed like a played line."""

import sys
from collections.abc import Callable

from otaku.terminal.api import Refused
from otaku.terminal.chat.chat import Chat
from otaku.terminal.dispatch import _files
from otaku.terminal.tty import DIM, RESET, error_line
from otaku.terminal.tty.render import message


def _card(chat: Chat, raw: str) -> None:
    """The terminal's path affordance, then the three-step import:
    resolve `FILE [NAME]` on THIS side (the longest leading token run
    naming a real file is the file, a leading `@` stripped, the rest
    renames), read the bytes, then check → ask the persona (tty only;
    the remembered default) → create. The exchange echoes like a played
    submission: the typed line (and the ask) erased and re-echoed as the
    grey block, the report and the greeting riding it — so /undo takes
    the screen back whole."""
    argument = raw.strip().removeprefix("@")
    if not argument:
        chat.say("Usage: /card FILE [NAME]")
        chat.ledger.invalidate()
        return
    path, rename = _files.file_and_name(argument)
    try:
        data = path.read_bytes()
    except OSError as e:
        chat.say(error_line(f"Could not read {path}: {e}"))
        chat.ledger.invalidate()
        return
    try:
        # The story the card lands in, made for it where there is none
        # yet and taken back with a card refused or an ask abandoned.
        with chat.writing() as story_id:
            check = chat.api.stories.cards.create(
                story_id, data=data, file_name=path.name, rename=rename, validate_only=True
            )
            persona = _ask_persona(chat, check.persona or "you")
            # The row records the argument as typed — a path is what the
            # terminal's user named the file, and /last must read it back.
            landed = chat.api.stories.cards.create(
                story_id,
                data=data,
                file_name=path.name,
                rename=rename,
                persona=persona,
                line=f"/card {argument}",
                expected_head=chat.head,
            ).value
    except Refused as e:
        chat.say(str(e))
        chat.ledger.invalidate()
        return
    chat.mark_drawn()
    # The import plays like a submission.
    chat.ledger.echo_block(message(landed.line.body, "user"))
    out = chat.ledger.reply
    print(f"{DIM}[ {landed.report} ]{RESET}", file=out)
    if landed.greeting is not None:
        print(file=out)
        print(
            message(
                landed.greeting.body,
                "assistant",
                segments=landed.greeting.segments,
                shown=chat.shown_tools,
            ),
            file=out,
        )


def _ask_persona(chat: Chat, default: str) -> str:
    """Who the player is in this story — `{{user}}` in every card text.
    Enter takes the default; a non-tty stdin never blocks on the ask.
    The echoed ask joins the typed rows (the lead blank included), so
    the block echo replaces it along with the typed line."""
    if not sys.stdin.isatty():
        return default
    lead = chat.ledger.said()
    if lead:
        sys.stdout.write(lead)
        chat.ledger.typed(lead)
    ask = f"User name for the card [{default}]: "
    answer = input(ask).strip()
    chat.ledger.typed(ask + answer + "\n")
    return answer or default


# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/card": _card,
}
