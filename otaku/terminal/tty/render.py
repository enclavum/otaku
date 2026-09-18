"""How a turn looks — decided once, used by the prompt's live coloring,
the played block, the resume echo, and the story browser alike. A
request has its commands picked out, and the pictures it carried noted
after them in dim brackets; a reply is typeset the way it streamed —
one or the other, never both: they are counterparts, not layers. The
reply goes through the very typesetter it arrived on, run to the end
in one pass, so a turn echoed on resume is the same text that was on
screen when it played. The vocabulary comes from `backend.commands`, so
what counts as a command is never guessed from a slash.
"""

import io
import re

from otaku.backend import Message
from otaku.backend.commands import COMMANDS
from otaku.terminal.tty import DIM, user_block
from otaku.terminal.tty.typography import Streamer, highlight_commands

# A command as a spec's token writes it: a slash and a word, nothing
# else — rules out the `…` the inliner rows ride behind.
_COMMAND_WORD = re.compile(r"^/[a-z]+$")
# Intensity back to normal, colors untouched: a reset inside the played
# block would knock its band out mid-row.
_NORMAL = "\x1b[22m"


def message(text: str, role: str, *, pictures: int = 0) -> str:
    """One body styled for display: highlighted (user) or typeset
    (assistant) — and, for a turn that carried pictures, the dim
    bracketed note on the same line after the words, the stats line's
    family. Takes no settings — the user's colors reached the theme at
    launch, so a caller only has to say WHAT it is drawing. The one
    renderer for showing AND measuring, so the ledger can never disagree
    with an echo."""
    if role == "user":
        styled = highlight_commands(text, command_tokens())
        if pictures:
            note = f"{DIM}{pictures_note(pictures)}{_NORMAL}"
            styled = f"{styled} {note}" if styled else note
        return styled
    out = io.StringIO()
    streamer = Streamer(out)
    streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def pictures_note(count: int) -> str:
    """The note's plain text — `[ 1 picture ]` — for a caller that
    measures a row before styling it; "" for none."""
    if not count:
        return ""
    return f"[ {count} picture{'s' if count != 1 else ''} ]"


def turn(item: Message) -> str:
    """One turn exactly as the echoes print it: a user turn as the grey
    block, a model turn as it streamed — the trailing newline normalized
    away, the caller joining and terminating lines."""
    if item.role == "user":
        return user_block(message(item.body, "user", pictures=len(item.attachments)))
    return "\n".join(message(item.body, item.role).splitlines())


def last_turns(messages: list[Message], count: int) -> str:
    """The last `count` turns, echoed the way they played, one blank line
    between turns, bodies verbatim."""
    out: list[str] = []
    for item in messages[-count:]:
        out.append("")
        out.append(turn(item))
    return "\n".join(out).lstrip("\n")


def command_tokens() -> tuple[str, ...]:
    """Every slash word the app answers to, in table order — the commands
    typed at a line's start and the inliners typed inside one. What
    display reads to tell a command from prose: `/me` is one, `and/or`
    is not, so a highlighter never has to guess from the slash alone."""
    found = (word for spec in COMMANDS for word in spec.token.split())
    return tuple(dict.fromkeys(word for word in found if _COMMAND_WORD.match(word)))
