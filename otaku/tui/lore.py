"""The /lore browser: scenes, cast, and journals in one prompt_toolkit app.

Two lenses over the same memory, switched with Tab — the story in order
(scenes) and the story by who is in it (cast). A journal row is the
intersection of a scene and a character, so it is reachable through both
parents, and `→` pivots between them on the focused row: the same entry,
two doors.

  Scenes lens                          Cast lens
        Scenes (7)                           Cast (5)
      >  4  61-84  The Crossing           >  Elara    now: resting at…
        ...                                  ...
        [preview: summary + present]         [preview: description,
                                              state + vintage, history]

Enter opens the item as a FIELD LIST — one row per editable text — and
Enter on a field edits it IN PLACE: the right panel's header stays put
and the text below it becomes the edit buffer (Ctrl+S saves, Esc
cancels). Editable fields are the write-once primitives (scene title /
summary, journal entry, the latest journal state, character description).
Derivatives (the histories) are shown dim and never editable: they are
rebuilt from the primitives, so an edit there would be silently
overwritten — fix the inputs and the outputs follow. The store writers
carry the invalidation; the browser never calls a model.
"""

from dataclasses import dataclass, replace
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
from prompt_toolkit.layout.containers import VSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.formatting import flatten, truncate
from otaku.store import Store
from otaku.store.schema import Character, Scene
from otaku.tui.screen import BASE_STYLE, ListScreen, wrap_text

_STYLE = Style.from_dict(
    {
        **BASE_STYLE,
        "row": "fg:#000000 bg:#ffffff",
        "row.selected": "bold fg:#000000 bg:#e4e4e4",
        "row.dim": "fg:#767676 bg:#ffffff",
        "row.dim.selected": "fg:#767676 bg:#e4e4e4",
        "preview.title": "bold fg:#303030 bg:#ffffff",
        "preview.muted": "fg:#767676 bg:#ffffff",
        "preview.body": "fg:#000000 bg:#ffffff",
        "notice": "fg:#767676 bg:#ffffff",
    }
)


@dataclass
class JournalRow:
    """One journal row, mutable so an edit updates the view in place."""

    id: int
    scene_id: int
    character_id: int
    state: str
    history: str
    entry: str


@dataclass(frozen=True)
class Field:
    """One row of a detail view: a label, the text it holds, and what an
    edit or a pivot on it targets."""

    label: str
    kind: str  # scene-title | scene-summary | description | entry | state | history
    text: str
    target: int  # scene id / character id / journal id, per kind
    editable: bool
    pivot: int | None = None  # character id (scene detail) / scene id (cast detail)
    note: str = ""  # dim annotation: why not editable, or the field's vintage
    scene_no: int | None = None  # journal rows in the char view carry their scene's number


class LoreBrowser(ListScreen):
    def __init__(self, store: Store, story_id: int, lens: str = "scenes") -> None:
        super().__init__()
        self.store = store
        ids = store.stories.get_messages_ids(story_id)
        self.message_ids = ids
        self.scenes: list[Scene] = store.scenes.get_current(story_id, ids)
        self.ordinal: dict[int, int] = {mid: i + 1 for i, mid in enumerate(ids)}
        self.total_messages = len(ids)
        self.cast: list[Character] = sorted(
            store.characters.list(story_id), key=lambda c: c.name.casefold()
        )
        current = {s.id for s in self.scenes}
        self.jrows: list[JournalRow] = [
            JournalRow(j.id, j.scene_id, j.character_id, j.state, j.history, j.entry)
            for j in store.journals.list(story_id)
            if j.scene_id in current
        ]
        self.by_scene: dict[int, list[JournalRow]] = {}
        self.by_char: dict[int, list[JournalRow]] = {}
        for r in self.jrows:
            self.by_scene.setdefault(r.scene_id, []).append(r)
            self.by_char.setdefault(r.character_id, []).append(r)
        self.latest_row: dict[int, int] = {cid: rows[-1].id for cid, rows in self.by_char.items()}

        self.lens: str = lens  # scenes | cast
        self.detail: tuple[str, int] | None = None  # ("scene", id) | ("char", id)
        self.fields: list[Field] = []
        self.scene_cursor: int = 0
        self.cast_cursor: int = 0
        self.field_cursor: int = 0
        self.scenes_f: list[int] = list(range(len(self.scenes)))
        self.cast_f: list[int] = list(range(len(self.cast)))

        # Inline editing: while True the right panel is the edit buffer and
        # every navigation binding is suspended — keystrokes belong to it.
        self.editing: bool = False
        self.edit_buffer = Buffer(multiline=True)

        self.app = self._build_app()

    def run(self) -> None:
        self.app.run()

    # ---------- lookups ----------

    def _scene_by_id(self, scene_id: int) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def _char_by_id(self, cid: int) -> Character | None:
        return next((c for c in self.cast if c.id == cid), None)

    def _scene_no(self, scene_id: int) -> int:
        """1-based position of a scene in story order."""
        return next((i + 1 for i, s in enumerate(self.scenes) if s.id == scene_id), 0)

    def _scene_label(self, scene: Scene | None) -> str:
        """A scene named for a row: its title, else its number."""
        if scene is None:
            return "?"
        return flatten(scene.title) if scene.title else f"scene {self._scene_no(scene.id)}"

    def _span(self, s: Scene) -> str:
        start = self.ordinal.get(s.start_message_id)
        end = self.ordinal.get(s.end_message_id)
        if start is None or end is None:
            return ""
        return f"{start}-{end}"

    def _vintage(self, r: JournalRow) -> str:
        """Where a journal row's text is from: its scene, and how far the
        story has moved past it."""
        scene = self._scene_by_id(r.scene_id)
        if scene is None:
            return ""
        no = self._scene_no(r.scene_id)
        ago = self.total_messages - self.ordinal.get(scene.end_message_id, self.total_messages)
        title = f" '{scene.title}'" if scene.title else ""
        return f"scene {no}{title}, {ago} msgs ago"

    def _current_history(self, cid: int) -> tuple[str, JournalRow | None]:
        """The character's newest non-empty rollup, as the store's
        `get_current` would pick it."""
        for r in reversed(self.by_char.get(cid, [])):
            if r.history:
                return r.history, r
        return "", None

    # ---------- detail field lists ----------

    def _scene_fields(self, scene: Scene) -> list[Field]:
        out = [
            Field("title", "scene-title", scene.title, scene.id, True),
            Field("summary", "scene-summary", scene.summary, scene.id, True),
        ]
        for r in self.by_scene.get(scene.id, []):
            char = self._char_by_id(r.character_id)
            name = char.name if char else "?"
            latest = self.latest_row.get(r.character_id) == r.id
            out.append(Field(f"{name} · entry", "entry", r.entry, r.id, True, r.character_id))
            out.append(
                Field(
                    f"{name} · state",
                    "state",
                    r.state,
                    r.id,
                    latest,
                    r.character_id,
                    note="" if latest else "(superseded)",
                )
            )
        return out

    def _char_fields(self, char: Character) -> list[Field]:
        out = [Field("description", "description", char.description, char.id, True)]
        for r in self.by_char.get(char.id, []):
            scene = self._scene_by_id(r.scene_id)
            no = self._scene_no(r.scene_id)
            label = self._scene_label(scene)
            latest = self.latest_row.get(char.id) == r.id
            out.append(
                Field(f"{label} · entry", "entry", r.entry, r.id, True, r.scene_id, scene_no=no)
            )
            out.append(
                Field(
                    f"{label} · state",
                    "state",
                    r.state,
                    r.id,
                    latest,
                    r.scene_id,
                    note="" if latest else "(superseded)",
                    scene_no=no,
                )
            )
        history, hrow = self._current_history(char.id)
        if history and hrow is not None:
            out.append(
                Field(
                    "history (so far)",
                    "history",
                    history,
                    hrow.id,
                    False,
                    note="(rebuilt from entries)",
                )
            )
        return out

    def _rebuild_fields(self) -> None:
        if self.detail is None:
            self.fields = []
            return
        kind, target = self.detail
        if kind == "scene":
            scene = self._scene_by_id(target)
            self.fields = self._scene_fields(scene) if scene else []
        else:
            char = self._char_by_id(target)
            self.fields = self._char_fields(char) if char else []
        if self.field_cursor >= len(self.fields):
            self.field_cursor = max(0, len(self.fields) - 1)

    # ---------- text content ----------

    def _header_text(self) -> StyleAndTextTuples:
        if self.detail is not None:
            kind, target = self.detail
            if kind == "scene":
                scene = self._scene_by_id(target)
                no = self._scene_no(target)
                if scene is not None and scene.title:
                    return [("class:header", f" Scene {no}: {truncate(flatten(scene.title), 60)}")]
                return [("class:header", f" Scene {no}")]
            char = self._char_by_id(target)
            name = char.name if char else "?"
            aka = f" (aka {', '.join(char.aliases)})" if char and char.aliases else ""
            return [("class:header", f" {name}{aka}")]
        if self.lens == "scenes":
            return [("class:header", f" Scenes ({len(self.scenes_f)})")]
        return [("class:header", f" Cast ({len(self.cast_f)})")]

    def _items_text(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if self.detail is not None:
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
                if f.note:
                    head = f"{f.note} {head}"
                no = f"{f.scene_no:>{no_w}}  " if f.scene_no is not None else " " * (no_w + 2)
                row = f"{no if no_w else ''}{truncate(f.label, label_w):<{label_w}} · {head}"
                self._emit_row(out, i == self.field_cursor, row, dim=not f.editable)
            return out
        if self.lens == "scenes":
            if not self.scenes_f:
                msg = "(no matches)" if self.query else "(no scenes yet — they close as you play)"
                out.append(("class:muted", "  " + msg))
                return out
            no_w = len(str(len(self.scenes)))
            span_w = max(len(self._span(s)) for s in self.scenes)
            avail = max(10, self._max_row_content_width() - 4 - no_w - 3 - span_w - 2)
            for row_i, idx in enumerate(self.scenes_f):
                s = self.scenes[idx]
                title = truncate(flatten(s.title), avail)
                row = f"{idx + 1:>{no_w}}  {self._span(s):>{span_w}}  {title}"
                self._emit_row(out, row_i == self._cursor(), row)
            return out
        if not self.cast_f:
            msg = "(no matches)" if self.query else "(no characters yet)"
            out.append(("class:muted", "  " + msg))
            return out
        name_w = min(20, max(len(c.name) for c in self.cast))
        avail = max(10, self._max_row_content_width() - 4 - name_w - 3)
        for row_i, idx in enumerate(self.cast_f):
            c = self.cast[idx]
            rows = self.by_char.get(c.id)
            snippet = f"now: {rows[-1].state}" if rows else "(no journal yet)"
            row = f"{truncate(c.name, name_w):<{name_w}} · {truncate(flatten(snippet), avail)}"
            self._emit_row(out, row_i == self._cursor(), row)
        return out

    def _panel_header_text(self) -> StyleAndTextTuples:
        """The fixed header above the focused field's text. It lives in its
        own window so it stays put when the text below it becomes the edit
        buffer — editing happens exactly where the text is displayed."""
        if self.detail is None or not self.fields:
            return [("", "")]
        f = self.fields[self.field_cursor]
        out: StyleAndTextTuples = [("class:preview.title", f.label + "\n")]
        if f.note:
            out.append(("class:preview.muted", f.note + "\n"))
        return out

    def _preview_text(self) -> StyleAndTextTuples:
        width = max(10, self._preview_inner_width())
        out: StyleAndTextTuples = []

        if self.detail is not None:
            if not self.fields:
                return [("class:preview.muted", "nothing to preview")]
            f = self.fields[self.field_cursor]
            for line in wrap_text(f.text or "(empty)", width):
                out.append(("class:preview.body", line + "\n"))
            return out

        if self.lens == "scenes":
            if not self.scenes_f:
                return [("class:preview.muted", "nothing to preview")]
            s = self.scenes[self.scenes_f[self.scene_cursor]]
            out.append(("class:preview.title", self._scene_label(s) + "\n"))
            out.append(("class:preview.muted", f"messages {self._span(s)}\n"))
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(s.summary or "(no summary)", width):
                out.append(("class:preview.body", line + "\n"))
            present = [
                c.name
                for c in self.cast
                if any(r.scene_id == s.id for r in self.by_char.get(c.id, []))
            ]
            if present:
                out.append(("class:preview.body", "\n"))
                out.append(("class:preview.muted", "present: " + ", ".join(present) + "\n"))
            return out

        if not self.cast_f:
            return [("class:preview.muted", "nothing to preview")]
        c = self.cast[self.cast_f[self.cast_cursor]]
        aka = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        out.append(("class:preview.title", f"{c.name}{aka}\n"))
        if c.description:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(c.description, width):
                out.append(("class:preview.body", line + "\n"))
        rows = self.by_char.get(c.id)
        if rows:
            latest = rows[-1]
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", f"now ({self._vintage(latest)}):\n"))
            for line in wrap_text(latest.state, width):
                out.append(("class:preview.body", line + "\n"))
            history, _ = self._current_history(c.id)
            if history:
                out.append(("class:preview.body", "\n"))
                out.append(("class:preview.muted", "so far (rebuilt from entries):\n"))
                for line in wrap_text(history, width):
                    out.append(("class:preview.muted", line + "\n"))
        else:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "(no journal yet)\n"))
        return out

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            return [("class:help", " editing — ctrl+s save · esc cancel")]
        if self.in_filter:
            return [
                (
                    "class:help",
                    " type to filter · ↑/↓ navigate · enter open · esc clear filter",
                )
            ]
        if self.detail is not None:
            other = "character" if self.detail[0] == "scene" else "scene"
            return [("class:help", f" ↑/↓ fields · enter edit · → their {other} · esc back")]
        other = "cast" if self.lens == "scenes" else "scenes"
        return [("class:help", f" ↑/↓ navigate · tab {other} · enter open · / filter · esc quit")]

    # ---------- behavior ----------

    def _rows_count(self) -> int:
        if self.detail is not None:
            return len(self.fields)
        return len(self.scenes_f) if self.lens == "scenes" else len(self.cast_f)

    def _cursor(self) -> int:
        if self.detail is not None:
            return self.field_cursor
        return self.scene_cursor if self.lens == "scenes" else self.cast_cursor

    def _set_cursor(self, value: int) -> None:
        if self.detail is not None:
            self.field_cursor = value
        elif self.lens == "scenes":
            self.scene_cursor = value
        else:
            self.cast_cursor = value

    def _move_cursor(self, delta: int) -> None:
        self.notice = ""
        super()._move_cursor(delta)

    def _refilter(self) -> None:
        q = self.query.strip().casefold()
        if not q:
            self.scenes_f = list(range(len(self.scenes)))
            self.cast_f = list(range(len(self.cast)))
        elif self.lens == "scenes":
            self.scenes_f = [
                i for i, s in enumerate(self.scenes) if q in f"{s.title} {s.summary}".casefold()
            ]
        else:
            self.cast_f = [
                i
                for i, c in enumerate(self.cast)
                if q
                in (
                    c.name
                    + " "
                    + " ".join(c.aliases)
                    + " "
                    + c.description
                    + " "
                    + (self.by_char[c.id][-1].state if c.id in self.by_char else "")
                ).casefold()
            ]
        if self._cursor() >= self._rows_count():
            self._set_cursor(max(0, self._rows_count() - 1))

    def _tab(self) -> None:
        self.notice = ""
        if self.detail is not None:
            return
        self.lens = "cast" if self.lens == "scenes" else "scenes"
        if self.query:
            self._refilter()

    def _open(self) -> None:
        self.notice = ""
        if self.detail is not None:
            return  # editing goes through _start_edit
        if self.lens == "scenes":
            if not self.scenes_f:
                return
            scene = self.scenes[self.scenes_f[self.scene_cursor]]
            self.detail = ("scene", scene.id)
        else:
            if not self.cast_f:
                return
            char = self.cast[self.cast_f[self.cast_cursor]]
            self.detail = ("char", char.id)
        self.field_cursor = 0
        self._rebuild_fields()

    def _pivot(self) -> None:
        """`→` on a journal field: the same row through the other lens."""
        self.notice = ""
        if self.detail is None or not self.fields:
            return
        f = self.fields[self.field_cursor]
        if f.pivot is None:
            return
        from_scene = self.detail[0] == "scene"
        self.detail = ("char", f.pivot) if from_scene else ("scene", f.pivot)
        self._rebuild_fields()
        # Land on the same journal row's same field, seen from the other side.
        for i, g in enumerate(self.fields):
            if g.kind == f.kind and g.target == f.target:
                self.field_cursor = i
                break
        else:
            self.field_cursor = 0

    def _on_enter(self) -> None:
        if self.detail is not None:
            self._start_edit()
        else:
            self._open()

    def _on_escape(self) -> None:
        self.notice = ""
        if self._clear_filter():
            return
        if self.detail is not None:
            self.detail = None
            self.fields = []
            return
        get_app().exit()

    def _type(self, data: str) -> None:
        """The base behavior, except that `/` opens the filter only on the
        top-level lists — a detail view has nothing to filter."""
        if self.in_filter:
            self.query += data
            self._refilter()
            return
        self.notice = ""
        if self.detail is None and data == "/":
            self._open_filter()

    # ---------- editing ----------

    def _edit_guard(self) -> str | None:
        """Why the focused field can't be edited, or None to go."""
        if self.detail is None or not self.fields:
            return "nothing to edit"
        f = self.fields[self.field_cursor]
        if not f.editable:
            if f.kind == "history":
                return "history is rebuilt from the entries — edit those instead"
            if f.kind == "state":
                return "superseded — only the latest state is ever read again"
            return "not editable"
        return None

    def _start_edit(self) -> None:
        """Enter on a field: the text under the header becomes the buffer."""
        reason = self._edit_guard()
        if reason is not None:
            self.notice = reason
            return
        f = self.fields[self.field_cursor]
        self.notice = ""
        self.editing = True
        self.edit_buffer.document = Document(f.text, len(f.text))
        self.app.layout.focus(self._edit_control)

    def _edit_width(self) -> int:
        """The edit window's rendered width — the wrap width the display
        actually uses; the layout math is the fallback before a render."""
        info = self._edit_window.render_info
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
        """Up/down inside the editor move by display row, not logical line.
        Prose fields are one long wrapped line — the default logical-line
        motion has nowhere to go on them and the cursor just sticks."""
        rows = self._display_rows(self.edit_buffer.text, self._edit_width())
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

    def _finish_edit(self, *, save: bool) -> None:
        """Ctrl+S applies the buffer through the store writers; Esc
        discards it."""
        self.editing = False
        self.app.layout.focus(self._items_control)
        if not save:
            self.notice = "(cancelled)"
            return
        f = self.fields[self.field_cursor]
        new = self.edit_buffer.text.rstrip("\n")
        if new == f.text:
            self.notice = "(unchanged)"
            return
        try:
            self._apply_edit(f, new)
        except ValueError as e:
            self.notice = str(e)
            return
        self.notice = "saved"

    def _apply_edit(self, f: Field, new: str) -> None:
        if f.kind in ("scene-title", "scene-summary"):
            for i, s in enumerate(self.scenes):
                if s.id != f.target:
                    continue
                if f.kind == "scene-title":
                    self.store.scenes.update(f.target, title=new)
                    self.scenes[i] = replace(s, title=new)
                else:
                    self.store.scenes.update(f.target, summary=new)
                    self.scenes[i] = replace(s, summary=new)
                    # Mirror the store: the histories composed from the old
                    # text are gone; the view must not keep showing them.
                    for j, other in enumerate(self.scenes):
                        if other.id >= f.target:
                            self.scenes[j] = replace(other, history="")
                break
        elif f.kind == "description":
            self.store.characters.set_description(f.target, new)
            for i, c in enumerate(self.cast):
                if c.id == f.target:
                    self.cast[i] = replace(c, description=new)
                    break
        elif f.kind == "entry":
            self.store.journals.set_entry(f.target, new)
            row = next(r for r in self.jrows if r.id == f.target)
            row.entry = new
            for r in self.by_char.get(row.character_id, []):
                if r.id >= row.id:
                    r.history = ""
        elif f.kind == "state":
            self.store.journals.set_state(f.target, new, self.message_ids)
            next(r for r in self.jrows if r.id == f.target).state = new
        self._rebuild_fields()

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()
        self._standard_keys(kb)

        @kb.add("tab")
        def _tab_key(event: Any) -> None:
            self._tab()

        @kb.add("right")
        def _right(event: Any) -> None:
            self._pivot()

        # While the buffer owns the panel, every binding above is suspended —
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

        always_kb = KeyBindings()

        @always_kb.add("c-c")
        def _ctrlc(event: Any) -> None:
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
        left_pane = self._list_pane(items_window, width=D(weight=1), notice=True)

        # Motion inside the editor, attached to the control itself so it only
        # exists while the buffer has focus — and outranks the default
        # logical-line motion, which sticks on wrapped prose.
        edit_motion = KeyBindings()

        @edit_motion.add("up")
        def _edit_up(event: Any) -> None:
            self._move_edit_cursor(-1)

        @edit_motion.add("down")
        def _edit_down(event: Any) -> None:
            self._move_edit_cursor(1)

        self._edit_control = BufferControl(
            buffer=self.edit_buffer, focusable=True, key_bindings=edit_motion
        )
        self._edit_window = Window(self._edit_control, wrap_lines=True, style="class:preview.body")
        preview_pane = self._preview_panel(
            header_filter=Condition(lambda: self.detail is not None and bool(self.fields)),
            editing=editing,
            edit_window=self._edit_window,
        )

        root = VSplit([left_pane, self._preview_gap(), preview_pane])
        return self._finish_app(root, bindings, _STYLE, floats=[])


def browse(store: Store, story_id: int, lens: str = "scenes") -> None:
    """Open the lore browser on the story — on the scenes lens (/lore) or
    the cast lens (/cast). Pure view/edit — nothing is returned; edits are
    written as they are saved."""
    LoreBrowser(store, story_id, lens=lens).run()
