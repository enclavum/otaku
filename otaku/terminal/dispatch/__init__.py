"""A typed command carried out. The routing: the line split into its
command and its argument (`find`, `raw_argument`), the sentence for one
no row answers (`unknown_notice`), and every handler merged into one
table (`HANDLERS` — a token two modules answer is a fault at import).
The handlers are laid out as the door is (`terminal.api`), a module for
each resource that has commands (`app`, `story`) — what the terminal
does with a command: the door's call, then what is drawn, where it
lands, which screen opens — and the four the terminal answers itself
are here, as the web's server holds its own paths: the /help page, the
app served to a browser (/web), the screen cleared, the session ended.
A handler takes the chat and the argument as typed, and answers the
sentence to say — or None, where it said its own."""

from collections.abc import Callable, Mapping

from otaku import web as web_frontend
from otaku.terminal import help as help_page
from otaku.terminal.api import Refused
from otaku.terminal.chat.chat import Chat
from otaku.terminal.commands import COMMANDS, SHORTCUTS, CommandSpec, is_command
from otaku.terminal.dispatch import app
from otaku.terminal.dispatch.app import integrations, models, reports
from otaku.terminal.dispatch.app import settings as app_settings
from otaku.terminal.dispatch.story import cards, context, lore, messages, stories, transfer
from otaku.terminal.dispatch.story import settings as story_settings
from otaku.terminal.tty import error_line
from otaku.terminal.tty.render import command_tokens
from otaku.terminal.tty.typography import highlight_commands

# One command's handler: the chat and the argument as typed in; the
# sentence to say back, or None where it said its own.
Handler = Callable[[Chat, str], str | None]


# The commands that manage the screen ledger themselves (they take an
# exchange back, or echo their own block). Every other command's output
# lands below the last exchange and invalidates on its first say — the
# dispatch window watches for the write rather than assuming one, so a
# picker left without a choice costs the exchanges above it nothing.
_PLAYING = {"/undo", "/regen", "/card"}


def dispatch(chat: Chat, line: str) -> bool:
    """True when the line was a command (handled); False when it should
    play as story. Unknown /words are reported here; the argument text
    passes raw — what it means is its handler's."""
    if not is_command(line):
        return False
    spec = find(line)
    token = spec.token if spec else line.split()[0]
    with chat.ledger.command_output(manages_screen=token in _PLAYING):
        try:
            if spec is None:
                chat.say(unknown_notice(line))
            else:
                said = HANDLERS[spec.token](chat, raw_argument(line, spec.token))
                if said is not None:
                    chat.say(said)
        except Refused as e:
            chat.say(str(e))
    return True


def find(token: str) -> CommandSpec | None:
    """The spec a typed line's head names — the longest match wins, so
    "/set think medium" finds the "/set think" row; None for an unknown
    command (bare "/set" included: the family's usage line is `unknown_notice`'s
    to compose, below). Inliner rows (`… /ooc`) never match — they close
    a line, they do not open one."""
    words = token.split()
    for depth in (2, 1):
        head = " ".join(words[:depth])
        for spec in COMMANDS:
            if spec.token == head:
                return spec
    return None


def raw_argument(line: str, token: str) -> str:
    """Everything after the spec's token, verbatim from the first
    non-space character — free-text arguments keep the user's exact
    spacing (split-and-rejoin would collapse it), and a fixed-width
    slice disagrees the moment a line is typed untidily: "/set  think
    medium" would hand an operation "k  medium". The ONE splitting rule
    for a typed line."""
    rest = line
    for _ in token.split():
        _, _, rest = rest.lstrip().partition(" ")
    return rest.lstrip()


def unknown_notice(line: str) -> str:
    """The sentence for a typed line no row answers — total over ANY
    string. The /set family's is a usage line composed FROM the table, so
    it and the commands can never disagree."""
    word, _, _ = line.strip().partition(" ")
    if word == "/set":
        forms = " | ".join(
            f"{spec.token} {spec.args}".strip()
            for spec in COMMANDS
            if spec.token.startswith("/set ")
        )
        return f"Usage: {forms}"
    return f"Unknown command: {word}. Type /help."


# ---------- the commands the terminal answers itself ----------


def _help(chat: Chat, raw: str) -> None:
    # The captions go over as DATA, the way the completion menu gets
    # them — the key table lives here and the page only reads it.
    # Colored at print time, never at build: the page's column math runs
    # on plain text (escapes would count into the padding), and the theme
    # is only settled once the launch has asked the terminal.
    captions = {token: shortcut.caption for token, shortcut in SHORTCUTS.items()}
    chat.say(highlight_commands(help_page.text(captions), command_tokens()))


def _web(chat: Chat, raw: str) -> None:
    """Serve this app to a browser and stand here until the reader
    stops it, then carry on at the prompt below. One app throughout:
    the other frontend is handed the open one rather than opening its
    own, and closing it stays cli's either way.

    This is the one place the terminal reaches the other frontend, and
    the arrow points only this way — a page has nowhere to send anybody
    back to."""
    # The other frontend prints into this terminal itself, which is
    # printing PAST `say` — so the say protocol is announced here
    # instead: the blank under the typed line, the stack invalidated
    # (what it prints lands past every row the ledger was counting), and
    # the gap before the next prompt earned. Without this the banner
    # opens flush against `/web` and the loop prints no gap at all.
    print(chat.ledger.said(), end="")
    try:
        web_frontend.run_shared(chat.app)
    except web_frontend.ServeError as e:
        # The address is configuration, and a reader who cannot have the
        # browser keeps the chat: a sentence, not a crash. The
        # reason is web's own words, under this medium's.
        chat.say(error_line(f"The web interface could not start: {e}"))


def _clear(chat: Chat, raw: str) -> None:
    """Wipe the screen; the story is untouched and /last brings the
    scene back. The next prompt opens at the top."""
    chat.ledger.clear()


def _bye(chat: Chat, raw: str) -> None:
    chat.quit = True


# ---------- the rows ----------


def _merged(*tables: Mapping[str, Handler]) -> dict[str, Handler]:
    """The command modules' rows as one table; a token two modules claim
    is a bug, caught here rather than by whichever import ran last."""
    out: dict[str, Handler] = {}
    for table in tables:
        twice = set(out) & set(table)
        if twice:
            raise AssertionError(f"a command in two modules: {sorted(twice)}")
        out.update(table)
    return out


# Every command the terminal answers, by its token.
HANDLERS: dict[str, Handler] = _merged(
    {"/help": _help, "/web": _web, "/clear": _clear, "/bye": _bye},
    app.HANDLERS,
    models.HANDLERS,
    app_settings.HANDLERS,
    reports.HANDLERS,
    integrations.HANDLERS,
    stories.HANDLERS,
    story_settings.HANDLERS,
    messages.HANDLERS,
    lore.HANDLERS,
    cards.HANDLERS,
    context.HANDLERS,
    transfer.HANDLERS,
)
