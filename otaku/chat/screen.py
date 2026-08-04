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
/stories pick, or after an /import can be taken back like played ones.

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
A successful /undo otherwise says nothing — the vanishing is the whole
report — and leaves the screen as if the exchange was never played; the
standing blank line before the prompt is then already on screen, so the
run loop asks `take_suppressed_gap()` before printing its own.

Command output is spaced like a reply: while the typed command line is
on screen, one blank line separates it from the command's first inline
write — armed by `command_output` around dispatch, consumed by the first
write, and disarmed by the paths that replace the typed line instead of
printing under it (the block echo, a successful erase). A command that
prints nothing gets no blank, so a cancelled picker leaves the screen
exactly as it was.
"""

import contextlib
import sys
from collections.abc import Iterator
from typing import Any, TextIO, cast

from otaku.terminal import CLEAR_SCREEN, user_block
from otaku.terminal.cursor import RowTracker, measure, terminal_width
from otaku.terminal.query import cursor_row

# Up N rows, to column 0, erase to the end of the screen.
_ERASE_UP = "\x1b[{}A\r\x1b[J"


class ScreenLedger:
    """Built once per session; the run loop, `submit`, and the playing
    commands talk to it. Everything inline happens on the REPL thread —
    the spinner and the pinned status row write straight to stdout and are
    cursor-neutral, so they stay outside the count by construction."""

    def __init__(self) -> None:
        # Rows the current submission's typed input still occupies on
        # screen (the run loop sets it; 0 when a shortcut key already
        # erased the prompt line, or without a terminal).
        self.typed_rows = 0
        self._stack: list[_Exchange] = []
        self._suppress_gap = False
        self._lead_blank = False  # a command's first write earns a blank
        self._reply = _ReplyWriter(self)
        self._cpr_dead = False  # the terminal never answered — stop asking

    # ---------- the exchange lifecycle ----------

    def echo_block(self, text: str, above: str = "") -> None:
        """Show `text` as the played-turn block: erase the typed input
        (`typed_rows`), print the block and the blank line under it, and
        open a new exchange. Until a reply writes, that blank doubles as
        the pre-prompt gap — a /you that answers nothing suppresses the
        loop's own. `above` is a marker line just printed (with its blank)
        over this echo — a fallback /regen's — riding the exchange the way
        an undo report rides its re-echo (see `restore_exchange`)."""
        self._lead_blank = False  # the typed line is replaced, not printed under
        width = terminal_width()
        block = user_block(text)
        erase = _ERASE_UP.format(self.typed_rows) if self.typed_rows else ""
        sys.stdout.write(f"{erase}{block}\n\n")
        sys.stdout.flush()
        entry = _Exchange(measure(block + "\n", width), width)
        if above:
            entry.lead = measure(above + "\n", width) + 1
        self._stack.append(entry)
        self._suppress_gap = True
        self.typed_rows = 0

    @property
    def reply(self) -> TextIO:
        """Where a reply prints. Everything inference shows inline goes
        through this writer; with no live exchange it just forwards."""
        return cast(TextIO, self._reply)

    def restore_exchange(self, block: str | None, reply: str | None, above: str = "") -> None:
        """Adopt an exchange a resume echo just printed — `block` and
        `reply` are the exact strings on screen, either possibly missing
        (an import's promptless tail, a reply whose prompt scrolled out of
        the echo) — so /undo and /regen can take shown turns back without
        having played them. `above` is an undo report line printed (with
        one blank under it) directly over this exchange: erasing the
        exchange takes it too, so the caller prints a fresh report in the
        same spot — the report refreshes rather than going stale.
        Measured, never printed: call for each echoed exchange, oldest
        first, right after the echo — and only when the echo is the
        flow's last output."""
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
        """Wipe the visible screen (/clear): everything erased, the cursor
        home — scrollback stays. The stack empties with it, and the
        suppressed gap puts the next prompt flush with the top."""
        self._lead_blank = False  # nothing prints under a wiped typed line
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.flush()
        self._stack.clear()
        self._suppress_gap = True

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
        command and the standing gap above the cursor, then the reply, the
        blank, and the block. True when erased — the caller prints
        nothing; False means fall back to reporting (and the fallback
        print must invalidate)."""
        entry = self._top()
        if entry is None or not self._erase(self.typed_rows + 1 + entry.rows, entry):
            return False
        self._stack.pop()
        self._suppress_gap = True
        self.typed_rows = 0
        return True

    def erase_reply(self) -> bool:
        """Take the last reply off the screen (/regen), leaving everything
        above it: the cursor lands where the reply began and the new one
        streams in its place. False — no reply rows on screen (a /you
        that answered nothing) or an unprovable erase — means fall back
        to the marker, which invalidates like any command output."""
        entry = self._top()
        if entry is None or entry.tracker.rows == 0:
            return False
        if not self._erase(self.typed_rows + 1 + entry.tracker.rows, entry):
            return False
        entry.tracker.reset()
        self.typed_rows = 0
        return True

    def erase_reply_tail(self) -> bool:
        """Take the current, just-interrupted reply off the screen (the
        in-stream Ctrl+R): the cursor sits right under it — no typed line,
        no gap. False means the caller prints the regenerating marker
        instead, through `reply`, so the exchange keeps covering it."""
        entry = self._top()
        if entry is None or entry.tracker.rows == 0:
            return False
        if not self._erase(entry.tracker.rows, entry):
            return False
        entry.tracker.reset()
        return True

    # ---------- the dispatch window ----------

    @contextlib.contextmanager
    def command_output(self) -> Iterator[None]:
        """Around a dispatched command: its first inline write earns one
        blank line under the typed command line — the same separation a
        reply gets under its block. Armed only while the typed line is
        still on screen (`typed_rows`); a command that prints nothing
        leaves no blank. On a crash the armed flag survives the window,
        so the crash report gets the same separation."""
        self._lead_blank = self.typed_rows > 0
        wrapper = _LeadBlankWriter(self, sys.stdout)
        sys.stdout = cast(TextIO, wrapper)
        try:
            yield
            self._lead_blank = False  # nothing printed — nothing to lead
        finally:
            sys.stdout = wrapper.wrapped

    def take_lead_blank(self) -> bool:
        """Consume the armed lead blank — whichever write comes first
        (the dispatch wrapper, an absorbed marker, the crash report)
        asks, and only the first gets True."""
        taken = self._lead_blank
        self._lead_blank = False
        return taken

    # ---------- the run loop's contract ----------

    def invalidate(self) -> None:
        """The stack no longer describes the screen — something printed
        inline past the ledger. Called by dispatch for every non-playing
        command, by every fallback print site, and by the run loop on a
        KeyboardInterrupt."""
        self._stack.clear()
        self._suppress_gap = False

    def take_suppressed_gap(self) -> bool:
        """True once when the screen already ends in the standing blank —
        after an erased /undo, or an echo no reply followed — so the run
        loop skips its own between-submissions blank line."""
        taken = self._suppress_gap
        self._suppress_gap = False
        return taken

    # ---------- internals ----------

    def _top(self) -> "_Exchange | None":
        return self._stack[-1] if self._stack else None

    def _erase(self, rows: int, entry: "_Exchange") -> bool:
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
        sys.stdout.write(_ERASE_UP.format(rows))
        sys.stdout.flush()
        return True

    def _reply_written(self, text: str) -> None:
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
    above the block, erased with the exchange and reprinted fresh."""

    __slots__ = ("block", "internal", "lead", "tracker", "width")

    def __init__(self, block: int, width: int) -> None:
        self.block = block
        self.internal = 0
        self.lead = 0
        self.tracker = RowTracker(width)
        self.width = width

    @property
    def rows(self) -> int:
        return self.lead + self.block + self.internal + self.tracker.rows


class _ReplyWriter:
    """The file-like a reply prints through: feeds the live exchange's
    tracker (a no-op between exchanges), forwards to stdout looked up per
    write — so the run loop's output tracker and the dispatch window still
    see the bytes — and flushes on newlines the way a line-buffered
    stream would."""

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


class _LeadBlankWriter:
    """A sys.stdout stand-in for the dispatch window: the first non-empty
    write triggers the armed lead blank. Everything else passes through
    (`__getattr__`), so trackers and ttys behave unchanged."""

    def __init__(self, ledger: ScreenLedger, wrapped: TextIO) -> None:
        self._ledger = ledger
        self.wrapped = wrapped

    def write(self, text: str) -> int:
        if text and self._ledger.take_lead_blank():
            self.wrapped.write("\n")
        return self.wrapped.write(text)

    def flush(self) -> None:
        self.wrapped.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)
