"""`Chat` — the package's namesake: what the terminal holds beside the
backend's Session, and `say`, the ONE chokepoint command output prints
through (the lead blank, the ledger invalidation — what keeps the
spacing honest without stdout wrappers). Its own module because the
dispatch topology forces it: the bindings need `Chat`, the loop needs
the bindings — whoever routes cannot live beside this class.
"""

import sys

from otaku.backend import StoryMode
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Session
from otaku.terminal.chat.ledger import ScreenLedger
from otaku.terminal.tty.render import flow_offsets, shown_tools, turn
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
    # The submission being played is the canvas's CONTINUE — an empty
    # line, no row of the reader's to echo — so the stream places the
    # reply where the text ended (`stream.show`). Set by the loop for
    # the one submission, cleared with it.
    continuing: bool
    # How full the next request is, as the status row shows it on its
    # right ("85% of 128K"); "" while the window is not known.
    # Read again by the loop before every prompt.
    context_use: str

    def __init__(self, session: Session) -> None:
        self.session = session
        self.quit = False
        self.continuing = False
        self.context_use = ""
        # One renderer for the prompt's toolbar and the pinned row, both
        # reading the same channels, so the line never twitches at the
        # streaming handoff.
        self.status_line = StatusLine(session.status, lambda: self.context_use)
        # An erase the ledger makes mid-stream takes the pinned row with it.
        self.ledger = ScreenLedger(repaint=self.status_line.refresh)

    @property
    def shown_tools(self) -> frozenset[str]:
        """The tools whose calls the open story shows (`render.shown_tools`
        over its settings) — read as each echo draws, so an echo and the
        ledger's measure of it agree."""
        return shown_tools(api_stories.get_settings(self.session))

    def say(self, text: str) -> None:
        """Print command output below the typed line: the lead blank on
        the first write, ledger invalidation where due — every handler
        answers through here (or through the ledger's own channels).
        Multi-line text is one answer: the bookkeeping runs once."""
        sys.stdout.write(self.ledger.said() + text + "\n")
        sys.stdout.flush()
        self.ledger.note_printed(text + "\n")

    def restore_tail(self, count: int, above: str = "") -> None:
        """Hand the just-echoed tail (`render.last_turns(…, count)`) to
        the ledger, grouped the way /undo pops — a reply plus the one
        user row before it, either alone when the other is missing — so
        the shown turns can be taken back off the screen without having
        been played. `above` names the report line printed (with its
        blank) right over the echo: it joins the oldest restored exchange
        and refreshes with it instead of going stale. Call right after
        the echo prints, and only when the echo is the flow's last
        output. In the canvas the echo is one text (`render.flow`), and
        each exchange is placed in it by its offsets
        (`ledger.restore_flow`)."""
        shown = list(self.session.messages)[-count:]
        groups: list[tuple[int | None, int | None]] = []  # indexes into `shown`
        i = len(shown)
        while i > 0:
            reply = i - 1 if shown[i - 1].role == "assistant" else None
            if reply is not None:
                i -= 1
            prompt = i - 1 if i > 0 and shown[i - 1].role == "user" else None
            if prompt is not None:
                i -= 1
            if reply is None and prompt is None:
                break  # an unexpected role — better unerasable than wrong
            groups.append((prompt, reply))
        drawn = self.shown_tools
        ordered = list(reversed(groups))
        if self.session.layout.mode is StoryMode.STORY:
            text, starts = flow_offsets(shown, shown=drawn)
            for n, (prompt, reply) in enumerate(ordered):
                first = prompt if prompt is not None else reply
                assert first is not None
                start = starts[first]
                following = ordered[n + 1] if n + 1 < len(ordered) else None
                end = len(text)
                if following is not None:
                    opener = following[0] if following[0] is not None else following[1]
                    assert opener is not None
                    end = starts[opener]
                reply_offset = None
                if reply is not None:
                    reply_offset = starts[reply] - start
                self.ledger.restore_flow(
                    text[:start],
                    text[start:end],
                    reply_offset,
                    last=following is None,
                    above=above,
                )
                above = ""
            return
        for prompt, reply in ordered:
            self.ledger.restore_exchange(
                turn(shown[prompt]) if prompt is not None else None,
                turn(shown[reply], shown=drawn) if reply is not None else None,
                above=above,
            )
            above = ""  # the report belongs to the oldest exchange only
