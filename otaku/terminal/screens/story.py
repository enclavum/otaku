"""Inside one story: the dossier — premise, messages, scenes and cast as
four tabs of one full-screen surface, for ANY story, editing included.

←/→ cycle the tabs (wrapping) from anywhere outside an edit — Tab and
Shift+Tab do the same, unadvertised; each tab keeps its cursor and its
filter, while an open detail closes with the switch, so coming back
lands on the tab's list, cursor on the row that was drilled into. The
premise tab is the one full-width view — a premise is one text, so
there is no list beside it; Enter edits it in place. The messages tab
is the story browser's message view: Enter on the last message resumes,
an earlier one asks what resuming there means (fork — the default —
truncate, or cancel), and `e` corrects a message where it is read.
Scenes and cast are the two lenses over the memory: Enter opens an item
as a FIELD LIST — one row per text (`LoreView.scene_fields` /
`char_fields`; the view composes, this screen renders) — and Enter on a
field edits it IN PLACE (Ctrl+S saves, Esc cancels). A journal row is
the intersection of a scene and a character, so `o` pivots to the same
entry through the other tab.

Editable fields are the write-once primitives; the extractor's own —
states, and every history, the scene's own included — are shown dim
and refused. Every
write goes through `backend.api` with the story it belongs to; the
store writers carry the invalidation, and this screen never calls a
model. `/lore` and `/cast` open the dossier directly on the OPEN story
(scenes and cast tabs); the story browser (`screens.stories`) opens it
on any story, with its list waiting underneath.
"""

from dataclasses import dataclass, replace
from typing import Any, Literal

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, Filter
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import (
    ConditionalKeyBindings,
    KeyBindings,
    merge_key_bindings,
)
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.backend import Character, Message, Scene
from otaku.backend.api import lore as api_lore
from otaku.backend.api import stories as api_stories
from otaku.backend.api.lore import Field, LoreView
from otaku.backend.session import Refused, Session
from otaku.formatting import flatten, truncate
from otaku.terminal.screens.base import (
    ListScreen,
    ansi_fragments,
    base_style,
    bordered_box,
    page_step,
    text_line,
    wrap_text,
)
from otaku.terminal.tty import latin_key
from otaku.terminal.tty.render import message as render_message
from otaku.terminal.tty.render import pictures_note
from otaku.terminal.tty.theme import theme
from otaku.terminal.tty.typography import highlight_toml

Tab = Literal["premise", "messages", "scenes", "cast"]

_TABS: tuple[Tab, ...] = ("premise", "messages", "scenes", "cast")
_TAB_LABELS: dict[Tab, str] = {
    "premise": "Premise",
    "messages": "Messages",
    "scenes": "Scenes",
    "cast": "Cast",
}

# List-to-preview split, list:preview. The message rows carry the content
# and the preview only echoes one, so the list gets twice the width; the
# lens lists and their field details share it evenly.
_TURN_SPLIT = (2, 1)
_LENS_SPLIT = (1, 1)

# The resume dialog's rows, in order: (action, label). The action is what
# the picker returns; "cancel" closes the dialog and stays in the browser.
_RESUME_OPTIONS: tuple[tuple[Literal["cancel"] | api_stories.LandAction, str], ...] = (
    ("fork", "Fork — continue in a new story from here"),
    ("truncate", "Truncate — discard the messages after this one"),
    ("cancel", "Cancel"),
)


def _style() -> Style:
    """Shared chrome from `base_style` plus the dossier's row, tab and
    preview overrides, in the shades the terminal background asked for."""
    colors = theme()
    panel = f"bg:{colors.panel.style}"
    return Style.from_dict(
        {
            **base_style(),
            "row": f"fg:{colors.text.style} {panel}",
            "row.selected": f"bold fg:{colors.ink.style} bg:{colors.selection.style}",
            "row.dim": f"dim fg:{colors.muted.style} {panel}",
            "row.dim.selected": f"dim fg:{colors.ink.style} bg:{colors.selection.style}",
            "tab": f"dim fg:{colors.muted.style} {panel}",
            # The open tab is body text at full weight. `nodim` matters:
            # a dotted class inherits its parent's rules, so without it
            # the active tab keeps `tab`'s dim and bold lands on grey.
            "tab.active": f"nodim bold fg:{colors.text.style} {panel}",
            "preview.title": f"bold fg:{colors.title.style} {panel}",
            "preview.muted": f"dim fg:{colors.muted.style} {panel}",
            "preview.body": f"fg:{colors.text.style} {panel}",
            "notice": f"dim fg:{colors.muted.style} {panel}",
        }
    )


@dataclass
class _TabState:
    """What a tab keeps while another is in front: its cursor and its
    filter. An open detail is deliberately NOT kept — leaving the tab
    closes it, and coming back lands on the list."""

    cursor: int = 0
    in_filter: bool = False
    query: str = ""


class Dossier(ListScreen):
    """The four tabs over one story. Standalone it IS the whole screen
    (`browse` — Esc quits); the story browser subclasses it and puts its
    list level in front (`_leave_dossier` is the seam, with the `_extra_*`
    hooks for the list's own keys and dialogs)."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        # How a message body looks — chat's one renderer, reached from
        # below it.
        self._render = render_message

        # The dossier's subject; unset until `_open_dossier` (the picker
        # subclass starts on its list level instead).
        self.dossier_on: bool = False
        self.story_id: int = 0
        self.tab: Tab = "messages"
        self.tabs: dict[Tab, _TabState] = {}

        # The loaded story: its premise, its chain, its memory.
        self.premise: str = ""
        self.msgs: list[Message] = []
        self.view: LoreView | None = None

        # Per-tab row indices (the filters narrow these); the open
        # detail's field list.
        self.turn_filtered: list[int] = []
        self.scenes_f: list[int] = []
        self.cast_f: list[int] = []
        self.detail: tuple[str, int] | None = None
        self.fields: list[Field] = []
        self._list_pos: int = 0
        self._premise_rows: int = 1

        # The resume dialog (Enter on an earlier message): up or not, and
        # which _RESUME_OPTIONS row is highlighted (fork is the default).
        self.confirming_resume: bool = False
        self.resume_choice: int = 0

        # Inline editing: while True the text under the cursor — the
        # premise, a message, a field — is the edit buffer and every
        # navigation binding is suspended.
        self.editing: bool = False
        self.edit_buffer = Buffer(multiline=True)

        # The landing line api.stories.land answered; None until then.
        self.result: str | None = None
        # Built last: the layout is callables over the state above, and a
        # subclass's hooks are already its own (nothing renders until run).
        self.app = self._build_app()

    def run(self) -> str | None:
        self.app.run()
        return self.result

    # ---------- loading a story ----------

    def _open_dossier(self, story_id: int, tab: Tab) -> None:
        """Load the story whole — premise, chain, memory — and open on
        `tab`, every tab's state fresh (messages starts on the tail)."""
        self.story_id = story_id
        self.premise = api_stories.get_system(self.session, story_id)
        try:
            self.msgs = api_stories.messages_of(self.session, story_id)
        except Exception:
            self.msgs = []
        self.view = api_lore.view(self.session, story_id)
        self.turn_filtered = list(range(len(self.msgs)))
        self.scenes_f = list(range(len(self.view.scenes)))
        self.cast_f = list(range(len(self.view.cast)))
        self.tabs = {name: _TabState() for name in _TABS}
        self.tabs["messages"].cursor = max(0, len(self.turn_filtered) - 1)
        self.detail = None
        self.fields = []
        self.tab = tab
        state = self.tabs[tab]
        self.cursor, self.in_filter, self.query = state.cursor, False, ""
        self.notice = ""
        self.dossier_on = True

    def _reload(self) -> None:
        """The whole memory again, after a save — cheap for one story, and
        the store's own invalidation is never mirrored by hand. The
        visible sets are NOT recomputed: an edit that drops a row out of
        an open filter would otherwise take it away as it is saved."""
        self.view = api_lore.view(self.session, self.story_id)
        self._rebuild_fields()

    # ---------- view lookups ----------

    def _scene_by_id(self, scene_id: int) -> Scene | None:
        assert self.view is not None
        return next((s for s in self.view.scenes if s.id == scene_id), None)

    def _char_by_id(self, cid: int) -> Character | None:
        assert self.view is not None
        return next((c for c in self.view.cast if c.id == cid), None)

    def _latest_state(self, cid: int) -> Field | None:
        """The character's newest state row, as their field list shows it."""
        assert self.view is not None
        states = [f for f in self.view.char_fields(cid) if f.kind == "state"]
        return states[-1] if states else None

    def _history_field(self, cid: int) -> Field | None:
        assert self.view is not None
        return next((f for f in self.view.char_fields(cid) if f.kind == "history"), None)

    def _label_parts(self, scene_id: int) -> tuple[str, str, str]:
        """The label's three columns — number, span, title — as the view
        holds them (`scene_no`, `scene_span`), so the aligned list never
        has to fish a part back out of display text."""
        assert self.view is not None
        scene = self._scene_by_id(scene_id)
        title = flatten(scene.title) if scene is not None and scene.title else ""
        return str(self.view.scene_no(scene_id)), self.view.scene_span(scene_id), title

    def _rebuild_fields(self) -> None:
        assert self.view is not None
        if self.detail is None:
            self.fields = []
            return
        kind, target = self.detail
        if kind == "scene":
            self.fields = self.view.scene_fields(target)
        else:
            self.fields = self.view.char_fields(target)
        if self.cursor >= len(self.fields):
            self.cursor = max(0, len(self.fields) - 1)

    # ---------- the tabs ----------

    def _tabstrip_text(self) -> StyleAndTextTuples:
        """The four tab names on one row — the dossier's whole header,
        the active one bold in the text color, the rest dim."""
        out: StyleAndTextTuples = [("", " ")]
        for name in _TABS:
            style = "class:tab.active" if name == self.tab else "class:tab"
            out.append((style, f" {_TAB_LABELS[name]} "))
            out.append(("", " "))
        return out

    def _switch_tab(self, step: int) -> None:
        self.notice = ""
        self._stash_tab()
        self.tab = _TABS[(_TABS.index(self.tab) + step) % len(_TABS)]
        self._restore_tab()

    def _stash_tab(self) -> None:
        # A detail does not survive the switch: what is kept is the LIST
        # as it stood — cursor on the row that was drilled into.
        self.tabs[self.tab] = _TabState(
            cursor=self._list_pos if self.detail is not None else self.cursor,
            in_filter=self.in_filter,
            query=self.query,
        )

    def _restore_tab(self) -> None:
        state = self.tabs[self.tab]
        self.in_filter, self.query = state.in_filter, state.query
        self.detail = None
        self.fields = []
        self.cursor = min(state.cursor, max(0, self._rows_count() - 1))

    def _pivot(self) -> None:
        """`o` on a journal field: the same row through the OTHER tab —
        the letter, because the arrows walk the tab strip."""
        self.notice = ""
        if not self.dossier_on or self.detail is None or not self.fields:
            return
        f = self.fields[self.cursor]
        if f.pivot is None:
            return
        from_scene = self.detail[0] == "scene"
        self._stash_tab()
        self.tab = "cast" if from_scene else "scenes"
        self._restore_tab()
        self.detail = ("char", f.pivot) if from_scene else ("scene", f.pivot)
        self._rebuild_fields()
        assert self.view is not None
        # Where Esc lands afterwards: the pivoted-to row in its own list.
        if from_scene:
            listed = [self.view.cast[idx].id for idx in self.cast_f]
        else:
            listed = [self.view.scenes[idx].id for idx in self.scenes_f]
        self._list_pos = listed.index(f.pivot) if f.pivot in listed else 0
        # Land on the same journal row's same field, seen from the other side.
        for i, g in enumerate(self.fields):
            if g.kind == f.kind and g.target == f.target:
                self.cursor = i
                break
        else:
            self.cursor = 0

    # ---------- text content for each pane ----------

    def _header_text(self) -> StyleAndTextTuples:
        if self.detail is not None:
            kind, target = self.detail
            if kind == "scene":
                scene = self._scene_by_id(target)
                no = self._label_parts(target)[0]
                if scene is not None and scene.title:
                    return [("class:header", f" Scene {no}: {truncate(flatten(scene.title), 60)}")]
                return [("class:header", f" Scene {no}")]
            char = self._char_by_id(target)
            name = char.name if char else "?"
            aka = f" (aka {', '.join(char.aliases)})" if char and char.aliases else ""
            return [("class:header", f" {name}{aka}")]
        # At tab level the strip IS the header — the dossier is the lore
        # browser grown two tabs, and which one is open is all the top
        # row has to say.
        return self._tabstrip_text()

    def _items_text(self) -> StyleAndTextTuples:
        if self.tab == "premise":
            return self._premise_text()
        if self.tab == "messages":
            return self._turn_rows()
        if self.detail is not None:
            return self._field_rows()
        return self._scene_rows() if self.tab == "scenes" else self._cast_rows()

    def _premise_text(self) -> StyleAndTextTuples:
        """The premise as wrapped read-only lines. The cursor is only the
        scroll anchor — no row is selected, the arrows just move through
        a long text — so the rows carry no band."""
        width = max(10, self._max_row_content_width() - 4)
        if not self.premise:
            self._premise_rows = 1
            return [("class:preview.muted", "  (no premise yet — enter to write one)")]
        lines = wrap_text(self.premise, width)
        self._premise_rows = len(lines)
        return [("class:preview.body", "".join(f"  {line}\n" for line in lines))]

    def _turn_rows(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if not self.msgs:
            out.append(("class:muted", "  (no messages yet)"))
            return out
        if not self.turn_filtered:
            out.append(("class:muted", "  (no matches)"))
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
            # does not depend on where the cursor happens to be.
            head = self._render(head, m.role, pictures=pictures)
            # The original message number, so a filtered row still reads
            # as its true position in the story.
            row = f"{orig + 1:>4} · {m.role:<{role_w}} · {head}"
            self._emit_row(out, row_i == self.cursor, row)
        return out

    def _field_rows(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if not self.fields:
            out.append(("class:muted", "  (nothing here yet)"))
            return out
        # The char view's journal rows carry their scene's number as its
        # own column, enumerated like the scenes list; rows without one
        # (description, history) keep the column blank.
        nos = [f.scene_no for f in self.fields if f.scene_no is not None]
        no_w = max((len(str(n)) for n in nos), default=0)
        label_w = min(24, max(len(f.label) for f in self.fields))
        avail = max(10, self._max_row_content_width() - 4 - no_w - 2 - label_w - 3)
        for i, f in enumerate(self.fields):
            head = truncate(flatten(f.text[: 4 * avail]), avail) or "(empty)"
            no = f"{f.scene_no:>{no_w}}  " if f.scene_no is not None else " " * (no_w + 2)
            row = f"{no if no_w else ''}{truncate(f.label, label_w):<{label_w}} · {head}"
            self._emit_row(out, i == self.cursor, row, dim=not f.editable)
        return out

    def _scene_rows(self) -> StyleAndTextTuples:
        assert self.view is not None
        out: StyleAndTextTuples = []
        if not self.scenes_f:
            msg = "(no matches)" if self.query else "(no scenes yet — they close as you play)"
            out.append(("class:muted", "  " + msg))
            return out
        parts = [self._label_parts(self.view.scenes[i].id) for i in self.scenes_f]
        no_w = max(len(no) for no, _, _ in parts)
        span_w = max(len(span) for _, span, _ in parts)
        # prefix(4) + no + "  " + span + "  " — the row's own spacing.
        avail = max(10, self._max_row_content_width() - 4 - no_w - 2 - span_w - 2)
        for row_i, (no, span, title) in enumerate(parts):
            row = f"{no:>{no_w}}  {span:>{span_w}}  {truncate(flatten(title), avail)}"
            self._emit_row(out, row_i == self.cursor, row)
        return out

    def _cast_rows(self) -> StyleAndTextTuples:
        assert self.view is not None
        out: StyleAndTextTuples = []
        if not self.cast_f:
            msg = "(no matches)" if self.query else "(no characters yet)"
            out.append(("class:muted", "  " + msg))
            return out
        name_w = min(20, max(len(c.name) for c in self.view.cast))
        avail = max(10, self._max_row_content_width() - 4 - name_w - 3)
        for row_i, idx in enumerate(self.cast_f):
            c = self.view.cast[idx]
            latest = self._latest_state(c.id)
            snippet = f"now: {latest.text}" if latest else "(no journal yet)"
            row = f"{truncate(c.name, name_w):<{name_w}} · {truncate(flatten(snippet), avail)}"
            self._emit_row(out, row_i == self.cursor, row)
        return out

    def _panel_header_text(self) -> StyleAndTextTuples:
        """The fixed header above the panel's text. It lives in its own
        window so it stays put when the text below it becomes the edit
        buffer — editing happens exactly where the text is displayed."""
        if self.tab == "messages":
            if not self.turn_filtered:
                return [("", "")]
            orig = self.turn_filtered[self.cursor]
            return [("class:preview.title", f"{orig + 1}. {self.msgs[orig].role}\n")]
        if self.detail is not None and self.fields:
            return [("class:preview.title", self.fields[self.cursor].label + "\n")]
        return [("", "")]

    def _preview_text(self) -> StyleAndTextTuples:
        width = max(10, self._preview_inner_width())
        if self.tab == "messages":
            return self._turn_preview(width)
        if self.detail is not None:
            return self._field_preview(width)
        if self.tab == "scenes":
            return self._scene_preview(width)
        return self._cast_preview(width)

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
            body = self._render(m.body, m.role, pictures=len(m.attachments))
            if not body.endswith("\n"):
                body += "\n"
            out.extend(ansi_fragments(body, "class:preview.body"))
        # The template snapshot shown DIM after a blank line — the
        # template layer (its `{body}` placeholder and all) that the turn
        # was played with, which the body alone does not show. It is not
        # what the model reads; `/context` shows that.
        if m.template:
            if m.body:
                out.append(("class:preview.body", "\n"))
            for line in wrap_text(m.template, width):
                out.append(("class:preview.muted", line + "\n"))
        # The model that generated THIS turn, dimmed and right-aligned —
        # user turns have none (messages.model is NULL there) and show
        # nothing.
        if m.role == "assistant" and m.model:
            label = f"{m.provider}/{m.model}" if m.provider else m.model
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", label.rjust(width) + "\n"))
        return out

    def _field_preview(self, width: int) -> StyleAndTextTuples:
        if not self.fields:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        f = self.fields[self.cursor]
        if f.kind == "card" and f.text:
            # The archive reads as TOML: keys and macros in the command
            # color — highlighted after the wrap, so the widths stay
            # honest (escapes take no columns the wrap could count).
            wrapped = "\n".join(wrap_text(f.text, width))
            for line in highlight_toml(wrapped).split("\n"):
                out.extend(ansi_fragments(line, "class:preview.body"))
                out.append(("class:preview.body", "\n"))
            return out
        for line in wrap_text(f.text or "(empty)", width):
            out.append(("class:preview.body", line + "\n"))
        return out

    def _scene_preview(self, width: int) -> StyleAndTextTuples:
        assert self.view is not None
        if not self.scenes_f:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        s = self.view.scenes[self.scenes_f[self.cursor]]
        no, span, title = self._label_parts(s.id)
        out.append(("class:preview.title", (flatten(title) or f"scene {no}") + "\n"))
        out.append(("class:preview.muted", f"messages {span}\n"))
        out.append(("class:preview.body", "\n"))
        for line in wrap_text(s.summary or "(no summary)", width):
            out.append(("class:preview.body", line + "\n"))
        present = [
            c.name
            for c in self.view.cast
            if any(j.scene_id == s.id and j.character_id == c.id for j in self.view.journals)
        ]
        if present:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "present: " + ", ".join(present) + "\n"))
        return out

    def _cast_preview(self, width: int) -> StyleAndTextTuples:
        assert self.view is not None
        if not self.cast_f:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        c = self.view.cast[self.cast_f[self.cursor]]
        aka = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        out.append(("class:preview.title", f"{c.name}{aka}\n"))
        if c.description:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(c.description, width):
                out.append(("class:preview.body", line + "\n"))
        latest = self._latest_state(c.id)
        if latest is not None:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", f"now ({self.view.vintage(latest.target)}):\n"))
            for line in wrap_text(latest.text, width):
                out.append(("class:preview.body", line + "\n"))
            history = self._history_field(c.id)
            if history is not None:
                out.append(("class:preview.body", "\n"))
                out.append(("class:preview.muted", "so far (rebuilt from entries):\n"))
                for line in wrap_text(history.text, width):
                    out.append(("class:preview.muted", line + "\n"))
        else:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "(no journal yet)\n"))
        return out

    def _resume_text(self) -> StyleAndTextTuples:
        picked = self.turn_filtered[self.cursor] + 1 if self.turn_filtered else 0
        out: StyleAndTextTuples = [
            ("class:dialog.title", f"Resume at message {picked} of {len(self.msgs)}\n"),
            ("class:dialog.body", "\n"),
        ]
        for i, (_, label) in enumerate(_RESUME_OPTIONS):
            selected = i == self.resume_choice
            style = "class:row.selected" if selected else "class:dialog.body"
            out.append((style, f"{' > ' if selected else '   '}{label}\n"))
        out.append(("class:dialog.body", "\n"))
        out.append(("class:dialog.muted", "↑/↓ choose · enter confirm · esc cancel"))
        return out

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            return [("class:help", " editing — ctrl+s save · esc cancel")]
        if self.in_filter:
            action = "enter resume" if self.tab == "messages" else "enter open"
            return [
                (
                    "class:help",
                    f" type to filter · ↑/↓ navigate · {action} · esc clear filter",
                )
            ]
        back = self._esc_label()
        if self.detail is not None:
            other = "character" if self.detail[0] == "scene" else "scene"
            return [
                (
                    "class:help",
                    f" ↑/↓ fields · enter edit · o their {other} · ←/→ tabs · esc back",
                )
            ]
        if self.tab == "premise":
            return [("class:help", f" enter edit · ←/→ switch tab · esc {back}")]
        if self.tab == "messages":
            return [
                (
                    "class:help",
                    " ↑/↓ navigate · / filter · e edit · enter resume from this turn"
                    f" · ←/→ tabs · esc {back}",
                )
            ]
        return [
            (
                "class:help",
                f" ↑/↓ navigate · / filter · enter open · ←/→ tabs · esc {back}",
            )
        ]

    def _esc_label(self) -> str:
        """What Esc does at the dossier's top level — the picker's list
        waits underneath it; standalone there is only the prompt."""
        return "quit"

    # ---------- behavior ----------

    def _split(self) -> tuple[int, int]:
        return _TURN_SPLIT if self.tab == "messages" else _LENS_SPLIT

    def _rows_count(self) -> int:
        if self.tab == "premise":
            return self._premise_rows
        if self.tab == "messages":
            return len(self.turn_filtered)
        if self.detail is not None:
            return len(self.fields)
        return len(self.scenes_f) if self.tab == "scenes" else len(self.cast_f)

    def _move_cursor(self, delta: int) -> None:
        self.notice = ""
        super()._move_cursor(delta)

    def _refilter(self) -> None:
        assert self.view is not None
        q = self.query.strip().casefold()
        if self.tab == "messages":
            if not q:
                self.turn_filtered = list(range(len(self.msgs)))
            else:
                self.turn_filtered = [i for i, m in enumerate(self.msgs) if q in m.body.casefold()]
        elif self.tab == "scenes":
            if not q:
                self.scenes_f = list(range(len(self.view.scenes)))
            else:
                self.scenes_f = [
                    i
                    for i, s in enumerate(self.view.scenes)
                    if q in f"{s.title} {s.summary}".casefold()
                ]
        elif self.tab == "cast":
            if not q:
                self.cast_f = list(range(len(self.view.cast)))
            else:
                self.cast_f = [
                    i
                    for i, c in enumerate(self.view.cast)
                    if q
                    in (
                        c.name
                        + " "
                        + " ".join(c.aliases)
                        + " "
                        + c.description
                        + " "
                        + (state.text if (state := self._latest_state(c.id)) else "")
                    ).casefold()
                ]
        if self.cursor >= self._rows_count():
            self.cursor = max(0, self._rows_count() - 1)

    def _filterable(self) -> bool:
        """Where `/` opens the filter: the three list tabs, at their top
        level — a detail has nothing to filter, and neither does one
        text."""
        return self.tab != "premise" and self.detail is None

    def _type(self, data: str) -> None:
        if self.in_filter:
            self.query += data
            self._refilter()
            return
        self.notice = ""
        if data in "/." and self._filterable():
            self._open_filter()
        else:
            self._on_key(data)

    def _on_key(self, data: str) -> None:
        key = latin_key(data)
        if key == "e" and self.dossier_on and self.tab == "messages":
            self._start_edit()
        elif key == "o" and self.dossier_on and self.detail is not None:
            self._pivot()

    def _open_detail(self) -> None:
        assert self.view is not None
        self.notice = ""
        if self.tab == "scenes":
            if not self.scenes_f:
                return
            self.detail = ("scene", self.view.scenes[self.scenes_f[self.cursor]].id)
        else:
            if not self.cast_f:
                return
            self.detail = ("char", self.view.cast[self.cast_f[self.cursor]].id)
        self._list_pos = self.cursor
        self.cursor = 0
        self._rebuild_fields()

    def _on_enter(self) -> None:
        if self.tab == "premise":
            self._start_edit()
            return
        if self.tab == "messages":
            if not self.turn_filtered:
                return
            orig = self.turn_filtered[self.cursor]
            if orig + 1 == len(self.msgs):
                self._land("resume")
            else:
                # An earlier turn: what resuming there means is the resume
                # dialog's question, fork being the default.
                self.confirming_resume = True
                self.resume_choice = 0
            return
        if self.detail is not None:
            self._start_edit()
        else:
            self._open_detail()

    def _on_escape(self) -> None:
        self.notice = ""
        if self._clear_filter():
            return
        if self.detail is not None:
            self.detail = None
            self.fields = []
            self.cursor = min(self._list_pos, max(0, self._rows_count() - 1))
            return
        self._leave_dossier()

    def _leave_dossier(self) -> None:
        """Esc at the dossier's top level. Standalone the dossier is the
        whole screen, so it quits; the picker overrides this to reveal
        its list again."""
        get_app().exit()

    # ---------- the landings ----------

    def _do_resume(self) -> None:
        action = _RESUME_OPTIONS[self.resume_choice][0]
        self.confirming_resume = False
        if action == "cancel" or not self.turn_filtered:
            return
        self._land(action)

    def _land(self, action: api_stories.LandAction) -> None:
        """Execute the settled pick (the screens' one ownership rule) and
        leave with the landing line for the caller to print."""
        upto = self.msgs[self.turn_filtered[self.cursor]]
        self._land_on(self.story_id, upto.id, action)

    def _land_on(self, story_id: int, upto_id: int | None, action: api_stories.LandAction) -> None:
        """The landing itself: a story and a pick, the pick absent where
        there is nothing to pick — the list level resumes a story with
        nothing played without opening it."""
        try:
            self.result = api_stories.land(self.session, story_id, upto_id, action)
        except Refused as e:
            self.notice = str(e)
            return
        get_app().exit()

    # ---------- editing ----------

    def _edit_guard(self) -> str | None:
        """Why the text under the cursor can't be edited, or None to go."""
        if self.tab == "premise":
            return None
        if self.tab == "messages":
            return None if self.turn_filtered else "nothing to edit"
        if self.detail is None or not self.fields:
            return "nothing to edit"
        f = self.fields[self.cursor]
        if not f.editable:
            if f.kind == "history":
                return "the history is rebuilt from the entries — edit those instead"
            if f.kind == "scene-history":
                return "the history is rebuilt from the summaries — edit those instead"
            if f.kind == "state":
                return "the state is the extractor's own — edit the entry instead"
            return "not editable"
        return None

    def _edit_text(self) -> str:
        """What the buffer opens with: the premise, the focused message,
        or the focused field."""
        if self.tab == "premise":
            return self.premise
        if self.tab == "messages":
            return self.msgs[self.turn_filtered[self.cursor]].body
        return self.fields[self.cursor].text

    def _start_edit(self) -> None:
        """Enter (or `e` on a message): the text becomes the buffer, in
        the spot it is displayed."""
        reason = self._edit_guard()
        if reason is not None:
            self.notice = reason
            return
        self.notice = ""
        self.editing = True
        # Cursor at the START: an edit begins by reading, and a long text
        # opened at its end shows only its tail.
        self.edit_buffer.document = Document(self._edit_text(), 0)
        self.app.layout.focus(
            self._premise_edit_control if self.tab == "premise" else self._edit_control
        )

    def _finish_edit(self, *, save: bool) -> None:
        """Ctrl+S applies the buffer through `backend.api`; Esc discards."""
        self.editing = False
        self.app.layout.focus(self._items_control)
        if not save:
            self.notice = "(cancelled)"
            return
        new = self.edit_buffer.text.rstrip("\n")
        if new == self._edit_text():
            self.notice = "(unchanged)"
            return
        try:
            if self.tab == "premise":
                self.notice = api_stories.set_system(self.session, new, self.story_id)
                self.premise = new  # "" is a save too: the premise cleared
                return
            if self.tab == "messages":
                orig = self.turn_filtered[self.cursor]
                m = self.msgs[orig]
                api_stories.edit_message(self.session, m.id, new, story_id=self.story_id)
                self.msgs[orig] = replace(m, body=new)
                self.notice = "saved"
                return
            f = self.fields[self.cursor]
            api_lore.edit(self.session, f.kind, f.target, new, self.story_id)
        except Refused as e:
            self.notice = str(e)
            return
        except Exception as e:
            self.notice = f"save failed: {e}"
            return
        self._reload()
        self.notice = "saved"

    def _active_edit_width(self) -> int:
        """The visible edit window's rendered width — the wrap width the
        display actually uses; the layout math is the fallback before a
        render."""
        window = self._premise_edit_window if self.tab == "premise" else self._edit_window
        info = window.render_info
        if info is not None and info.window_width > 0:
            return info.window_width
        return max(10, self._preview_inner_width())

    @staticmethod
    def _display_rows(text: str, width: int) -> list[tuple[int, int, bool]]:
        """(start, length, line_end) of each wrapped DISPLAY row — the same
        character wrapping the Window renders, so motion by row lands where
        the eye expects."""
        rows: list[tuple[int, int, bool]] = []
        pos = 0
        for line in text.split("\n"):
            start = 0
            while True:
                length = min(width, len(line) - start)
                line_end = start + length >= len(line)
                rows.append((pos + start, length, line_end))
                if line_end:
                    break
                start += length
            pos += len(line) + 1
        return rows

    def _move_edit_cursor(self, delta: int) -> None:
        """Up/down inside the editor move by display row, not logical
        line. Prose fields are one long wrapped line — the default
        logical-line motion has nowhere to go on them and the cursor just
        sticks."""
        rows = self._display_rows(self.edit_buffer.text, self._active_edit_width())
        cur = self.edit_buffer.cursor_position
        idx = len(rows) - 1
        for i, (start, length, line_end) in enumerate(rows):
            # On a wrapped (non-final) row the offset just past it already
            # displays at the start of the next row.
            if cur < start + length + (1 if line_end else 0):
                idx = i
                break
        target = max(0, min(len(rows) - 1, idx + delta))
        if target == idx:
            return
        col = cur - rows[idx][0]
        tstart, tlength, tline_end = rows[target]
        self.edit_buffer.cursor_position = tstart + min(
            col, tlength if tline_end else max(0, tlength - 1)
        )

    # ---------- hooks the picker subclass fills ----------

    def _extra_idle(self) -> Filter:
        """What else must be quiet for the standard keys to fire — the
        picker's delete confirm; nothing standalone."""
        return Condition(lambda: True)

    def _extra_keys(self, kb: KeyBindings, idle: Filter) -> None:
        """The picker's own keys (delete and its confirm), added after the
        standard ones so its backspace outranks their no-op."""

    def _extra_floats(self) -> list[AnyContainer]:
        """The picker's own dialogs."""
        return []

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()
        resuming = Condition(lambda: self.confirming_resume)

        # While the resume dialog is up: arrows walk the options, Enter
        # confirms the highlighted one, Esc closes back into the browser.
        @kb.add("escape", eager=True, filter=resuming)
        def _resume_esc(event: Any) -> None:
            self.confirming_resume = False

        @kb.add("up", filter=resuming)
        def _resume_up(event: Any) -> None:
            self.resume_choice = (self.resume_choice - 1) % len(_RESUME_OPTIONS)

        @kb.add("down", filter=resuming)
        def _resume_down(event: Any) -> None:
            self.resume_choice = (self.resume_choice + 1) % len(_RESUME_OPTIONS)

        @kb.add("enter", filter=resuming)
        def _resume_enter(event: Any) -> None:
            self._do_resume()

        idle = ~resuming & self._extra_idle()
        self._standard_keys(kb, when=idle)

        in_dossier = Condition(lambda: self.dossier_on)

        # ←/→ are the advertised way between tabs; Tab and Shift+Tab do
        # the same, unadvertised, for the hands that reach for them.
        @kb.add("right", filter=idle & in_dossier)
        @kb.add("tab", filter=idle & in_dossier)
        def _tab_key(event: Any) -> None:
            self._switch_tab(1)

        @kb.add("left", filter=idle & in_dossier)
        @kb.add("s-tab", filter=idle & in_dossier)
        def _stab_key(event: Any) -> None:
            self._switch_tab(-1)

        self._extra_keys(kb, idle)

        # While the buffer owns the text, every binding above is suspended —
        # keystrokes are the buffer's (the app's default bindings edit it).
        # Only save/cancel and quit stay live.
        editing = Condition(lambda: self.editing)
        edit_kb = KeyBindings()

        @edit_kb.add("c-s", filter=editing)
        def _save(event: Any) -> None:
            self._finish_edit(save=True)

        @edit_kb.add("escape", filter=editing, eager=True)
        def _cancel(event: Any) -> None:
            self._finish_edit(save=False)

        # The buffer's own bindings know arrows, not pages.
        @edit_kb.add("pageup", filter=editing)
        def _edit_pgup(event: Any) -> None:
            self.edit_buffer.cursor_up(page_step())

        @edit_kb.add("pagedown", filter=editing)
        def _edit_pgdn(event: Any) -> None:
            self.edit_buffer.cursor_down(page_step())

        always_kb = KeyBindings()

        @always_kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        bindings = merge_key_bindings([ConditionalKeyBindings(kb, ~editing), edit_kb, always_kb])

        # Motion inside the editor, attached to the controls themselves so
        # it only exists while a buffer has focus — and outranks the
        # default logical-line motion, which sticks on wrapped prose.
        edit_motion = KeyBindings()

        @edit_motion.add("up")
        def _edit_up(event: Any) -> None:
            self._move_edit_cursor(-1)

        @edit_motion.add("down")
        def _edit_down(event: Any) -> None:
            self._move_edit_cursor(1)

        # Focusable so focus has somewhere to return to when editing ends.
        self._items_control = self._make_items_control(focusable=True)
        self._items_window = Window(
            content=self._items_control,
            wrap_lines=False,
            always_hide_cursor=True,
            style="class:row",
        )

        # ONE buffer, two windows: the panel editor under the preview's
        # header, and the premise editor across the full pane — only ever
        # one visible, so the buffer and the finish path are shared.
        self._edit_control = BufferControl(
            buffer=self.edit_buffer, focusable=True, key_bindings=edit_motion
        )
        self._edit_window = Window(self._edit_control, wrap_lines=True, style="class:preview.body")
        self._premise_edit_control = BufferControl(
            buffer=self.edit_buffer, focusable=True, key_bindings=edit_motion
        )
        self._premise_edit_window = Window(
            self._premise_edit_control, wrap_lines=True, style="class:preview.body"
        )

        premise_shown = Condition(lambda: self.dossier_on and self.tab == "premise")
        premise_editing = Condition(
            lambda: self.editing and self.dossier_on and self.tab == "premise"
        )
        panel_editing = editing & ~premise_editing

        # The premise editor sits exactly where the text is read: two
        # columns in, the same indent its lines carry.
        premise_editor = VSplit(
            [
                Window(width=2, char=" ", always_hide_cursor=True),
                self._premise_edit_window,
            ]
        )
        center = HSplit(
            [
                ConditionalContainer(self._items_window, filter=~premise_editing),
                ConditionalContainer(premise_editor, filter=premise_editing),
            ]
        )

        # One header row, its fragments carrying their own classes: the
        # tab strip at tab level, an open detail's title over it.
        left_rows: list[AnyContainer] = [
            text_line(self._header_text),
            Window(height=1, char=" ", always_hide_cursor=True),
            text_line(self._filter_text, filter=Condition(lambda: self.in_filter)),
            center,
            text_line(
                lambda: [("class:notice", "  " + self.notice if self.notice else "")],
                filter=Condition(lambda: bool(self.notice)),
            ),
            Window(height=1, char=" ", always_hide_cursor=True),
            text_line(self._help_text, style="class:help"),
        ]
        left_pane = HSplit(left_rows, width=lambda: D(weight=self._split()[0]))

        preview_pane = self._preview_panel(
            header_filter=Condition(
                lambda: (
                    self.dossier_on
                    and (
                        (self.tab == "messages" and bool(self.turn_filtered))
                        or (self.detail is not None and bool(self.fields))
                    )
                )
            ),
            editing=panel_editing,
            edit_window=self._edit_window,
        )
        # The premise is one text at full measure — no list beside it, so
        # no preview either; the pane folds away and the text takes the
        # width.
        preview_side = ConditionalContainer(
            VSplit([self._preview_gap(), preview_pane]), filter=~premise_shown
        )

        resume_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._resume_text, show_cursor=False),
                width=D(min=50, max=74, preferred=64),
                height=D.exact(11),
                style="class:dialog.body",
                border_style="class:dialog.border",
            ),
            filter=resuming,
        )

        root = VSplit([left_pane, preview_side])
        floats: list[AnyContainer] = [resume_dialog, *self._extra_floats()]
        return self._finish_app(root, bindings, _style(), floats=floats)


def browse(session: Session, tab: Tab = "scenes") -> str | None:
    """Open the dossier on the OPEN story — on the scenes tab (/lore) or
    the cast tab (/cast); edits are written as they are saved. On a
    landing (Enter in the messages tab) the land is EXECUTED and its
    line returned for the caller to print; None when the reader only
    left (Esc/Ctrl+C)."""
    assert session.story_id is not None  # callers gate on a story
    screen = Dossier(session)
    screen._open_dossier(session.story_id, tab)
    return screen.run()
