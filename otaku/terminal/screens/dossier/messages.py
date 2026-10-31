"""The messages tab: the story browser's message view — the messages
listed, the discarded among them faint, an asterisk for their number,
while `h` shows them, one read whole beside them, `e` correcting it where it is read,
space pinning it, and the story resumed from one: on the last at once,
on an earlier one after asking what resuming there means (fork — the
default — truncate, or cancel), on a discarded one likewise (resume —
the default — fork, or cancel)."""

from typing import Literal

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.formatting import flatten, printable, truncate
from otaku.terminal.api import Refused
from otaku.terminal.screens.base import (
    ansi_fragments,
    wrap_text,
)
from otaku.terminal.screens.dossier.base import DossierBase, made_by
from otaku.terminal.tty import BOLD
from otaku.terminal.tty.render import pictures_note

# A pinned message's mark, where the list draws the dots around its role
# — the heavy asterisk, heavy in every font without a bold of its own —
# and the role between them bold.
_PIN_MARK = "✱"
# Normal intensity back, after a bold run.
_NORMAL = "\x1b[22m"
# What a pin does to the message it is on, dim under it in the preview —
# the page's sentence, copied (`web/static/js/screens/dossier.js`
# `_PIN_NOTE`): change one, change both.
_PIN_NOTE = (
    "Pinned: this message is sent as is and in its place, even after its scene is summarized."
)

# What picking a story or one of its turns settles to: open it as it is,
# continue in a copy from the turn, or move its head to the turn — a
# rewind on the chain, a resume on a discarded one (the turns it leaves
# stay as siblings).
_Landing = Literal["resume", "fork", "move"]


# The resume dialog's rows, in order: (action, label) — on an earlier
# message of the chain, and on a discarded one. The action is what the
# picker returns; "cancel" closes the dialog and stays in the browser.
_RESUME_OPTIONS: tuple[tuple[Literal["cancel"] | _Landing, str], ...] = (
    ("fork", "Fork — continue in a new story from here"),
    ("move", "Truncate — discard the messages after this one"),
    ("cancel", "Cancel"),
)
_DISCARDED_OPTIONS: tuple[tuple[Literal["cancel"] | _Landing, str], ...] = (
    ("move", "Resume — continue the story from this message"),
    ("fork", "Fork — continue in a new story from here"),
    ("cancel", "Cancel"),
)


class MessagesTab(DossierBase):
    """The messages, and the landings from them."""

    def _turn_rows(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if not self.turn_filtered:
            # nothing played, or nothing but discarded messages kept hidden
            caption = "  (no matches)" if self.query.strip() else "  (no messages yet)"
            out.append(("class:muted", caption))
            return out
        role_w = len("assistant")  # widest role name
        # prefix(4) + idx(4) + " · "(3) + role(role_w) + " · "(3) = fixed
        fixed = 4 + 4 + 3 + role_w + 3
        avail = max(10, self._max_row_content_width() - fixed)
        for row_i, orig in enumerate(self.turn_filtered):
            m = self.msgs[orig]
            # The list shows the line AS TYPED — the body is exactly
            # that, syntax included, so nothing is composed here. Slice
            # first: this renders per keystroke, and avail chars never
            # need more than a slice of a huge message.
            # A turn's pictures are noted after the line by `render`, the
            # way the played block notes them; the note's width comes off
            # the cut, so the row still fits.
            pictures = len(m.attachments)
            note_w = len(pictures_note(pictures)) + 1 if pictures else 0
            head = truncate(flatten(m.body[: 4 * avail]), max(4, avail - note_w))
            if not head and not pictures:
                head = "(empty)"
            # Styled AFTER the cut, so no escape can be sliced in half —
            # and on every row, selected or not: what a line says it is
            # does not depend on where the cursor happens to be. Then one
            # line again: a reply's calls are drawn as blocks, on rows of
            # their own, and a row has no rows. Never longer than the cut:
            # the tags the drawing drops outweigh the bar it adds.
            # A tool's turn is its result as stored — a search's is the
            # internet's text, so made printable: the transcript draws the
            # tool's label alone, but here the row is read for itself.
            if m.role == "tool":
                head = printable(head)
            else:
                head = flatten(
                    self._render(head, m.role, pictures=pictures, shown=self.shown_tools)
                )
            # The message's number along the chain, so a filtered row still
            # reads as its true position in the story; a discarded one has an
            # asterisk in its place.
            mark, role = "·", m.role
            if m.pinned:
                mark, role = _PIN_MARK, f"{BOLD}{m.role}{_NORMAL}"
            pad = " " * (role_w - len(m.role))
            row = f"{self.numbers[orig] or '*':>4} {mark} {role}{pad} {mark} {head}"
            self._emit_row(out, row_i == self.cursor, row, dim=m.discarded)
        return out

    def _turn_preview(self, width: int) -> StyleAndTextTuples:
        if not self.turn_filtered:
            return [("class:preview.muted", "nothing to preview")]
        m = self.msgs[self.turn_filtered[self.cursor]]
        out: StyleAndTextTuples = []
        if m.body or m.attachments:
            # Whatever `render` makes of it, parsed into fragments so
            # the window's own wrapping carries styles across wrapped
            # rows. Editing swaps this window out, so the buffer stays
            # raw text.
            # a tool's turn as its result, plain, as the list row reads it
            body = (
                printable(m.body)
                if m.role == "tool"
                else self._render(
                    m.body,
                    m.role,
                    segments=m.segments,
                    pictures=len(m.attachments),
                    shown=self.shown_tools,
                    width=max(10, self._preview_inner_width()),
                )
            )
            if not body.endswith("\n"):
                body += "\n"
            out.extend(ansi_fragments(body, "class:preview.body"))
        if m.pinned:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(_PIN_NOTE, width):
                out.append(("class:preview.muted", line + "\n"))
        # The template snapshot shown DIM after a blank line — the
        # template layer (its `{body}` placeholder and all) that the turn
        # was played with, which the body alone does not show. It is not
        # what the model reads; `/context` shows that.
        if m.template:
            if m.body:
                out.append(("class:preview.body", "\n"))
            for line in wrap_text(m.template, width):
                out.append(("class:preview.muted", line + "\n"))
        # The model that generated THIS turn — user turns have none
        # (messages.model is NULL there) and show nothing.
        if m.role == "assistant":
            out += made_by(m.provider, m.model, width)
        return out

    def _toggle_pin(self) -> None:
        """Space: the message under the cursor pinned, or unpinned — sent
        whole even where its scene is summarized — the backend's sentence
        the notice, a refusal's included."""
        if not self.turn_filtered:
            return
        orig = self.turn_filtered[self.cursor]
        m = self.msgs[orig]
        try:
            changed = self.api.stories.messages.update(self.story_id, m.id, pinned=not m.pinned)
        except Refused as e:
            self.notice = str(e)
            return
        self.msgs[orig] = changed.value
        self.notice = changed.notice

    def _toggle_discarded(self) -> None:
        """h: the discarded messages shown among the chain's, or hidden —
        the cursor kept on its message, or on the nearest shown above it."""
        at = self.turn_filtered[self.cursor] if self.turn_filtered else 0
        self.discarded_shown = not self.discarded_shown
        self._refilter()
        self.cursor = max((i for i, orig in enumerate(self.turn_filtered) if orig <= at), default=0)

    def _resume_options(self) -> tuple[tuple[Literal["cancel"] | _Landing, str], ...]:
        """The resume dialog's rows for the message under the cursor."""
        if self.turn_filtered and self.msgs[self.turn_filtered[self.cursor]].discarded:
            return _DISCARDED_OPTIONS
        return _RESUME_OPTIONS

    def _resume_text(self) -> StyleAndTextTuples:
        number = self.numbers[self.turn_filtered[self.cursor]] if self.turn_filtered else 0
        played = sum(n is not None for n in self.numbers)
        title = (
            f"Resume at message {number} of {played}"
            if number is not None
            else "Resume at a discarded message"
        )
        out: StyleAndTextTuples = [
            ("class:dialog.title", f"{title}\n"),
            ("class:dialog.body", "\n"),
        ]
        for i, (_, label) in enumerate(self._resume_options()):
            selected = i == self.resume_choice
            style = "class:row.selected" if selected else "class:dialog.body"
            out.append((style, f"{' > ' if selected else '   '}{label}\n"))
        out.append(("class:dialog.body", "\n"))
        out.append(("class:dialog.muted", "↑/↓ choose · enter confirm · esc cancel"))
        return out

    def _do_resume(self) -> None:
        action = self._resume_options()[self.resume_choice][0]
        self.confirming_resume = False
        if action == "cancel" or not self.turn_filtered:
            return
        self._land(action)

    def _land(self, action: _Landing) -> None:
        """Execute the settled pick (the screens' one ownership rule) and
        leave with the landing line for the caller to print."""
        upto = self.msgs[self.turn_filtered[self.cursor]]
        self._land_on(self.story_id, upto.id, action)

    def _land_on(self, story_id: int, upto_id: int | None, action: _Landing) -> None:
        """The landing itself: a story and a pick, the pick absent where
        there is nothing to pick — the list level resumes a story with
        nothing played without opening it. What the landing settled is
        kept for the caller to play: the story, a copy, or the story
        rewound or resumed on a discarded turn, and the line that says
        so."""
        try:
            if action == "resume":
                landed = self.api.stories.get(story_id)
                self.result = landed.id, landed.landed_line()
            else:
                assert upto_id is not None  # a copy and a head move cut AT a turn
                if action == "fork":
                    cut = self.api.stories.forks.create(story_id, at=upto_id)
                else:
                    cut = self.api.stories.update(
                        story_id, head=upto_id, expected_head=self._head()
                    )
                self.result = cut.value.id, cut.notice
        except Refused as e:
            self.notice = str(e)
            return
        get_app().exit()
