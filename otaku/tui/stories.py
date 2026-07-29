"""Story browser — opened by `/stories`.

A single full-screen Application with two views — the story list and a
message-by-message view of one story — switched internally so transitions
are flicker-free. Both views are the same list-plus-preview layout; only
the split changes.

  View 1 (story list) — list and preview share the width 50/50:
        Stories (N)                    │ ┌────────────────┐
        <blank>                        │ │  model name    │  bold #303030
          > 05-02 16:55 · 6 msg · t…   │ │  Sat … · 2h ago│  #767676
          ...                          │ │  arc text…     │
        <blank>                        │ │  first prompt: │  #767676
        type to filter · ↑/↓ · …       │ │  prompt text…  │
                                       │ └────────────────┘

  View 2 (message list) — list gets 2/3, preview 1/3:
        Story: The Long Road · 12 messages           bold #303030
        <blank>
          >  1. [user] I push the d…   │ ┌────────────┐
          ...                          │ │  1. user   │  the selected
        <blank>                        │ │  <content> │  message in full,
        ↑/↓ · enter resume · …         │ │  omlx/big  │  model dimmed,
                                       │ └────────────┘  right-aligned

A row's label is the story's title, else its newest story-so-far rollup,
else its first prompt. `/` filters; in the story list the filter also
matches full message content, indexed lazily on the first keystroke.
Enter drills in; Enter on a message hands the selection back to the
caller, which owns what resuming mid-story means. `e` edits a message in
place; Del deletes a story after a confirm.
"""

from dataclasses import replace
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import (
    ConditionalKeyBindings,
    KeyBindings,
    merge_key_bindings,
)
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import ConditionalContainer, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.formatting import combine_framing, flatten, human_age, truncate
from otaku.store import Store
from otaku.store.schema import Message
from otaku.store.stories import StoryListing
from otaku.terminal import latin_key
from otaku.tui.screen import BASE_STYLE, ListScreen, bordered_box, wrap_text

# Light palette — shared chrome from BASE_STYLE plus the row + preview
# overrides this browser needs.
_STYLE = Style.from_dict(
    {
        **BASE_STYLE,
        "row": "fg:#000000 bg:#ffffff",
        "row.selected": "bold fg:#000000 bg:#e4e4e4",
        "preview.title": "bold fg:#303030 bg:#ffffff",
        "preview.muted": "fg:#767676 bg:#ffffff",
        "preview.body": "fg:#000000 bg:#ffffff",
        "notice": "fg:#767676 bg:#ffffff",
    }
)

# List-to-preview split, list:preview. The story list gets an even split so
# the preview has room for the arc; the message view gives the list twice the
# preview, since the rows carry the content and the preview only echoes one.
_STORY_SPLIT = (1, 1)
_TURN_SPLIT = (2, 1)


def _label(row: StoryListing) -> str:
    """A story's one-line label: title, else the story so far, else the
    first prompt."""
    return row.title or row.story_so_far or row.first_user


class StoryPicker(ListScreen):
    def __init__(
        self,
        store: Store,
        rows: list[StoryListing],
        initial_story: int | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.all: list[StoryListing] = list(rows)
        self.filtered: list[StoryListing] = list(rows)
        # Full message text per story, built lazily on the first search
        # keystroke so filtering matches buried content, not just the labels.
        self._texts: dict[int, str] | None = None
        # in_filter/query are shared by both views; stash the story-list filter
        # while drilled into messages so returning restores it verbatim.
        self._story_filter: tuple[bool, str] = (False, "")
        self.list_cursor: int = 0
        if initial_story is not None:
            for i, row in enumerate(self.all):
                if row.id == initial_story:
                    self.list_cursor = i
                    break
        self.list_cursor_saved: int = self.list_cursor

        self.in_turns: bool = False
        self.selected_story: StoryListing | None = None
        self.loaded_msgs: list[Message] = []
        # Indices into loaded_msgs that match the message filter (all of them
        # when no query). turn_cursor indexes THIS list; resume maps back to
        # the original position so a filtered pick still truncates correctly.
        self.turn_filtered: list[int] = []
        self.turn_cursor: int = 0

        self.confirming_delete: bool = False

        # Inline message editing (`e` in the message view): while True the
        # preview body is the edit buffer and navigation is suspended.
        self.editing: bool = False
        self.edit_buffer = Buffer(multiline=True)

        self.result: tuple[int, list[Message], int] | None = None
        self.app = self._build_app()

    def run(self) -> tuple[int, list[Message], int] | None:
        if not self.all:
            return None
        self.app.run()
        return self.result

    # ---------- text content for each pane ----------

    def _header_text(self) -> StyleAndTextTuples:
        if self.in_turns and self.selected_story is not None:
            row = self.selected_story
            story = truncate(flatten(_label(row)), 50)
            prefix = f" Story: {story}" if story else " Story"
            return [("class:header", f"{prefix} · {row.num_messages} messages")]
        n, total = len(self.filtered), len(self.all)
        label = f"Stories ({n} of {total})" if n != total else f"Stories ({n})"
        return [("class:header", " " + label)]

    def _confirm_text(self) -> StyleAndTextTuples:
        return [
            ("class:dialog.title", "Delete this story?\n"),
            ("class:dialog.body", "(its messages, scenes, and cast go with it)\n"),
            ("class:dialog.muted", "y to confirm     n / esc to cancel"),
        ]

    def _items_text(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if self.in_turns:
            if not self.loaded_msgs:
                out.append(("class:muted", "  (no messages)"))
                return out
            if not self.turn_filtered:
                out.append(("class:muted", "  (no matches)"))
                return out
            role_w = len("assistant")  # widest role name
            # prefix(4) + idx(4) + " · "(3) + role(role_w) + " · "(3) = fixed
            fixed = 4 + 4 + 3 + role_w + 3
            avail = max(10, self._max_row_content_width() - fixed)
            for row_i, orig in enumerate(self.turn_filtered):
                m = self.loaded_msgs[orig]
                # The list shows the COMPOSED line — framing joined to body by
                # combine_framing, the turn as the model sees it. Bound the
                # body slice first: this renders per keystroke, and avail
                # chars never need more than a slice of a huge message.
                composed = combine_framing(m.body[: 4 * avail], m.framing)
                head = truncate(flatten(composed), avail) or "(empty)"
                # The original message number, so a filtered row still reads
                # as its true position in the story.
                row = f"{orig + 1:>4} · {m.role:<{role_w}} · {head}"
                self._emit_row(out, row_i == self.turn_cursor, row)
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
            for i, listing in enumerate(self.filtered):
                ts = listing.updated_at.astimezone().strftime("%m-%d %H:%M")
                head = truncate(flatten(_label(listing)), max(10, avail))
                line = f"{ts} · {listing.num_messages:>4} msg · {head}"
                self._emit_row(out, i == self.list_cursor, line)
        return out

    def _panel_header_text(self) -> StyleAndTextTuples:
        """The fixed header above the message text — its own window, so it
        stays put when the text below it becomes the edit buffer."""
        if not self.in_turns or not self.turn_filtered:
            return [("", "")]
        orig = self.turn_filtered[self.turn_cursor]
        return [("class:preview.title", f"{orig + 1}. {self.loaded_msgs[orig].role}\n")]

    def _preview_text(self) -> StyleAndTextTuples:
        width = max(10, self._preview_inner_width())

        if self.in_turns:
            if not self.turn_filtered:
                return [("class:preview.muted", "nothing to preview")]
            orig = self.turn_filtered[self.turn_cursor]
            m = self.loaded_msgs[orig]
            out: StyleAndTextTuples = []
            if m.body:
                for line in wrap_text(m.body, width):
                    out.append(("class:preview.body", line + "\n"))
            # The framing (a /me or /you direction, the /ooc note) shown DIM
            # after a blank line — the raw template layer (its `{body}`
            # placeholder and all) that combine_framing joins to the body to
            # make the composed line on the left / the wire.
            if m.framing:
                if m.body:
                    out.append(("class:preview.body", "\n"))
                for line in wrap_text(m.framing, width):
                    out.append(("class:preview.muted", line + "\n"))
            # The model that generated THIS turn, dimmed and right-aligned —
            # user turns have none (messages.model is NULL there) and show
            # nothing.
            if m.role == "assistant" and m.model:
                label = f"{m.provider}/{m.model}" if m.provider else m.model
                out.append(("class:preview.body", "\n"))
                out.append(("class:preview.muted", label.rjust(width) + "\n"))
            return out

        if not self.filtered:
            return [("class:preview.muted", "nothing to preview")]
        row = self.filtered[self.list_cursor]
        out = [
            ("class:preview.title", (row.model or "?") + "\n"),
            ("class:preview.body", "\n"),
            (
                "class:preview.muted",
                row.updated_at.astimezone().strftime("%a %Y-%m-%d %H:%M")
                + " · "
                + human_age(row.updated_at)
                + "\n",
            ),
        ]
        # Title (if any) before the arc, each block separated by a blank
        # line; a story with neither simply shows nothing there.
        if row.title:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(flatten(row.title), width):
                out.append(("class:preview.title", line + "\n"))
        if row.story_so_far:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(row.story_so_far, width):
                out.append(("class:preview.body", line + "\n"))
        if row.first_user:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "first prompt:\n"))
            for line in wrap_text(flatten(row.first_user), width):
                out.append(("class:preview.body", line + "\n"))
        return out

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            return [("class:help", " editing — ctrl+s save · esc cancel")]
        if self.in_filter:
            action = "enter resume" if self.in_turns else "enter drill in"
            return [
                (
                    "class:help",
                    f" type to filter · ↑/↓ navigate · {action} · esc clear filter",
                )
            ]
        if self.in_turns:
            return [
                (
                    "class:help",
                    " ↑/↓ navigate · / filter · e edit · enter resume from this turn · esc back",
                )
            ]
        return [
            (
                "class:help",
                " ↑/↓ navigate · / filter · enter drill in · del delete · esc quit",
            )
        ]

    # ---------- behavior ----------

    def _split(self) -> tuple[int, int]:
        """(left weight, preview weight) for the current view."""
        return _TURN_SPLIT if self.in_turns else _STORY_SPLIT

    def _refilter(self) -> None:
        if self.in_turns:
            self._refilter_turns()
            return
        q = self.query.strip().lower()
        if not q:
            self.filtered = list(self.all)
        else:
            if self._texts is None:  # build the full-content index once, on demand
                try:
                    self._texts = self.store.stories.get_texts()
                except Exception:
                    self._texts = {}
            texts = self._texts
            self.filtered = [
                row
                for row in self.all
                if q in f"{row.title} {row.story_so_far} {row.first_user} {row.model}".lower()
                or q in texts.get(row.id, "")
            ]
        if self.list_cursor >= len(self.filtered):
            self.list_cursor = max(0, len(self.filtered) - 1)

    def _refilter_turns(self) -> None:
        q = self.query.strip().lower()
        if not q:
            self.turn_filtered = list(range(len(self.loaded_msgs)))
        else:
            self.turn_filtered = [i for i, m in enumerate(self.loaded_msgs) if q in m.body.lower()]
        if self.turn_cursor >= len(self.turn_filtered):
            self.turn_cursor = max(0, len(self.turn_filtered) - 1)

    def _rows_count(self) -> int:
        return len(self.turn_filtered) if self.in_turns else len(self.filtered)

    def _cursor(self) -> int:
        return self.turn_cursor if self.in_turns else self.list_cursor

    def _set_cursor(self, value: int) -> None:
        if self.in_turns:
            self.turn_cursor = value
        else:
            self.list_cursor = value

    def _move_cursor(self, delta: int) -> None:
        self.notice = ""
        super()._move_cursor(delta)

    def _request_delete(self) -> None:
        if self.in_turns or self.confirming_delete or not self.filtered:
            return
        self.confirming_delete = True

    def _do_delete(self) -> None:
        if not self.filtered:
            self.confirming_delete = False
            return
        target = self.filtered[self.list_cursor]
        try:
            self.store.stories.delete(target.id)
        except Exception:
            # Silent failure is acceptable here — the row stays visible
            # and the user can try again or check the DB out of band.
            self.confirming_delete = False
            return
        self.all = [row for row in self.all if row.id != target.id]
        self._refilter()
        self.confirming_delete = False

    def _on_enter(self) -> None:
        if self.in_turns:
            if not self.turn_filtered or self.selected_story is None:
                return
            orig = self.turn_filtered[self.turn_cursor]
            self.result = (
                self.selected_story.id,
                self.loaded_msgs[: orig + 1],
                len(self.loaded_msgs),
            )
            get_app().exit()
            return

        if not self.filtered:
            return
        self.list_cursor_saved = self.list_cursor
        self.selected_story = self.filtered[self.list_cursor]
        try:
            self.loaded_msgs = self.store.stories.get_messages(self.selected_story.id)
        except Exception:
            self.loaded_msgs = []
        # Fresh view: stash the story-list filter and start unfiltered on the
        # tail. Escape restores it verbatim on the way back.
        self._story_filter = (self.in_filter, self.query)
        self.in_filter = False
        self.query = ""
        self.turn_filtered = list(range(len(self.loaded_msgs)))
        self.turn_cursor = max(0, len(self.turn_filtered) - 1)
        self.in_turns = True

    def _on_escape(self) -> None:
        self.notice = ""
        # In either view an active filter clears first; a second Esc backs out.
        if self._clear_filter():
            return
        if self.in_turns:
            self.in_turns = False
            self.loaded_msgs = []
            self.turn_filtered = []
            self.selected_story = None
            self.in_filter, self.query = self._story_filter
            self.list_cursor = self.list_cursor_saved
            return
        get_app().exit()

    def _on_key(self, data: str) -> None:
        if latin_key(data) == "e" and self.in_turns:
            self._start_edit()

    # ---------- inline message editing ----------

    def _start_edit(self) -> None:
        """`e` on a message: the text under the header becomes the buffer."""
        if not self.in_turns or not self.turn_filtered:
            self.notice = "nothing to edit"
            return
        orig = self.turn_filtered[self.turn_cursor]
        text = self.loaded_msgs[orig].body
        self.notice = ""
        self.editing = True
        self.edit_buffer.document = Document(text, len(text))
        self.app.layout.focus(self._edit_control)

    def _finish_edit(self, *, save: bool) -> None:
        """Ctrl+S writes the corrected text; Esc discards it."""
        self.editing = False
        self.app.layout.focus(self._items_control)
        if not save:
            self.notice = "(cancelled)"
            return
        orig = self.turn_filtered[self.turn_cursor]
        m = self.loaded_msgs[orig]
        new = self.edit_buffer.text.rstrip("\n")
        if new == m.body:
            self.notice = "(unchanged)"
            return
        if not new.strip():
            self.notice = "(empty — not saved)"
            return
        try:
            self.store.messages.update(m.id, new)
        except Exception as e:
            self.notice = f"save failed: {e}"
            return
        self.loaded_msgs[orig] = replace(m, body=new)
        self._texts = None  # the content index is stale now
        self.notice = "saved"

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()
        confirming = Condition(lambda: self.confirming_delete)

        # While the confirm dialog is up: only y/n/esc do anything.
        @kb.add("escape", eager=True, filter=confirming)
        def _confirm_esc(event: Any) -> None:
            self.confirming_delete = False

        @kb.add(Keys.Any, filter=confirming, eager=True)
        def _confirm_any(event: Any) -> None:
            key = latin_key(event.data) if event.data else ""
            if key == "y":
                self._do_delete()
            elif key == "n":
                self.confirming_delete = False

        self._standard_keys(kb, when=~confirming)

        @kb.add("delete")
        def _delete_key(event: Any) -> None:
            self._request_delete()

        # While the buffer owns the panel, every binding above is suspended —
        # keystrokes belong to it. Only save/cancel and quit stay live.
        editing = Condition(lambda: self.editing)
        edit_kb = KeyBindings()

        @edit_kb.add("c-s", filter=editing)
        def _save(event: Any) -> None:
            self._finish_edit(save=True)

        @edit_kb.add("escape", filter=editing, eager=True)
        def _cancel(event: Any) -> None:
            self._finish_edit(save=False)

        always_kb = KeyBindings()

        @always_kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        bindings = merge_key_bindings([ConditionalKeyBindings(kb, ~editing), edit_kb, always_kb])

        # Focusable so focus has somewhere to return to when editing ends.
        self._items_control = self._make_items_control(focusable=True)
        items_window = Window(
            content=self._items_control,
            wrap_lines=False,
            always_hide_cursor=True,
            style="class:row",
        )
        # The left pane's width is a weighted share against the preview's —
        # 50/50 in the story list, 2:1 in the message view (re-evaluated
        # each render, so the split flips when the view does).
        left_pane = self._list_pane(
            items_window, width=lambda: D(weight=self._split()[0]), notice=True
        )

        self._edit_control = BufferControl(buffer=self.edit_buffer, focusable=True)
        preview_pane = self._preview_panel(
            header_filter=Condition(lambda: self.in_turns and bool(self.turn_filtered)),
            editing=editing,
            edit_window=Window(self._edit_control, wrap_lines=True, style="class:preview.body"),
        )

        confirm_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._confirm_text, show_cursor=False),
                width=D(min=44, max=70, preferred=60),
                height=D.exact(7),
                style="class:dialog.body",
                border_style="class:dialog.border",
            ),
            filter=confirming,
        )

        root = VSplit([left_pane, self._preview_gap(), preview_pane])
        return self._finish_app(root, bindings, _STYLE, floats=[confirm_dialog])


def pick(
    store: Store, rows: list[StoryListing], initial_story: int | None = None
) -> tuple[int, list[Message], int] | None:
    """Show the story browser over `rows`. `initial_story` pre-selects the
    matching row when set (the story already loaded in the REPL). Returns
    (story_id, its messages up to the picked turn, total turns) on a
    confirmed selection, or None when the user cancels (Esc/Ctrl+C)."""
    return StoryPicker(store, rows, initial_story=initial_story).run()
