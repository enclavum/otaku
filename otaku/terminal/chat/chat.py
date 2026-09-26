"""`Chat` — the package's namesake: what the terminal holds beside the
backend's Session, and `say`, the ONE chokepoint command output prints
through (the lead blank, the ledger invalidation — what keeps the
spacing honest without stdout wrappers). Its own module because the
dispatch topology forces it: the bindings need `Chat`, the loop needs
the bindings — whoever routes cannot live beside this class.
"""

import sys

from otaku.backend import Message
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Session
from otaku.terminal.chat.ledger import ScreenLedger
from otaku.terminal.tty.render import notes_displayed, turn
from otaku.terminal.tty.statusline import StatusLine

# Turns echoed when a story (re)opens (the launch, a browser pick, an
# import), so the scene is on screen before the prompt — terminal
# presentation policy, the web re-renders the story itself.
RESUME_TURNS = 4


class Chat:
    """The terminal-side state, one per run — and the session it drives:
    handlers take `chat` alone and reach the session through it."""

    session: Session
    ledger: ScreenLedger
    status_line: StatusLine
    # The loop's exit flag: /bye (and Ctrl+D) raise it, and the run loop
    # leaves after the current submission instead of unwinding mid-command.
    quit: bool

    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = ScreenLedger()
        self.quit = False
        # One renderer for the prompt's toolbar and the pinned row, both
        # reading the same channel, so the line never twitches at the
        # streaming handoff.
        self.status_line = StatusLine(session.status)

    @property
    def notes_displayed(self) -> bool:
        """Whether the open story displays the model's notes
        (`render.notes_displayed` over its settings) — read as each echo
        draws, so an echo and the ledger's measure of it agree."""
        return notes_displayed(api_stories.get_settings(self.session))

    def say(self, text: str) -> None:
        """Print command output below the typed line: the lead blank on
        the first write, ledger invalidation where due — every handler
        answers through here (or through the ledger's own channels).
        Multi-line text is one answer: the bookkeeping runs once."""
        sys.stdout.write(self.ledger.said() + text + "\n")
        sys.stdout.flush()

    def restore_tail(self, count: int, above: str = "") -> None:
        """Hand the just-echoed tail (`render.last_turns(…, count)`) to
        the ledger, grouped the way /undo pops — a reply plus the one
        user row before it, either alone when the other is missing — so
        the shown turns can be taken back off the screen without having
        been played. `above` names the report line printed (with its
        blank) right over the echo: it joins the oldest restored exchange
        and refreshes with it instead of going stale. Call right after
        the echo prints, and only when the echo is the flow's last
        output."""
        shown = list(self.session.messages)[-count:]
        groups: list[tuple[Message | None, Message | None]] = []
        i = len(shown)
        while i > 0:
            reply = shown[i - 1] if shown[i - 1].role == "assistant" else None
            if reply is not None:
                i -= 1
            prompt = shown[i - 1] if i > 0 and shown[i - 1].role == "user" else None
            if prompt is not None:
                i -= 1
            if reply is None and prompt is None:
                break  # an unexpected role — better unerasable than wrong
            groups.append((prompt, reply))
        notes = self.notes_displayed
        for prompt, reply in reversed(groups):
            self.ledger.restore_exchange(
                turn(prompt) if prompt else None,
                turn(reply, notes=notes) if reply is not None else None,
                above=above,
            )
            above = ""  # the report belongs to the oldest exchange only
