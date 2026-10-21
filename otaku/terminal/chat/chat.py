"""`Chat` — the package's namesake: what the terminal holds beside the
backend's App — the story it plays among them, its own and nobody
else's — and `say`, the ONE chokepoint command output prints
through (the lead blank, the ledger invalidation — what keeps the
spacing honest without stdout wrappers). Its own module because the
dispatch topology forces it: the commands need `Chat`, the run loop
needs the commands — whoever routes cannot live beside this class.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager

from otaku.terminal.api import (
    DEFAULT_LAYOUT,
    LAYOUT_SETTING,
    NO_STORY_HINT,
    Api,
    App,
    Conflict,
    Layout,
    Message,
    NotFound,
    StoryMode,
)
from otaku.terminal.api.stories import Story
from otaku.terminal.chat.ledger import ScreenLedger
from otaku.terminal.tty.render import flow_offsets, turn, unit_start
from otaku.terminal.tty.statusline import StatusLine

# Turns echoed when a story (re)opens (the launch, a browser pick, an
# import), so the scene is on screen before the prompt — terminal
# presentation policy, the web re-renders the story itself.
RESUME_TURNS = 4


class Chat:
    """The terminal-side state, one per run — and the app it drives:
    handlers take `chat` alone and reach the interface through it
    (`api`), the app itself only for its life and its channels."""

    app: App
    api: Api
    # The story this terminal plays — chosen here and moved by nothing
    # else — what it last read of it (`refresh`), and its head as this
    # terminal last drew it, which a head moved elsewhere differs from
    # (`catch_up`). None while it plays no story yet.
    story_id: int | None
    story: Story | None
    head: int | None
    # The layout the reader set for a story not made yet, handed over
    # when one is made (`ensure_story`); a premise makes the story itself.
    draft_layout: Layout | None
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
    # A reply streamed this submission (`stream.show`), or an undo left
    # the story on the question a reply ended on (`dispatch.stories`):
    # the prompt after it may offer the answers, where one after any other
    # command, its output standing between, offers none. Cleared by the
    # loop with each submission.
    replied: bool
    # In story mode, what the last reply ended on that chat mode prints
    # under it — its verbose stats, the failure that stopped it, or why
    # none was asked for: shown
    # on the status row, where a line would break the one text, until the
    # next line is sent. "" elsewhere.
    status_note: str
    # How full the next request is, as the status row shows it on its
    # right ("85% of 128K"); "" while the window is not known.
    # Read again by the loop before every prompt.
    context_fill: str

    def __init__(self, app: App) -> None:
        self.app = app
        self.api = Api(app)
        self.story_id = None
        self.story = None
        self.head = None
        self.draft_layout = None
        self.quit = False
        self.continuing = False
        self.replied = False
        self.status_note = ""
        self.context_fill = ""
        # One renderer for the prompt's toolbar and the pinned row, both
        # reading the same channels, so the line never twitches at the
        # streaming handoff.
        self.status_line = StatusLine(self.api.app.status.get, lambda: self.context_fill)
        # An erase the ledger makes mid-stream takes the pinned row with it.
        self.ledger = ScreenLedger(repaint=self.status_line.refresh)

    # ---------- the story it plays ----------

    def land(self, story_id: int) -> Story:
        """Play `story_id` from now on: read it, take its head as drawn —
        the caller echoes it — and remember it for the next launch."""
        self.story_id = story_id
        self.story = self.api.stories.get(story_id)
        self.head = self.story.head
        self.draft_layout = None
        self.api.settings.update(last_story=story_id)
        return self.story

    def leave(self) -> None:
        """Play no story — the one played is gone."""
        self.story_id = self.story = self.head = None

    def refresh(self) -> Story | None:
        """Read the story again, its facts as they stand now; None with no
        story, or one deleted under the terminal, which then plays none."""
        if self.story_id is not None:
            try:
                self.story = self.api.stories.get(self.story_id)
            except NotFound:
                self.leave()
        return self.story

    def mark_drawn(self, story: Story | None = None) -> None:
        """What the story is now is what the screen shows: `story` where
        the caller holds it as it now stands (an undo's answer), else read
        again — and its head taken as drawn, after this terminal's own
        turn, take or undo."""
        if story is not None and story.id == self.story_id:
            self.story = story
        else:
            self.refresh()
        self.head = self.story.head if self.story is not None else None

    def catch_up(self) -> bool:
        """Whether the story moved on elsewhere since this terminal last
        drew it — read again before each prompt; the head is taken as
        drawn either way, the caller echoing what moved."""
        drawn = self.head
        self.mark_drawn()
        return self.story is not None and self.story.head != drawn

    def ensure_story(self) -> int:
        """The story this terminal plays — made now, with what was set for
        it, when there is none yet: a first line, a title, a premise."""
        if self.story_id is None:
            made = self.api.stories.create()
            if self.draft_layout is not None:
                self.api.stories.settings.update(
                    made.value.id, LAYOUT_SETTING, layout=self.draft_layout
                )
            self.land(made.value.id)
        assert self.story_id is not None
        return self.story_id

    @contextmanager
    def writing(self) -> Iterator[int]:
        """The story a write lands in — made for it when there is none yet
        (`ensure_story`), and deleted again where the write raises, refused
        or cancelled, the layout set for it held as it was: a first write
        that lands nothing leaves no story behind."""
        if self.story_id is not None:
            yield self.story_id
            return
        layout = self.draft_layout
        story_id = self.ensure_story()
        try:
            yield story_id
        except BaseException:
            self.api.stories.delete(story_id)
            self.leave()
            self.draft_layout = layout
            raise

    def played(self) -> int:
        """The story this terminal plays, for a command that acts on one —
        refused with the backend's hint while there is none."""
        if self.story_id is None:
            raise Conflict(NO_STORY_HINT)
        return self.story_id

    def messages(self) -> list[Message]:
        """The story's chain as it stands; [] with no story."""
        if self.story_id is None:
            return []
        return self.api.stories.messages.list(self.story_id)

    @property
    def layout(self) -> Layout:
        """The layout the terminal draws in: the story's, or what was set
        for one not made yet — the default until then."""
        if self.story is not None:
            return self.story.layout
        return self.draft_layout or DEFAULT_LAYOUT

    @property
    def is_story_mode(self) -> bool:
        """Whether the story is read in story mode — echoed as one text
        then (`render.flow`), the played turns' blocks otherwise."""
        return self.layout.mode is StoryMode.STORY

    @property
    def premise(self) -> str:
        """The story's premise; none while there is no story."""
        return self.story.premise if self.story is not None else ""

    @property
    def can_attach(self) -> bool:
        """Whether a picture may ride the next line in the layout drawn
        in — per keystroke, so off what was last read; a story not made
        yet asks the model in use (its layouts here, the canvas and the
        manuscript, go over the chat wire alike)."""
        if self.story is not None:
            return self.story.can_attach
        model = self.api.app.get().model
        return model is not None and model.can_attach

    @property
    def shown_tools(self) -> frozenset[str]:
        """The tools whose calls the story shows (`Story.shown_tools`) —
        read as each echo draws, so an echo and the ledger's measure of
        it agree. Empty with no story: a story not made yet has every
        tool off."""
        if self.story_id is None:
            return frozenset()
        return self.api.stories.get(self.story_id).shown_tools

    @property
    def posed(self) -> bool:
        """Whether the story stands on a question its newest turn asked
        (`Story.question`) — read as each echo draws, as `shown_tools`
        is."""
        return (
            self.story_id is not None and self.api.stories.get(self.story_id).question is not None
        )

    # ---------- what it prints ----------

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
        the ledger, grouped the way /undo pops — a reply with its tool
        chain (`Message.unit`) plus the one user row before it,
        either alone when the other is missing — so
        the shown turns can be taken back off the screen without having
        been played. `above` names the report line printed (with its
        blank) right over the echo: it joins the oldest restored exchange
        and refreshes with it instead of going stale. Call right after
        the echo prints, and only when the echo is the flow's last
        output. In the canvas the echo is one text (`render.flow`), and
        each exchange is placed in it by its offsets
        (`ledger.restore_flow`)."""
        messages = self.messages()
        shown = messages[-count:]
        offset = len(messages) - len(shown)
        # indexes into `shown`: the prompt, and the reply's unit — its first
        # row and the row after its last — and whether the echo cut the unit
        groups: list[tuple[int | None, int | None, int, bool]] = []
        i = len(shown)
        while i > 0:
            end, reply, cut = i, None, False
            if shown[i - 1].role == "assistant":
                # the reply with its tool chain, as /undo pops it — the part
                # the echo shows, where it cut the chain
                begins = unit_start(messages, offset + i)
                cut = begins < offset
                reply = i = max(begins, offset) - offset
            prompt = i - 1 if i > 0 and shown[i - 1].role == "user" else None
            if prompt is not None:
                i -= 1
            if reply is None and prompt is None:
                break  # an unexpected role — better unerasable than wrong
            groups.append((prompt, reply, end, cut))
        drawn = self.shown_tools
        ordered = list(reversed(groups))
        if self.layout.mode is StoryMode.STORY:
            text, starts = flow_offsets(shown, shown=drawn, posed=self.posed)
            for n, (prompt, reply, end, cut) in enumerate(ordered):
                first = prompt if prompt is not None else reply
                assert first is not None
                start = starts[first]
                following = ordered[n + 1] if n + 1 < len(ordered) else None
                stop = len(text)
                if following is not None:
                    opener = following[0] if following[0] is not None else following[1]
                    assert opener is not None
                    stop = starts[opener]
                reply_offset = None
                if reply is not None:
                    reply_offset = starts[reply] - start
                self.ledger.restore_flow(
                    text[:start],
                    text[start:stop],
                    reply_offset,
                    last=following is None,
                    above=above,
                    shown_turns=end - reply if cut and reply is not None else None,
                )
                above = ""
            return
        for prompt, reply, end, _ in ordered:
            self.ledger.restore_exchange(
                turn(shown[prompt]) if prompt is not None else None,
                # the unit's turns as the echo printed them, a blank between
                "\n\n".join(turn(row, shown=drawn) for row in shown[reply:end])
                if reply is not None
                else None,
                above=above,
            )
            above = ""  # the report belongs to the oldest exchange only
