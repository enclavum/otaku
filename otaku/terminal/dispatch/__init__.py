"""A typed command carried out. The routing: the line split into its
command and its argument (`find`, `raw_argument`), the sentence for one
no row answers (`unknown_notice`), and every command's handler in one
table (`HANDLERS`). A resource whose commands do more than call the
door has a module of handlers, flat as the door is (`terminal.api`),
named after it, `app` the app's own — what the terminal does with a
command: the door's call, then what is drawn, where it lands, which
screen opens. A command that is one call is its row's lambda, and the
three the terminal answers itself are here, as the web's server holds
its own paths: the /help page, the screen cleared, the session ended. A
handler takes the chat and the argument as typed, and answers the
sentence to say — or None, where it said its own."""

from collections.abc import Callable

from otaku.terminal import help as help_page
from otaku.terminal.api import Refused
from otaku.terminal.api import lore as api_lore
from otaku.terminal.api import models as api_models
from otaku.terminal.api import reports as api_reports
from otaku.terminal.api import settings as api_settings
from otaku.terminal.api import stories as api_stories
from otaku.terminal.chat.chat import Chat
from otaku.terminal.commands import COMMANDS, SHORTCUTS, CommandSpec, is_command
from otaku.terminal.dispatch import (
    app,
    cards,
    context,
    lore,
    messages,
    settings,
    stories,
    transfer,
)
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


def _clear(chat: Chat, raw: str) -> None:
    """Wipe the screen; the story is untouched and /last brings the
    scene back. The next prompt opens at the top."""
    chat.ledger.clear()


def _bye(chat: Chat, raw: str) -> None:
    chat.quit = True


# ---------- the rows ----------


# Every command the terminal answers, by its token, under the resource it
# reaches; a command that is one call of the door is a lambda, the door's
# answer (a report's `text()`) the sentence.
HANDLERS: dict[str, Handler] = {
    # the terminal's own
    "/help": _help,
    "/clear": _clear,
    "/bye": _bye,
    # the app itself (`backend.app.core`)
    "/model": app.model,
    # the installation's settings (`backend.app.settings`)
    "/set verbose": lambda chat, raw: api_settings.set_verbose(chat.api, raw),
    "/set autocorrect": lambda chat, raw: api_settings.set_autocorrect(chat.api, raw),
    "/set notification": lambda chat, raw: api_settings.set_notification(chat.api, raw),
    "/set max_context": lambda chat, raw: api_settings.set_max_context(chat.api, raw),
    "/set serve": settings.set_serve,
    # the model in use's own settings (`backend.app.models`)
    "/set think": lambda chat, raw: api_models.set_think(chat.api, raw),
    "/set parameter": lambda chat, raw: api_models.set_parameter(chat.api, raw),
    # a support request (`backend.app.integrations`): the whole line, an
    # address to answer at included
    "/support": lambda chat, raw: chat.api.integrations.support_requests.create(body=raw).notice,
    # the reports (`backend.app.reports`)
    "/usage": lambda chat, raw: api_reports.usage(chat.api, chat.story_id, raw).text(),
    "/info": lambda chat, raw: chat.api.info.get(story_id=chat.story_id).text(),
    "/balance": lambda chat, raw: chat.api.balance.get().text(),
    # the stories (`backend.story.stories`)
    "/stories": stories.stories,
    "/new": stories.new,
    "/fork": stories.fork,
    "/title": lambda chat, raw: api_stories.title(chat.api, chat.story_id, raw, make=chat.writing),
    "/system": stories.system,
    "/regen": stories.regen,
    "/undo": stories.undo,
    # a story's settings (`backend.story.settings`)
    "/set mode": settings.set_mode,
    # its messages (`backend.story.messages`)
    "/last": messages.last,
    # its lore (`backend.story.lore`)
    "/lore": lore.lore,
    "/cast": lore.cast,
    "/extract": lore.extract,
    "/merge": lambda chat, raw: api_lore.merge(chat.api, chat.played(), raw),
    # a character card brought in (`backend.story.cards`)
    "/card": cards.card,
    # its next request (`backend.story.context`)
    "/context": context.context,
    # the story in and out as a document (`backend.story.transfer`)
    "/import": transfer.import_,
    "/export": transfer.export,
}
