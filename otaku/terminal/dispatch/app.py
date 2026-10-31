"""The app's commands carried out — the twin of `backend.app.core`: `/model`,
bare the picker, `PROVIDER/MODEL` the switch, its confirmation's model
in bold."""

from otaku.terminal.api import app as api_app
from otaku.terminal.chat.chat import Chat
from otaku.terminal.screens import models as screen_models
from otaku.terminal.tty import BOLD, RESET


def model(chat: Chat, raw: str) -> None:
    """Bare: the picker screen (which executes a confirmed switch and
    returns the notice); with an argument: `model` (the split rule
    lives there)."""
    if raw.strip():
        chat.say(_bold_switch(api_app.model(chat.api, raw).notice))
        return
    current = api_app.specs(chat.api)[0]
    notice = screen_models.pick(chat.api, initial_spec=current or None)
    if notice:
        chat.say(_bold_switch(notice))


def _bold_switch(notice: str) -> str:
    """The switch confirmation with its model spec in bold — styling is
    the terminal's, so it is laid over the backend's sentence here, at
    the two doors that show one."""
    head = "Switched to "
    if notice.startswith(head) and notice.endswith("."):
        return f"{head}{BOLD}{notice[len(head) : -1]}{RESET}."
    return notice
