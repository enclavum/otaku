"""History picker built on prompt_toolkit.

A single full-screen Application with two views — conversation list and
turn-by-turn view — switched internally so transitions are flicker-free.

Visual spec:

  Picker 1 (conversation list, with preview pane on the right that spans
  the full screen height):

    Left side (items pane shrinks to fit the longest row, so the
    horizontal gap to the preview border is exactly 3 chars):
        Conversations (N)                       bold #303030
        <blank>
          > 05-02 16:55 ·    6 msg · summary…   selected: bold,
                                                 bg #e4e4e4 fg #000000
          ...                                    others: fg #000000 bg #ffffff
        <blank>
        type to filter · ↑/↓ navigate · ...     #767676

    Right side (single-line border #767676, 2-col inner padding,
    blank row top/bottom inside border, full-height):
        ┌──────────────────┐
        │                  │
        │  model name      │   bold #303030
        │                  │
        │  Sat 2026-... · …│   #767676
        │                  │
        │  summary text…   │
        │                  │
        │  first prompt:   │   #767676
        │  prompt text…    │
        │                  │
        └──────────────────┘

  Picker 2 (turn list, no preview pane):
        qwen3-coder:30b · 2026-05-01 21:08 · 2 messages   bold #303030
        <blank>
          >  1. [user] write a p…
          ...
        <blank>
        ↑/↓ navigate · enter resume from this turn · ...  #767676
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.pickers._widgets import BASE_STYLE, KEY_NO, KEY_YES, bordered_box, page_step, text_line
from otaku.storage.store import Conversation, Message, Store
from otaku.text import flatten, truncate

# Light palette per the spec — shared chrome from BASE_STYLE plus the
# row + preview overrides this picker needs.
_STYLE = Style.from_dict(
    {
        **BASE_STYLE,
        "row": "fg:#000000 bg:#ffffff",
        "row.selected": "bold fg:#000000 bg:#e4e4e4",
        "preview.title": "bold fg:#303030 bg:#ffffff",
        "preview.muted": "fg:#767676 bg:#ffffff",
        "preview.body": "fg:#000000 bg:#ffffff",
    }
)

PREVIEW_OUTER_GAP = 3  # cols between items pane and preview border
PREVIEW_INNER_PAD = 2  # cols inside the border, both sides
MIN_PREVIEW_WIDTH = 60  # bound items width so the preview never collapses

# Shown when a conversation has no title (/title), no summary, and no user
# message to fall back to (e.g. system-only snapshots).
_UNTITLED = "(untitled)"


def _list_label(conv: Conversation) -> str:
    """One-line label for a conversation row: '<title> / <summary>' when both
    exist, else whichever is present, else the first prompt, else a
    placeholder."""
    if conv.title and conv.summary:
        return f"{conv.title} / {conv.summary}"
    return conv.title or conv.summary or conv.first_user or _UNTITLED


class HistoryPicker:
    def __init__(
        self,
        store: Store,
        convs: list[Conversation],
        initial_id: UUID | None = None,
    ) -> None:
        self.store = store
        self.all: list[Conversation] = list(convs)
        self.filtered: list[Conversation] = list(convs)
        # Full message text per conversation, built lazily on the first search
        # keystroke so filtering matches buried content, not just summary/first.
        self._texts: dict[UUID, str] | None = None
        self.in_filter: bool = False
        self.query: str = ""
        self.list_cursor: int = 0
        if initial_id is not None:
            for i, c in enumerate(self.all):
                if c.id == initial_id:
                    self.list_cursor = i
                    break
        self.list_cursor_saved: int = self.list_cursor

        self.in_turns: bool = False
        self.selected_conv: Conversation | None = None
        self.loaded_msgs: list[Message] = []
        self.turn_cursor: int = 0

        self.confirming_delete: bool = False

        self.result: tuple[UUID, list[Message], int] | None = None
        self.app = self._build_app()

    def run(self) -> tuple[UUID, list[Message], int] | None:
        if not self.all:
            return None
        self.app.run()
        return self.result

    # ---------- text content for each pane ----------

    def _header_text(self) -> StyleAndTextTuples:
        if self.in_turns and self.selected_conv is not None:
            c = self.selected_conv
            ts = c.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            return [("class:header", f" {c.model} · {ts} · {c.num_turns} messages")]
        n, total = len(self.filtered), len(self.all)
        label = f"Conversations ({n} of {total})" if n != total else f"Conversations ({n})"
        return [("class:header", " " + label)]

    def _filter_text(self) -> StyleAndTextTuples:
        if not self.in_filter or self.in_turns:
            return [("", "")]
        return [
            ("class:filter", "  filter: "),
            ("class:filter.query", self.query),
        ]

    def _confirm_text(self) -> StyleAndTextTuples:
        return [
            ("class:dialog.title", "Delete this conversation?\n"),
            ("class:dialog.body", "\n"),
            ("class:dialog.muted", "y to confirm     n / esc to cancel"),
        ]

    def _items_text(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if self.in_turns:
            if not self.loaded_msgs:
                out.append(("class:muted", "  (no messages)"))
                return out
            role_w = len("assistant")  # widest role name
            # prefix(4) + idx(4) + " · "(3) + role(role_w) + " · "(3) = fixed
            fixed = 4 + 4 + 3 + role_w + 3
            avail = max(10, self._max_row_content_width() - fixed)
            for i, m in enumerate(self.loaded_msgs):
                head = truncate(flatten(m.content), avail) or "(empty)"
                row = f"{i + 1:>4} · {m.role:<{role_w}} · {head}"
                self._emit_row(out, i == self.turn_cursor, row)
        else:
            if not self.filtered:
                msg = (
                    "(no matches)"
                    if self.query
                    else "(none yet — start chatting and they'll show up here)"
                )
                out.append(("class:muted", "  " + msg))
                return out
            avail = self._max_row_content_width() - 4 - len("MM-DD HH:MM ·    N msg · ")
            for i, c in enumerate(self.filtered):
                ts = c.updated_at.astimezone().strftime("%m-%d %H:%M")
                head = truncate(flatten(_list_label(c)), max(10, avail))
                row = f"{ts} · {c.num_turns:>4} msg · {head}"
                self._emit_row(out, i == self.list_cursor, row)
        return out

    @staticmethod
    def _emit_row(out: StyleAndTextTuples, selected: bool, row: str) -> None:
        prefix = "  > " if selected else "    "
        style = "class:row.selected" if selected else "class:row"
        out.append((style, prefix + row + "\n"))

    def _preview_text(self) -> StyleAndTextTuples:
        width = max(10, self._preview_inner_width())

        if self.in_turns:
            if not self.loaded_msgs:
                return [("class:preview.muted", "nothing to preview")]
            m = self.loaded_msgs[self.turn_cursor]
            out: StyleAndTextTuples = [
                ("class:preview.title", f"{self.turn_cursor + 1}. {m.role}\n"),
                ("class:preview.body", "\n"),
            ]
            for line in _wrap(m.content, width):
                out.append(("class:preview.body", line + "\n"))
            return out

        if not self.filtered:
            return [("class:preview.muted", "nothing to preview")]
        c = self.filtered[self.list_cursor]
        out = [
            ("class:preview.title", c.model + "\n"),
            ("class:preview.body", "\n"),
            (
                "class:preview.muted",
                c.updated_at.astimezone().strftime("%a %Y-%m-%d %H:%M")
                + " · "
                + _human_age(c.updated_at)
                + "\n",
            ),
            ("class:preview.body", "\n"),
        ]
        # Title row (if any) before the summary, separated by a blank line;
        # fall back to the summary alone, then a placeholder when neither exists.
        if c.title:
            for line in _wrap(flatten(c.title), width):
                out.append(("class:preview.title", line + "\n"))
            if c.summary:
                out.append(("class:preview.body", "\n"))
                for line in _wrap(c.summary, width):
                    out.append(("class:preview.body", line + "\n"))
        elif c.summary:
            for line in _wrap(c.summary, width):
                out.append(("class:preview.body", line + "\n"))
        else:
            out.append(("class:preview.body", _UNTITLED + "\n"))
        if c.first_user:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "first prompt:\n"))
            for line in _wrap(flatten(c.first_user), width):
                out.append(("class:preview.body", line + "\n"))
        return out

    def _help_text(self) -> StyleAndTextTuples:
        if self.in_turns:
            return [
                (
                    "class:help",
                    " ↑/↓ navigate · enter resume from this turn · esc back · ctrl+c quit",
                )
            ]
        if self.in_filter:
            return [
                (
                    "class:help",
                    " type to filter · ↑/↓ navigate · enter drill in · esc clear filter",
                )
            ]
        return [
            (
                "class:help",
                " ↑/↓ navigate · / filter · enter drill in · del delete · esc quit",
            )
        ]

    # ---------- width helpers ----------

    def _term_cols(self) -> int:
        try:
            return get_app().output.get_size().columns
        except Exception:
            return 120

    def _max_row_content_width(self) -> int:
        """Cap row content (prefix + body) so the preview always has at
        least MIN_PREVIEW_WIDTH cols available after the 3-char gap.
        """
        return max(20, self._term_cols() - MIN_PREVIEW_WIDTH - PREVIEW_OUTER_GAP)

    def _preview_inner_width(self) -> int:
        """Approx width of the preview pane's text area. Computed against
        the longest items row at render time so we wrap to roughly the
        right size; off-by-one due to layout is fine.
        """
        cols = self._term_cols()
        # Items pane shrinks to its content via dont_extend_width on
        # items_window — we don't know the exact width here, so estimate
        # based on the configured cap.
        items_cap = self._max_row_content_width()
        outer = max(MIN_PREVIEW_WIDTH, cols - items_cap - PREVIEW_OUTER_GAP)
        return max(10, outer - 2 - PREVIEW_INNER_PAD * 2)

    # ---------- behavior ----------

    def _refilter(self) -> None:
        q = self.query.strip().lower()
        if not q:
            self.filtered = list(self.all)
        else:
            if self._texts is None:  # build the full-content index once, on demand
                try:
                    self._texts = self.store.conversation_texts()
                except Exception:
                    self._texts = {}
            texts = self._texts
            self.filtered = [
                c
                for c in self.all
                if q in (c.summary + " " + c.first_user + " " + c.model).lower()
                or q in texts.get(c.id, "")
            ]
        if self.list_cursor >= len(self.filtered):
            self.list_cursor = max(0, len(self.filtered) - 1)

    def _items_count(self) -> int:
        return len(self.loaded_msgs) if self.in_turns else len(self.filtered)

    def _cursor(self) -> int:
        return self.turn_cursor if self.in_turns else self.list_cursor

    def _move_cursor(self, delta: int) -> None:
        new = max(0, min(self._items_count() - 1, self._cursor() + delta))
        if self.in_turns:
            self.turn_cursor = new
        else:
            self.list_cursor = new

    def _request_delete(self) -> None:
        if self.in_turns or self.confirming_delete or not self.filtered:
            return
        if self.store.read_only:
            return  # no-record session — store deletes are no-ops; don't pretend
        self.confirming_delete = True

    def _do_delete(self) -> None:
        if not self.filtered:
            self.confirming_delete = False
            return
        target = self.filtered[self.list_cursor]
        try:
            self.store.delete_conversation(target.id)
        except Exception:
            # Silent failure is acceptable here — the row stays visible
            # and the user can try again or check the DB out of band.
            self.confirming_delete = False
            return
        self.all = [c for c in self.all if c.id != target.id]
        self._refilter()
        self.confirming_delete = False

    def _enter(self) -> None:
        if self.in_turns:
            if not self.loaded_msgs or self.selected_conv is None:
                return
            self.result = (
                self.selected_conv.id,
                self.loaded_msgs[: self.turn_cursor + 1],
                len(self.loaded_msgs),
            )
            get_app().exit()
            return

        if not self.filtered:
            return
        self.list_cursor_saved = self.list_cursor
        self.selected_conv = self.filtered[self.list_cursor]
        try:
            self.loaded_msgs = self.store.load_conversation(self.selected_conv.id)
        except Exception:
            self.loaded_msgs = []
        self.turn_cursor = max(0, len(self.loaded_msgs) - 1)
        self.in_turns = True

    def _escape(self) -> None:
        if self.in_turns:
            self.in_turns = False
            self.loaded_msgs = []
            self.selected_conv = None
            self.list_cursor = self.list_cursor_saved
            return
        if self.in_filter:
            self.in_filter = False
            self.query = ""
            self._refilter()
            return
        get_app().exit()

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()

        @kb.add("up")
        def _up(event: Any) -> None:
            if self.confirming_delete:
                return
            self._move_cursor(-1)

        @kb.add("down")
        def _down(event: Any) -> None:
            if self.confirming_delete:
                return
            self._move_cursor(1)

        @kb.add("pageup")
        def _pgup(event: Any) -> None:
            if self.confirming_delete:
                return
            self._move_cursor(-page_step())

        @kb.add("pagedown")
        def _pgdn(event: Any) -> None:
            if self.confirming_delete:
                return
            self._move_cursor(page_step())

        @kb.add("home")
        def _home(event: Any) -> None:
            if self.confirming_delete:
                return
            if self.in_turns:
                self.turn_cursor = 0
            else:
                self.list_cursor = 0

        @kb.add("end")
        def _end(event: Any) -> None:
            if self.confirming_delete:
                return
            if self.in_turns:
                self.turn_cursor = max(0, len(self.loaded_msgs) - 1)
            else:
                self.list_cursor = max(0, len(self.filtered) - 1)

        @kb.add("enter")
        def _enter_key(event: Any) -> None:
            if self.confirming_delete:
                return  # require explicit y to confirm
            self._enter()

        @kb.add("escape", eager=True)
        def _esc(event: Any) -> None:
            if self.confirming_delete:
                self.confirming_delete = False
                return
            self._escape()

        @kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        @kb.add("backspace")
        def _bs(event: Any) -> None:
            if self.confirming_delete or self.in_turns or not self.in_filter:
                return
            if not self.query:
                self.in_filter = False
                self._refilter()
                return
            self.query = self.query[:-1]
            self._refilter()

        @kb.add("delete")
        def _delete_key(event: Any) -> None:
            self._request_delete()

        @kb.add(Keys.Any)
        def _any(event: Any) -> None:
            data = event.data
            if self.confirming_delete:
                key = data.lower() if data else ""
                if key in KEY_YES:
                    self._do_delete()
                elif key in KEY_NO:
                    self.confirming_delete = False
                return
            if self.in_turns:
                return
            if not data or len(data) != 1 or not data.isprintable():
                return
            if not self.in_filter:
                if data == "/":
                    self.in_filter = True
                    self.query = ""
                    self._refilter()
                return
            self.query += data
            self._refilter()

        items_window = Window(
            content=FormattedTextControl(
                text=self._items_text,
                get_cursor_position=lambda: Point(0, self._cursor()),
                show_cursor=False,
            ),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_width=True,
            style="class:row",
        )

        # Left pane is the chrome around the items list.
        left_pane = HSplit(
            [
                text_line(self._header_text, style="class:header"),
                Window(height=1, char=" ", always_hide_cursor=True),
                text_line(
                    self._filter_text,
                    filter=Condition(lambda: self.in_filter and not self.in_turns),
                ),
                items_window,
                Window(height=1, char=" ", always_hide_cursor=True),
                text_line(self._help_text, style="class:help"),
            ]
        )

        gap = Window(width=PREVIEW_OUTER_GAP, char=" ", always_hide_cursor=True)

        preview_pane = bordered_box(
            FormattedTextControl(text=self._preview_text, show_cursor=False),
            width=D(weight=1),
            style="class:preview.body",
            inner_pad=PREVIEW_INNER_PAD,
        )

        confirm_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._confirm_text, show_cursor=False),
                width=D(min=44, max=70, preferred=60),
                height=D.exact(7),
                style="class:dialog.body",
                border_style="class:dialog.border",
            ),
            filter=Condition(lambda: self.confirming_delete),
        )

        root = FloatContainer(
            content=VSplit([left_pane, gap, preview_pane]),
            floats=[Float(content=confirm_dialog)],
        )
        layout = Layout(root)

        app: Application[None] = Application(
            layout=layout,
            key_bindings=kb,
            style=_STYLE,
            mouse_support=False,
            full_screen=True,
            enable_page_navigation_bindings=False,
        )
        # Snappy ESC: defaults are 1.0s/0.5s and feel laggy.
        app.timeoutlen = 0.05
        app.ttimeoutlen = 0.05
        return app


def pick_history(
    store: Store, initial_id: UUID | None = None
) -> tuple[UUID, list[Message], int] | None:
    """Show the conversation+turn picker. `initial_id` pre-selects the
    matching row when set (e.g. the conversation already loaded in the
    REPL). Returns (conv_id, truncated_messages, total_messages) on a
    confirmed selection, or None when the user cancels (Esc/Ctrl+C).
    """
    convs = store.list_conversations(limit=None)  # all — nothing silently unreachable
    return HistoryPicker(store, convs, initial_id=initial_id).run()


# ---------- helpers ----------


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    out: list[str] = []
    for line in text.splitlines() or [""]:
        if not line:
            out.append("")
            continue
        wrapped = textwrap.wrap(
            line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )
        out.extend(wrapped or [""])
    return out


def _human_age(t: datetime) -> str:
    delta = datetime.now(UTC) - t.astimezone(UTC)
    sec = delta.total_seconds()
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    return f"{int(sec // 86400)}d ago"
