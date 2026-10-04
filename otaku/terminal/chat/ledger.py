"""What the played exchanges occupy on screen, and how to take them back.

Every played submission shows the same way: the typed input erased and
re-echoed as the grey `> ` block, one blank line, then the reply below —
thinking, prose, error and stats lines alike. `ScreenLedger` owns that
presentation and its bookkeeping: `echo_block` prints the block, `reply`
is the writer everything the reply shows goes through (so the row count
can never drift from the screen), and a stack of exchanges still sitting
directly above the prompt lets /undo erase the whole last exchange and
/regen erase just its reply and stream the new one in its place — /undo
again keeps peeling turns off the screen. A resume echo's turns join the
stack the same way (`restore_exchange`): turns shown at launch, after a
browser pick, or after an import can be taken back like played ones.

The canvas plays the same game over ONE text (`echo_flow`): an exchange
there is the reader's line and the reply, or a continue's reply alone,
starting where the text before it ended — at a column, mid-line, for an
inline seam — and an erase rewinds to that point rather than a row's
start (`_ERASE_FROM`), the row ended again where the erased text had
ended it. `reply_begins` marks where the reply starts inside such an
exchange, which is where /regen rewinds to, and `restore_flow` adopts
the exchanges a flow echo printed as `restore_exchange` adopts blocks.

An erase happens only when it provably restores the screen:

- the stack must be live — any inline print the ledger does not make
  (another command's output, a usage or crash line, an interrupted
  prompt) must `invalidate()` it first, because that output now sits
  between the last exchange and the cursor;
- the terminal width must still be what the exchange printed at — a
  resize rewraps history and voids every count;
- the terminal must answer the cursor-position query, and the rows to
  erase must all lie above the cursor on screen. Rows scrolled into
  scrollback are beyond reach — no escape sequence edits scrollback — so
  a partially visible exchange is left alone.

Otherwise the caller falls back to printing, exactly as before erasing
existed. The clearable run is TURNS — blocks and replies: a report or
marker line ("[ undone… ]", "[ regenerating ]") ends it when it prints,
the same as any other command's output. Turns echoed BELOW an undo
report start a new run: a regen clears just that response and streams
the fresh one in its place, the request still displayed above — and an
undo that takes the re-echoed exchange takes the report line with it and
prints a fresh one in the same spot, so the screen always shows exactly
one, current, report — never one pointing at nothing, never two stacked.

Every such break is drawn: `rule()` prints one faint dotted line over
whatever the break has to say. It is the ONE piece of that output no
erase touches — it sits above the erasable lead — so a report replacing
a marker slides in under the standing rule instead of stacking a second
one; only fresh output past the ledger (`invalidate`) earns a new one.
A successful /undo otherwise says nothing — the vanishing is the whole
report — and leaves the screen as if the exchange was never played; the
standing blank line before the prompt is then already on screen, and
`gap()` skips the loop's own.

Command output is spaced like a reply: while the typed command line is
on screen, one blank line separates it from the command's first inline
write — armed by `command_output` around dispatch, consumed by the first
`Chat.say` (the arming and consumption are internal: say and this
window are the whole protocol), and disarmed by the paths that replace
the typed line instead of printing under it (the block echo, a
successful erase). A command that says nothing gets no blank, so a
cancelled picker leaves the screen exactly as it was.
"""

import contextlib
import sys
from collections.abc import Callable, Iterator
from typing import TextIO, cast

from otaku.terminal.tty import CLEAR_SCREEN, break_rule, user_block
from otaku.terminal.tty.cursor import RowTracker, cursor_row, measure, terminal_width

# Up N rows, to column 0, erase to the end of the screen.
_ERASE_UP = "\x1b[{}A\r\x1b[J"
# Up N rows, to column C, erase from there to the end of the screen: what
# stood before column C on that row stays.
_ERASE_FROM = "\x1b[{}A\r\x1b[{}C\x1b[J"


class ScreenLedger:
    """Built once per session; the run loop, `submit`, and the playing
    handlers talk to it. Everything inline happens on the loop's thread —
    the spinner and the pinned status row write straight to the terminal
    (past sys.stdout) and are cursor-neutral, so they stay outside the
    count by construction."""

    def __init__(self, repaint: Callable[[], None] = lambda: None) -> None:
        # What stands on the pinned status row is painted again after an
        # erase: an erase to the end of the screen ignores the scroll
        # region and takes that row too.
        self._repaint = repaint
        # Rows the current submission's typed input still occupies on
        # screen — fed by `typed`, consumed by the echo and the erases,
        # dropped by `typed_gone`, ended by `gap`.
        self._typed_rows = 0
        self._stack: list[_Exchange] = []
        self._suppress_gap = False
        self._lead_blank = False  # a command's first say earns a blank
        self._stale_on_say = False  # this command's output would invalidate
        self._wrote = False  # something printed this submission (the gap's question)
        self._ruled = False  # a break rule stands above the current run
        self._reply = _ReplyWriter(self)
        self._cpr_dead = False  # the terminal never answered — stop asking
        # Where the text on screen ends — the last line's text, escapes
        # and all, as every print the ledger sees leaves it
        # (`note_printed`) — for the canvas's flow, whose inline line
        # goes on from there (`echo_flow`); `_pending` the newlines
        # printed since, which end the line without starting another.
        self._tail = ""
        self._pending = 0
        # A story-mode flow is being written into: the cursor was placed
        # by `echo_flow`, and the stream must not place it again.
        self.flow_open = False
        # PROTOTYPE: the prompt stood in the text and the cursor is back at
        # the text's end (`typed_in_text`): `echo_flow` places nothing.
        self._at_tail = False

    # ---------- the submission's typed input ----------

    def typed(self, text: str) -> None:
        """Count the rows the submission's typed input occupies on screen
        (the prompt read with its prefix, an inline ask's echo — pass the
        text WITH its trailing newline). Accumulates across a `\"\"\"`
        block's prompts; the row math lives HERE — no caller does
        arithmetic on the ledger."""
        self._typed_rows += measure(text, terminal_width())

    def typed_gone(self) -> None:
        """The typed input left the screen by other means — a shortcut
        key erased its own prompt line, an interrupted prompt was
        abandoned: drop the count."""
        self._typed_rows = 0

    # ---------- the exchange lifecycle ----------

    def echo_block(self, text: str, above: str = "") -> None:
        """Show `text` as the played-turn block: erase the typed input,
        print the block and the blank line under it, and open a new
        exchange. `text` arrives rendered — how a message looks is the
        caller's to decide; rows and erasure are all this owns.

        Until a reply writes, that blank doubles as the pre-prompt gap —
        a /you that answers nothing suppresses the loop's own. `above` is
        a marker line just printed (with its blank) over this echo — a
        fallback /regen's — riding the exchange the way an undo report
        rides its re-echo (see `restore_exchange`)."""
        self._lead_blank = False  # the typed line is replaced, not printed under
        self._wrote = True
        width = terminal_width()
        block = user_block(text)
        erase = _ERASE_UP.format(self._typed_rows) if self._typed_rows else ""
        sys.stdout.write(f"{erase}{block}\n\n")
        sys.stdout.flush()
        if erase:
            self._repaint()
        self.note_printed(block + "\n\n")
        entry = _Exchange(measure(block + "\n", width), width)
        if above:
            entry.lead = measure(above + "\n", width) + 1
        self._stack.append(entry)
        self._suppress_gap = True
        self._typed_rows = 0

    def echo_flow(self, *, inline: bool, above: str = "") -> bool:
        """The canvas's stand-in for `echo_block`: the played line is not
        a block but prose in the ONE text, so this only PLACES the cursor
        for it, and the caller prints the line through `reply`. The typed
        input is erased; a paragraph is left under the standing blank; an
        inline line takes the blank back too and goes on where the text
        ended (`tail_column`), mid-line — the exchange above then ends
        there, its ending newline gone with the blank. Opens the
        exchange /undo and /regen work, starting at that point. Also the
        continue's placing, with no line to follow; `above` as
        `echo_block`'s."""
        self._lead_blank = False
        self._wrote = True
        self._suppress_gap = False
        width = terminal_width()
        # PROTOTYPE: a line goes on from where the text ends only while the
        # story's text is the last thing on screen; under anything else (a
        # command's output) it starts a line of its own
        top = self._top()
        inline = inline and top is not None and top.flow
        column = self.tail_column if inline else 0
        tail_before = self._tail
        at_tail, self._at_tail = self._at_tail, False
        rows = 0 if at_tail else self._typed_rows + (1 if inline else 0)
        if rows:
            sys.stdout.write(_ERASE_UP.format(rows))
        if inline:
            if not at_tail:
                sys.stdout.write(self._onto_tail())
            top = self._top()
            if top is not None and top.tracker.rows:
                top.tracker.rewind(top.tracker.rows - 1, column)
        else:
            if at_tail:
                # the text's line ended and the blank under it, as the
                # prompt below the text would have left them
                sys.stdout.write("\n\n")
            self._tail = ""
        self._pending = 0
        sys.stdout.flush()
        if rows:
            self._repaint()
        entry = _Exchange(0, width)
        entry.flow = True
        entry.inline = inline
        # a text that filled its row ends at the width: the line goes on
        # from the next row's start, where the terminal wraps to
        entry.start_column = column if column < width else 0
        entry.tail_before = tail_before
        entry.tracker.rewind(0, column)
        if above:
            entry.lead = measure(above + "\n", width) + 1
        self._stack.append(entry)
        self._typed_rows = 0
        self.flow_open = True
        return inline  # PROTOTYPE: placed inline — false under anything but the text

    def reply_begins(self) -> None:
        """The reply's first character goes where the cursor now stands —
        the canvas's drawer says so once the played line is echoed, or at
        once for a continue — the point /regen rewinds to."""
        entry = self._top()
        if entry is not None and entry.flow:
            entry.reply_at = (entry.tracker.rows, entry.tracker.column)
            entry.tail_at_reply = self._tail

    def restore_flow(
        self, before: str, text: str, reply_offset: int | None, *, last: bool, above: str = ""
    ) -> None:
        """Adopt a story-mode exchange a flow echo just printed: `before`
        is the flow's text above it (its last line places the start
        column, and is the tail an erase puts back), `text` the
        exchange's own through the join to the next — a paragraph join's
        first newline ends the exchange as a reply's own does, and its
        second is the blank the loop's gap stands in for, so only one is
        counted — and `reply_offset` where in `text` the reply begins
        (None: a line no reply followed). `last` says the echo's own
        newline ended it. Measured, never printed; oldest first, as
        `restore_exchange`."""
        width = terminal_width()
        inline = bool(before) and not before.endswith("\n")
        # the line the exchange goes on from: the text's own last line
        # where it is inline; a paragraph starts a fresh one
        lead = before.rsplit("\n", 1)[-1] if inline else ""
        column = _column(lead, width) if inline else 0
        entry = _Exchange(0, width)
        entry.flow = True
        entry.inline = inline
        entry.start_column = column if column < width else 0
        # what an erase puts back as the text's last line: the text's own,
        # above the blank a paragraph stands under
        entry.tail_before = before.rstrip("\n").rsplit("\n", 1)[-1]
        entry.tracker.rewind(0, column)
        body = text.rstrip("\n")
        if reply_offset is not None:
            head = body[:reply_offset]
            entry.tracker.feed(head)
            entry.reply_at = (entry.tracker.rows, entry.tracker.column)
            entry.tail_at_reply = (lead + head).rsplit("\n", 1)[-1]
            entry.tracker.feed(body[reply_offset:])
        else:
            entry.tracker.feed(body)
        if last or text.endswith("\n"):
            entry.tracker.feed("\n")
        if above:
            entry.lead = measure(above + "\n", width) + 1
        self._stack.append(entry)

    # ---------- PROTOTYPE: the prompt in the story's text ----------

    def can_type_in_text(self) -> bool:
        """Whether the next prompt can stand in the story's own text: the
        flow is the last thing on screen, as printed at this width, its
        last line and the rows under it known."""
        top = self._top()
        return (
            top is not None
            and top.flow
            and bool(self._tail)
            and self._pending > 0
            and top.width == terminal_width()
        )

    def lift_to_tail(self) -> None:
        """Up onto the first row of the text's last line and erase from
        there: the prompt draws that line again as its message, and the
        typing goes on after its last word."""
        tracker = RowTracker(terminal_width())
        tracker.feed(self._tail)
        sys.stdout.write(f"\x1b[{self._pending + tracker.rows}A\r\x1b[J")
        sys.stdout.flush()

    def resume_tail(self) -> None:
        """The prompt that stood in the text is done, and erased all it
        drew (`erase_when_done`) — the text's last line, its message, with
        it — the caret back at that line's start. Write the line again: the
        caret then stands where the text ends, for the stream to go on from
        or `below_text` to leave. Nothing is counted, so no terminal's way
        of ending a frame can put the text a row off."""
        sys.stdout.write(self._tail)
        sys.stdout.flush()
        self._repaint()
        self._at_tail = True
        self._pending = 0
        self._typed_rows = 0

    def below_text(self, shown: str = "") -> None:
        """From the text's end back to the layout a prompt below the text
        leaves: the line ended, the blank under it, and `shown` — a typed
        command, as that prompt showed it — on the row after."""
        self._at_tail = False
        sys.stdout.write("\n\n")
        self._pending = 2
        if shown:
            sys.stdout.write(shown + "\n")
            self.typed(shown + "\n")
        sys.stdout.flush()

    @property
    def tail(self) -> str:
        """The last line of text on screen, as printed (escapes and all):
        what an inline continuation goes on from."""
        return self._tail

    @property
    def tail_column(self) -> int:
        """The column the text on screen ends at — the last line measured
        as the terminal lays it out, wrapping included."""
        return _column(self._tail, terminal_width())

    def note_printed(self, text: str) -> None:
        """Keep where the text ends as `text` prints: the last line's
        text, and the newlines after it. Newlines alone end the line
        without starting another — the stream's own after a reply, the
        loop's gap — so the tail stays the text's until something prints
        below it. `reply` notes its own writes; the say protocol and the
        few prints past the ledger call this themselves."""
        core = text.rstrip("\n")
        trailing = len(text) - len(core)
        if not core:
            self._pending += trailing
            return
        # newlines printed BEFORE this text ended the line it would
        # otherwise go on; its own trailing ones are counted after it
        if self._pending:
            self._tail = ""
        if "\n" in core:
            self._tail = core.rsplit("\n", 1)[1]
        else:
            self._tail += core
        self._pending = trailing

    def _onto_tail(self) -> str:
        """The escape that puts the cursor where the text ended, from the
        row under it: up onto its last row and past its last character —
        or, where that row is full, nowhere, since the terminal wraps the
        next character to the row the cursor is on anyway."""
        column = self.tail_column
        if column >= terminal_width():
            return ""
        return "\x1b[1A" + (f"\x1b[{column}C" if column else "")

    @property
    def reply(self) -> TextIO:
        """Where a reply prints. Everything a stream shows inline goes
        through this writer; with no live exchange it just forwards."""
        return cast(TextIO, self._reply)

    def restore_exchange(self, block: str | None, reply: str | None, above: str = "") -> None:
        """Adopt an exchange a resume echo just printed — `block` and
        `reply` are the exact strings on screen, either possibly missing
        (an import's promptless tail, a reply whose prompt scrolled out
        of the echo) — so /undo and /regen can take shown turns back
        without having played them. `above` is an undo report line
        printed (with one blank under it) directly over this exchange:
        erasing the exchange takes it too, so the caller prints a fresh
        report in the same spot — the report refreshes rather than going
        stale. Measured, never printed: call for each echoed exchange,
        oldest first, right after the echo — and only when the echo is
        the flow's last output."""
        width = terminal_width()
        entry = _Exchange(measure(block + "\n", width) if block else 0, width)
        if above:
            entry.lead = measure(above + "\n", width) + 1
        if block and reply is not None:
            entry.internal = 1
        if reply is not None:
            entry.tracker.feed(reply + "\n")
        self._stack.append(entry)

    def clear(self) -> None:
        """Wipe the visible screen (/clear): everything erased, the
        cursor home — scrollback stays. The stack empties with it, and
        the suppressed gap puts the next prompt flush with the top."""
        self._lead_blank = False  # nothing prints under a wiped typed line
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.flush()
        self._tail, self._pending = "", 0
        self._stack.clear()
        self._ruled = False
        self._suppress_gap = True

    # ---------- the break rule ----------

    def rule(self) -> None:
        """The break rule marking where the played sequence breaks — a
        report or marker that lands below live output instead of
        replacing it, or a story swapped underneath the screen. Prints
        the rule and the blank line under it, always OVER what the break
        has to say, so every announcement reads the same way.

        It sits above the erasable lead, so no erase reaches it, and it
        prints once: a rule already standing stays put while the report
        under it refreshes, so an /undo taking over a /regen marker keeps
        the one rule instead of stacking a second. That is why the blank
        belongs to the rule and not to the caller — a caller printing its
        own would add one per refresh, the rule drifting further from its
        report every time. `invalidate` — output past the ledger, which
        is a break of its own — is what lets the next rule print, and the
        rule counts as that output itself: drawn by a command that does
        not manage the screen, it lands below whatever stood there, so
        the rule a PREVIOUS command left cannot swallow this one."""
        # One blank line ALWAYS separates the rule from what stands
        # above it: the armed lead blank when the typed line does, a
        # fresh one when this submission already printed (an import's
        # notices), nothing when an erase just rewound to a standing
        # blank.
        wrote_before = self._wrote
        lead = self.said()
        if self._ruled:
            sys.stdout.write(lead)  # consumed is printed, never dropped
            return
        above = lead or ("\n" if wrote_before else "")
        sys.stdout.write(f"{above}{break_rule(terminal_width())}\n\n")
        sys.stdout.flush()
        self.note_printed(break_rule(terminal_width()) + "\n\n")
        self._ruled = True

    # ---------- erasing ----------

    def top_is_report(self) -> bool:
        """A report or marker line rides the exchange on top (an undo
        report over its re-echo, a fallback /regen's marker over its
        echo): erasing the exchange takes that line too, so the caller
        must print a fresh report in its place — the screen would
        otherwise show a message pointing at nothing."""
        entry = self._top()
        return entry is not None and entry.lead > 0

    def erase_exchange(self) -> bool:
        """Take the whole last exchange off the screen (/undo): the typed
        command and the standing gap above the cursor, then the reply,
        the blank, and the block. True when erased — the caller prints
        nothing; False means fall back to reporting (and the fallback
        print must invalidate)."""
        entry = self._top()
        if entry is None or not self._erase(
            self._typed_rows + 1 + entry.rows, entry, column=entry.start_column
        ):
            return False
        self._stack.pop()
        self._typed_rows = 0
        if entry.flow:
            self._rewound(entry)
        else:
            self._suppress_gap = True
        return True

    def erase_reply(self) -> bool:
        """Clear the ground the fresh reply takes (/regen): the cursor
        lands where the reply began — or where an unanswered prompt's
        reply WOULD begin — and the new one streams there. A block no
        reply followed (a resumed prompt, a /you that answered nothing)
        keeps the blank under it: that is the gap the reply sits below,
        so only the typed line goes, and there may be nothing at all to
        erase. False — no live exchange, or an unprovable erase — means
        fall back to the marker, which invalidates like any command
        output."""
        entry = self._top()
        if entry is None:
            return False
        if entry.flow:
            # to where the reply began, mid-line where the line was: the
            # fresh take goes on from the reader's words
            at_rows, at_column = entry.reply_at or (0, entry.start_column)
            rows = self._typed_rows + 1 + (entry.tracker.rows - at_rows)
            if not self._erase(rows, entry, column=at_column):
                return False
            entry.tracker.rewind(at_rows, at_column)
            self._tail = entry.tail_at_reply if entry.reply_at else entry.tail_before
            self._pending = 0
            self._typed_rows = 0
            return True
        rows = self._typed_rows + (1 + entry.tracker.rows if entry.tracker.rows else 0)
        if rows and not self._erase(rows, entry):
            return False
        entry.tracker.reset()
        self._typed_rows = 0
        return True

    def erase_reply_tail(self) -> bool:
        """Take the current, just-interrupted reply off the screen (the
        in-stream Ctrl+R): the cursor sits right under it — no typed
        line, no gap. False means the caller prints the regenerating
        marker instead, through `reply`, so the exchange keeps covering
        it."""
        entry = self._top()
        if entry is None or entry.tracker.rows == 0:
            return False
        if entry.flow:
            at_rows, at_column = entry.reply_at or (0, entry.start_column)
            if not self._erase(entry.tracker.rows - at_rows, entry, column=at_column):
                return False
            entry.tracker.rewind(at_rows, at_column)
            self._tail = entry.tail_at_reply if entry.reply_at else entry.tail_before
            self._pending = 0
            return True
        if not self._erase(entry.tracker.rows, entry):
            return False
        entry.tracker.reset()
        return True

    # ---------- the dispatch window ----------

    @contextlib.contextmanager
    def command_output(self, *, manages_screen: bool = False) -> Iterator[None]:
        """Around a dispatched command: its first `Chat.say` earns one
        blank line under the typed command line — the same separation a
        reply gets under its block. Armed only while the typed line is
        still on screen; a command that says nothing leaves no blank. On
        a crash the armed flag survives the window, so the crash report
        gets the same separation.

        The window also decides when the command invalidated the stack.
        `manages_screen` names the playing commands, which own the ledger
        themselves; every other command's output lands below the last
        exchange, so its FIRST say invalidates — not the dispatch that
        precedes it. A full-screen picker left without a choice says
        NOTHING (prompt_toolkit holds the real stream) and restores the
        screen it took over, so the exchanges above are still exactly
        where they were and /regen can erase in place. Its typed line is
        the exception: still on screen, below them all, so a command that
        said nothing invalidates anyway when one stands — a shortcut key
        erased its own."""
        self._lead_blank = self._typed_rows > 0
        self._stale_on_say = not manages_screen
        try:
            yield
            self._lead_blank = False  # nothing said — nothing to lead
        finally:
            if self._stale_on_say and self._typed_rows:
                self.invalidate()
            self._stale_on_say = False

    # ---------- the run loop's contract ----------

    def invalidate(self) -> None:
        """The stack no longer describes the screen — something printed
        inline past the ledger. Called by the say protocol for every
        non-playing command, by every fallback print site, and by the run
        loop on a KeyboardInterrupt."""
        if self._at_tail:
            self.below_text()  # PROTOTYPE: output lands under the text, not after it
        self._stack.clear()
        self._ruled = False
        self._suppress_gap = False
        self.flow_open = False

    def gap(self, *, deferred: bool = False) -> bool:
        """The between-submissions blank line, printed by the run loop
        after every submission — or skipped when nothing calls for one (a
        shortcut that said nothing left the screen untouched) or the
        screen already ends in the standing blank (an erased undo, an
        echo no reply followed). `deferred` leaves a wanted blank to the
        PROMPT to draw as its own first row (the bar over a question's
        answers, a blank once they hide): True says the prompt owes it —
        the row is on screen either way, so the row math above holds.
        Ends the submission: the typed count and the wrote flag reset
        with it."""
        wants = self._typed_rows > 0 or self._wrote
        suppressed = self._suppress_gap
        self._suppress_gap = False
        self._typed_rows = 0
        self._wrote = False
        self.flow_open = False
        if not (wants and not suppressed):
            return False
        if deferred:
            return True
        print()
        self.note_printed("\n")  # the rows under the text's end, counted
        return False

    # ---------- the say protocol's side ----------

    def said(self) -> str:
        """The say protocol's first-write bookkeeping — called by
        `Chat.say` and the few inline-ask sites that print past it (an
        `input()` echo), nobody else: invalidate when this command's
        output lands past the stack, cancel a standing suppressed gap
        (output follows after all), and hand back the armed lead blank
        (once). Returns the prefix to print before the text ("" or the
        blank line)."""
        self._wrote = True
        self._suppress_gap = False
        if self._stale_on_say:
            self._stale_on_say = False
            self.invalidate()
        if self._lead_blank:
            self._lead_blank = False
            return "\n"
        return ""

    def _top(self) -> "_Exchange | None":
        return self._stack[-1] if self._stack else None

    def _erase(self, rows: int, entry: "_Exchange", *, column: int = 0) -> bool:
        """Up `rows` rows and erase to the end of the screen — from
        `column` of that row, what stood before it staying, where a flow
        exchange began mid-line."""
        if entry.width != terminal_width():
            self.invalidate()  # a resize voided every count
            return False
        if self._cpr_dead:
            return False
        row = cursor_row()
        if row is None:
            self._cpr_dead = True
            return False
        if rows > row - 1:
            return False  # partly in scrollback — beyond repair, keep it
        self._lead_blank = False  # the typed line goes; nothing prints under it
        sys.stdout.write(_ERASE_FROM.format(rows, column) if column else _ERASE_UP.format(rows))
        sys.stdout.flush()
        self._repaint()
        return True

    def extend_reply(self, text: str) -> None:
        """Draw `text` more of the reply on top, in place, once an undo
        took the exchange under it off (`erase_exchange`): one blank line
        under the reply's text — a paragraph's standing blank becomes that
        line, the reply's own row now — and `text` ending its row as a
        reply does. What an undo that left the story on a question draws
        the question back with (`bindings._pose_again`)."""
        entry = self._top()
        lead = "\n"
        if self._pending >= 2:
            # the blank stands already: counted with the reply from now on
            lead = ""
            if entry is not None:
                entry.tracker.feed("\n")
            self._pending = 1
        self.reply.write(lead + text)
        self._suppress_gap = False

    def _rewound(self, entry: "_Exchange") -> None:
        """The screen after a flow exchange is erased: the text above
        stands as it was, its tail put back. Mid-line, the row is ended
        again — the newline the erased text had taken — and the loop's
        gap follows; at a row's start (a paragraph's, or the row after a
        text that filled its own) the row above is whole already, and a
        paragraph's standing blank is the gap."""
        self._tail = entry.tail_before
        top = self._top()
        if not entry.inline:
            self._pending = 2
            self._suppress_gap = True
            return
        if entry.start_column:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if top is not None:
            top.tracker.feed("\n")
        self._pending = 1
        # the row is ended but no blank stands under it: the loop's gap
        # is wanted, which `_wrote` is what asks for
        self._suppress_gap = False
        self._wrote = True

    def _reply_written(self, text: str) -> None:
        self._wrote = True
        self.note_printed(text)
        entry = self._top()
        if entry is None:
            return
        if entry.block and not entry.internal:
            # The blank under the block is now interior. A block-less
            # restored reply owns no blank — the row above it is the
            # deeper exchange's standing gap.
            entry.internal = 1
            self._suppress_gap = False
        entry.tracker.feed(text)


class _Exchange:
    """One played submission's screen extent: the grey block, the blank
    under it (claimed once a reply writes), and the reply's tracker —
    all at the width they printed at. `lead` counts an undo report's rows
    above the block, erased with the exchange and reprinted fresh. A
    story-mode exchange (`flow`) has no block: its text begins at
    `start_column` of the row it opened on — mid-line when `inline` —
    and `reply_at` is where the reply begins in its tracker's count;
    `tail_before` and `tail_at_reply` are the ledger's tail at those two
    points, put back when an erase rewinds to them."""

    __slots__ = (
        "block",
        "flow",
        "inline",
        "internal",
        "lead",
        "reply_at",
        "start_column",
        "tail_at_reply",
        "tail_before",
        "tracker",
        "width",
    )

    def __init__(self, block: int, width: int) -> None:
        self.block = block
        self.internal = 0
        self.lead = 0
        self.tracker = RowTracker(width)
        self.width = width
        self.flow = False
        self.inline = False
        self.start_column = 0
        self.reply_at: tuple[int, int] | None = None
        self.tail_before = ""
        self.tail_at_reply = ""

    @property
    def rows(self) -> int:
        return self.lead + self.block + self.internal + self.tracker.rows


def _column(text: str, width: int) -> int:
    """The column `text` ends at when it is the last line on screen —
    laid out at `width`, wrapping included, escapes taking nothing."""
    tracker = RowTracker(width)
    tracker.feed(text)
    return tracker.column


class _ReplyWriter:
    """The file-like a reply prints through: feeds the live exchange's
    tracker (a no-op between exchanges), forwards to stdout, and flushes
    on newlines the way a line-buffered stream would."""

    def __init__(self, ledger: ScreenLedger) -> None:
        self._ledger = ledger

    def write(self, text: str) -> int:
        self._ledger._reply_written(text)
        count = sys.stdout.write(text)
        if "\n" in text:
            sys.stdout.flush()
        return count

    def flush(self) -> None:
        sys.stdout.flush()
