"""Meta commands: /help and /bye — about the app, not the story."""

# The module reference, not the names: the package __init__ (which owns
# HELP_TEXT) imports THIS module for the dispatch table, so the text is
# read at call time, after the package finished initializing.
from otaku.chat import commands
from otaku.chat.session import Session
from otaku.store import Store


def cmd_bye(session: Session, store: Store, args: list[str]) -> None:
    session.should_quit = True


def cmd_help(session: Session, store: Store, args: list[str]) -> None:
    print(commands.HELP_TEXT)
