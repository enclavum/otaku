"""Inside one story: the dossier — premise, messages, scenes, cast and
tools as five tabs of one full-screen surface, for ANY story, editing
included.

←/→ cycle the tabs (wrapping) from anywhere outside an edit; each tab
keeps its cursor and its filter, while an open detail closes with the switch, so coming back
lands on the tab's list, cursor on the row that was drilled into. The
premise tab is the one full-width view — a premise is one text, so
there is no list beside it; Enter edits it in place. The messages tab
is the story browser's message view: Enter on the last message resumes,
an earlier one asks what resuming there means (fork — the default —
truncate, or cancel), and `e` corrects a message where it is read.
Scenes and cast are the two lenses over the memory: Enter opens an item
as a FIELD LIST — one row per text (`scene_fields` / `char_fields`,
this screen's own reading of the story's `Lore`) — and Enter on a
field edits it IN PLACE (Ctrl+S saves, Esc cancels). A journal row is
the intersection of a scene and a character, so `o` pivots to the same
entry through the other tab. Del on a cast row deletes the character
after a y/n confirm. The tools tab is the story's settings
(`api.stories.settings`), one row each — a checkbox and the name; Space
checks and unchecks it — with the setting's fields in the panel beside:
the notes' display, a reminder's text, a setting's instructions (its
prompt: a tool's `api.tools`, the emulation's `api.prompts`), the web
search key (`api.integrations`), and last where its text rides. Enter
or Tab moves into the panel, where ↑/↓ walk the fields and Enter acts
on the one under the cursor — a checkbox toggles, the depth opens the
closed list of places in the same panel (↑/↓ pick, Enter sets — a
dropdown, no dialog), a reminder's text edits in place like any field,
the web search key is typed in place over where it stands (never
shown; Delete forgets it), the instructions row opens the prompt over
the whole panel (Ctrl+S saves, Esc cancels, the fields return) — and
Esc returns to the list.
Every change is written at once through
`backend.api`; nothing it says is shown here — the row itself shows
where the setting now stands. The sentences (a confirmation, a refusal)
are kept in `notice` for a caller and drawn nowhere.

Editable fields are the write-once primitives; the extractor's own —
states, and every history, the scene's own included — are shown dim
and refused. Every
write goes through `backend.api` with the story it belongs to; the
store writers carry the invalidation, and this screen never calls a
model. `/lore` and `/cast` open the dossier directly on the OPEN story
(scenes and cast tabs); the story browser (`screens.stories`) opens it
on any story, with its list waiting underneath.

Each tab's own rows, preview and actions are its module's (`premise`,
`messages`, `scenes`, `cast`, `tools`), over the skeleton they share
(`base`); the `Dossier` assembled here routes the panes, the keys and
the editing by tab, and wires the application."""

from dataclasses import replace
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
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

from otaku.formatting import flatten, truncate
from otaku.terminal.api import Api, Refused
from otaku.terminal.api.story import prompts as api_prompts
from otaku.terminal.screens.base import base_style, bordered_box, page_step, text_line
from otaku.terminal.screens.dossier.base import Field, Tab
from otaku.terminal.screens.dossier.cast import CastTab, char_fields
from otaku.terminal.screens.dossier.messages import RESUME_OPTIONS, MessagesTab
from otaku.terminal.screens.dossier.premise import PremiseTab
from otaku.terminal.screens.dossier.scenes import ScenesTab, scene_fields
from otaku.terminal.screens.dossier.tools import SHARED_REMINDER, TEXT_KINDS, ToolsTab
from otaku.terminal.tty import latin_key
from otaku.terminal.tty.theme import theme


class Dossier(PremiseTab, MessagesTab, ScenesTab, CastTab, ToolsTab):
    """The five tabs over one story. Standalone it IS the whole screen
    (`browse` — Esc quits); the story browser subclasses it and puts its
    list level in front (`_leave_dossier` is the seam, with the `_extra_*`
    hooks for the list's own keys and dialogs)."""

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
        if self.tab == "tools":
            return self._tool_rows()
        if self.detail is not None:
            return self._field_rows()
        return self._scene_rows() if self.tab == "scenes" else self._cast_rows()

    def _panel_header_text(self) -> StyleAndTextTuples:
        """The fixed header above the panel's text. It lives in its own
        window so it stays put when the text below it becomes the edit
        buffer — editing happens exactly where the text is displayed."""
        if self.tab == "messages":
            if not self.turn_filtered:
                return [("", "")]
            orig = self.turn_filtered[self.cursor]
            return [("class:preview.title", f"{orig + 1}. {self.msgs[orig].role}\n")]
        if self.tab == "tools":
            # The setting's name is the panel's header, so the text under
            # it can become the edit buffer with the header standing; the
            # prompt open over the panel says so in the header.
            if self.settings:
                label = self.settings[self.cursor].label
                if self.prompt_editing:
                    label += " prompt"
                return [("class:preview.title", label + "\n")]
            return [("", "")]
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

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            return [("class:help", " editing — ctrl+s save · esc cancel")]
        if self.choosing:
            return [("class:help", " ↑/↓ choose · enter set · esc cancel")]
        if self.in_filter:
            action = "enter resume" if self.tab == "messages" else "enter open"
            return [
                (
                    "class:help",
                    f" type to filter · ↑/↓ navigate · {action} · esc clear filter",
                )
            ]
        back = self._esc_label()
        if self.tab == "tools":
            if self.in_panel:
                f = self._tool_field()
                verb = {"display": "toggle", "position": "choose"}.get(f.kind if f else "", "edit")
                forget = " · del forget" if f is not None and f.kind == "key" else ""
                return [
                    (
                        "class:help",
                        f" ↑/↓ fields · enter {verb}{forget} · ←/→ tabs · esc back to list",
                    )
                ]
            return [
                (
                    "class:help",
                    f" ↑/↓ navigate · space check · enter/tab fields · ←/→ tabs · esc {back}",
                )
            ]
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
        if self.tab == "cast":
            return [
                (
                    "class:help",
                    f" ↑/↓ navigate · / filter · enter open · del delete · ←/→ tabs · esc {back}",
                )
            ]
        return [
            (
                "class:help",
                f" ↑/↓ navigate · / filter · enter open · ←/→ tabs · esc {back}",
            )
        ]

    def _cursor_line(self) -> int:
        """The visual row of the cursor: the tools list draws a blank row
        before each group (`_tool_rows`), so a cursor sits below its
        index by the gaps above it."""
        if self.tab == "tools" and self.settings:
            return self.cursor + sum(1 for gap in self._gaps() if self.cursor >= gap)
        return self.cursor

    def _rows_count(self) -> int:
        if self.tab == "premise":
            return self._premise_rows
        if self.tab == "messages":
            return len(self.turn_filtered)
        if self.tab == "tools":
            return len(self.settings)
        if self.detail is not None:
            return len(self.fields)
        return len(self.scenes_f) if self.tab == "scenes" else len(self.cast_f)

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
                self.cast_f = list(range(len(self.view.characters)))
            else:
                self.cast_f = [
                    i
                    for i, c in enumerate(self.view.characters)
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
        text or a handful of settings."""
        return self.tab not in ("premise", "tools") and self.detail is None

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
        elif data == " " and self.dossier_on and self.tab == "tools":
            self._toggle_checkbox()

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
            self.detail = ("char", self.view.characters[self.cast_f[self.cursor]].id)
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
        if self.tab == "tools":
            if self.in_panel:
                self._act_on_tool_field()
            else:
                self._enter_panel()
            return
        if self.detail is not None:
            self._start_edit()
        else:
            self._open_detail()

    def _on_escape(self) -> None:
        self.notice = ""
        if self._clear_filter():
            return
        if self.in_panel:
            self.in_panel = False
            return
        if self.detail is not None:
            self.detail = None
            self.fields = []
            self.cursor = min(self._list_pos, max(0, self._rows_count() - 1))
            return
        self._leave_dossier()

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
            listed = [self.view.characters[idx].id for idx in self.cast_f]
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

    def _rebuild_fields(self) -> None:
        assert self.view is not None
        if self.detail is None:
            self.fields = []
            return
        kind, target = self.detail
        if kind == "scene":
            self.fields = scene_fields(self.view, target)
        else:
            self.fields = char_fields(self.view, target)
        if self.cursor >= len(self.fields):
            self.cursor = max(0, len(self.fields) - 1)

    def _edit_guard(self) -> str | None:
        """Why the text under the cursor can't be edited, or None to go."""
        if self.tab == "premise":
            return None
        if self.tab == "messages":
            return None if self.turn_filtered else "nothing to edit"
        if self.tab == "tools":
            focused = self._tool_field() if self.in_panel else None
            if focused is None:
                return "nothing to edit"
            return None if focused.kind in TEXT_KINDS else "not a text"
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
            # without the whitespace at its edges, the row's join, which the
            # save keeps (`stories.update_message`) — as the page's field does
            return self.msgs[self.turn_filtered[self.cursor]].body.strip()
        if self.tab == "tools":
            focused = self._tool_field()
            # the key's field shows where it stands, never the key: a new
            # one is typed from nothing
            if focused is None or focused.kind == "key":
                return ""
            return focused.text
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
        control = self._edit_control
        if self.tab == "premise":
            control = self._premise_edit_control
        elif self.tab == "tools":
            focused = self._tool_field()
            self.prompt_editing = focused is not None and focused.kind == "prompt"
            control = self._prompt_edit_control if self.prompt_editing else self._tool_edit_control
        self.application.layout.focus(control)

    def _finish_edit(self, *, save: bool) -> None:
        """Ctrl+S applies the buffer through `backend.api`; Esc discards."""
        self.editing = False
        prompt_edited, self.prompt_editing = self.prompt_editing, False
        self.application.layout.focus(self._items_control)
        if not save:
            self.notice = "(cancelled)"
            return
        new = self.edit_buffer.text.rstrip("\n")
        if new == self._edit_text():
            self.notice = "(unchanged)"
            return
        try:
            if self.tab == "premise":
                self.notice = self.api.stories.update(self.story_id, premise=new).notice
                self.premise = new  # "" is a save too: the premise cleared
                return
            if self.tab == "messages":
                orig = self.turn_filtered[self.cursor]
                m = self.msgs[orig]
                self.api.stories.messages.update(self.story_id, m.id, body=new)
                self.msgs[orig] = replace(m, body=new)
                self.notice = "saved"
                return
            if self.tab == "tools":
                setting = self.settings[self.cursor]
                focused = self._tool_field()
                if prompt_edited and setting.name in self.prompts:
                    self.notice = api_prompts.write_prompt(self.api, setting, new)
                    self._reload_settings()
                elif focused is not None and focused.kind == "key":
                    self.notice = self.api.integrations.web_search.update(api_key=new).notice
                    self._reload_settings()
                elif setting.name == SHARED_REMINDER:
                    self.notice = self.api.shared_reminder.update(text=new).notice
                    self._reload_settings()
                else:
                    self._write_setting(setting, reminder_text=new)
                return
            f = self.fields[self.cursor]
            _update_field(self.api, self.story_id, f, new)
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
        window = self._edit_window
        if self.tab == "premise":
            window = self._premise_edit_window
        elif self.tab == "tools":
            window = self._prompt_edit_window if self.prompt_editing else self._tool_edit_window
        info = window.render_info
        if info is not None and info.window_width > 0:
            return info.window_width
        return max(10, self._preview_inner_width())

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

    def _build_application(self) -> Application[None]:
        kb = KeyBindings()
        resuming = Condition(lambda: self.confirming_resume)

        # While the resume dialog is up: arrows walk the options, Enter
        # confirms the highlighted one, Esc closes back into the browser.
        @kb.add("escape", eager=True, filter=resuming)
        def _resume_esc(event: Any) -> None:
            self.confirming_resume = False

        @kb.add("up", filter=resuming)
        def _resume_up(event: Any) -> None:
            self.resume_choice = (self.resume_choice - 1) % len(RESUME_OPTIONS)

        @kb.add("down", filter=resuming)
        def _resume_down(event: Any) -> None:
            self.resume_choice = (self.resume_choice + 1) % len(RESUME_OPTIONS)

        @kb.add("enter", filter=resuming)
        def _resume_enter(event: Any) -> None:
            self._do_resume()

        # While the depth dropdown is up: arrows walk the places, Enter
        # sets the one under the cursor, Esc leaves the setting as it was.
        choosing = Condition(lambda: self.choosing)

        @kb.add("escape", eager=True, filter=choosing)
        def _choose_esc(event: Any) -> None:
            self._choose_place(confirm=False)

        @kb.add("up", filter=choosing)
        def _choose_up(event: Any) -> None:
            self.choice = (self.choice - 1) % len(self.settings[self.cursor].allowed_positions)

        @kb.add("down", filter=choosing)
        def _choose_down(event: Any) -> None:
            self.choice = (self.choice + 1) % len(self.settings[self.cursor].allowed_positions)

        @kb.add("enter", filter=choosing)
        def _choose_enter(event: Any) -> None:
            self._choose_place(confirm=True)

        # While the delete confirm is up: only y/n/esc do anything.
        deleting = Condition(lambda: self.confirming_cast_delete)

        @kb.add("escape", eager=True, filter=deleting)
        def _cast_delete_esc(event: Any) -> None:
            self.confirming_cast_delete = False

        @kb.add(Keys.Any, filter=deleting, eager=True)
        def _cast_delete_any(event: Any) -> None:
            key = latin_key(event.data) if event.data else ""
            if key == "y":
                self._do_cast_delete()
            elif key == "n":
                self.confirming_cast_delete = False

        # In the tools panel the arrows walk the setting's fields, not the
        # list; Enter, Space and Esc are the standard keys' and read
        # `in_panel` themselves.
        in_panel = Condition(lambda: self.in_panel)

        @kb.add("up", filter=~choosing & in_panel)
        def _field_up(event: Any) -> None:
            self._move_field(-1)

        @kb.add("down", filter=~choosing & in_panel)
        def _field_down(event: Any) -> None:
            self._move_field(1)

        idle = ~resuming & ~choosing & ~deleting & self._extra_idle()
        self._standard_keys(kb, when=idle & ~in_panel)

        # What the panel keeps of the standard keys: Enter acts on the
        # field, Space toggles a checkbox, Esc returns to the list.
        @kb.add("enter", filter=idle & in_panel)
        def _panel_enter(event: Any) -> None:
            self._on_enter()

        @kb.add("escape", eager=True, filter=idle & in_panel)
        def _panel_esc(event: Any) -> None:
            self._on_escape()

        @kb.add(" ", filter=idle & in_panel)
        def _panel_space(event: Any) -> None:
            self._toggle_checkbox()

        @kb.add("delete", filter=idle & in_panel)
        def _panel_delete(event: Any) -> None:
            self._forget_key()

        in_dossier = Condition(lambda: self.dossier_on)

        # ←/→ are the way between tabs.
        @kb.add("right", filter=idle & in_dossier)
        def _tab_key(event: Any) -> None:
            self._switch_tab(1)

        @kb.add("left", filter=idle & in_dossier)
        def _stab_key(event: Any) -> None:
            self._switch_tab(-1)

        # Tab on the tools list moves into the panel as Enter does; the
        # way back is Esc alone.
        on_tools_list = Condition(lambda: self.tab == "tools" and not self.in_panel)

        @kb.add("tab", filter=idle & in_dossier & on_tools_list)
        def _into_panel(event: Any) -> None:
            self._enter_panel()

        # Del on a cast row asks to delete the character. The key macOS
        # captions "delete" arrives as backspace, honoured wherever no
        # filter is open for it to edit — after the standard keys, so it
        # outranks their no-op, as on the story list.
        on_cast_row = Condition(lambda: self._cast_target() is not None)

        @kb.add("delete", filter=idle & on_cast_row)
        def _cast_delete_key(event: Any) -> None:
            self._request_cast_delete()

        @kb.add("backspace", filter=idle & on_cast_row & Condition(lambda: not self.in_filter))
        def _cast_delete_backspace(event: Any) -> None:
            self._request_cast_delete()

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
        self._items_control = self._make_items_control(
            focusable=True, cursor_line=self._cursor_line
        )
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

        # The tools panel: a text between the fields above and below it,
        # edited where it stands — the third window over the one buffer.
        self._tool_edit_control = BufferControl(
            buffer=self.edit_buffer, focusable=True, key_bindings=edit_motion
        )
        # Sized to the text, as the read window is: the editor grows only
        # as the text does, and the fields below stay where they were.
        self._tool_edit_window = Window(
            self._tool_edit_control,
            wrap_lines=True,
            dont_extend_height=True,
            style="class:preview.body",
        )
        # A tool's prompt is edited over the WHOLE panel: the fields give
        # way to one editor, the panel's height, until it is saved or
        # cancelled — a prompt is paragraphs, and a row's seat is not.
        self._prompt_edit_control = BufferControl(
            buffer=self.edit_buffer, focusable=True, key_bindings=edit_motion
        )
        self._prompt_edit_window = Window(
            self._prompt_edit_control, wrap_lines=True, style="class:preview.body"
        )
        tools_shown = Condition(lambda: self.dossier_on and self.tab == "tools")
        tools_text = Condition(self._tool_has_text)
        prompt_editing = Condition(lambda: self.prompt_editing)
        tools_panel = HSplit(
            [
                ConditionalContainer(
                    Window(
                        FormattedTextControl(text=self._tool_above, show_cursor=False),
                        wrap_lines=True,
                        dont_extend_height=True,
                        always_hide_cursor=True,
                        style="class:preview.body",
                    ),
                    filter=~prompt_editing,
                ),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(text=self._tool_text, show_cursor=False),
                        wrap_lines=True,
                        dont_extend_height=True,
                        always_hide_cursor=True,
                        style="class:preview.body",
                    ),
                    filter=tools_text & ~editing,
                ),
                ConditionalContainer(
                    self._tool_edit_window, filter=tools_text & editing & ~prompt_editing
                ),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(text=self._tool_below, show_cursor=False),
                        wrap_lines=True,
                        dont_extend_height=True,
                        always_hide_cursor=True,
                        style="class:preview.body",
                    ),
                    filter=tools_text & ~prompt_editing,
                ),
                ConditionalContainer(self._prompt_edit_window, filter=prompt_editing),
                Window(char=" ", always_hide_cursor=True),  # the rest of the box
            ]
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
                        or (self.tab == "tools" and bool(self.settings))
                        or (self.detail is not None and bool(self.fields))
                    )
                )
            ),
            editing=panel_editing,
            edit_window=self._edit_window,
            alternate=(tools_panel, tools_shown),
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
        cast_delete_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._cast_delete_text, show_cursor=False),
                width=D(min=44, max=70, preferred=60),
                height=D.exact(7),
                style="class:dialog.body",
                border_style="class:dialog.border",
            ),
            filter=deleting,
        )

        root = VSplit([left_pane, preview_side])
        floats: list[AnyContainer] = [resume_dialog, cast_delete_dialog, *self._extra_floats()]
        return self._finish_application(root, bindings, _style(), floats=floats)


def browse(api: Api, story_id: int, tab: Tab = "scenes") -> tuple[int, str] | None:
    """Open the dossier on a story — on the scenes tab (/lore) or the
    cast tab (/cast); edits are written as they are saved. On a landing
    (Enter in the messages tab) the land is EXECUTED, and the story it
    settled in returned with its line for the caller to play and print;
    None when the reader only left (Esc/Ctrl+C)."""
    screen = Dossier(api)
    screen._open_dossier(story_id, tab)
    return screen.run()


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
        }
    )


def _update_field(api: Api, story_id: int, field: Field, text: str) -> None:
    """A corrected field written to the resource it is a field of — the
    view's address (`Field.kind`, `Field.target`) to that resource's
    update. A derived field is never editable here, so it never comes."""
    match field.kind:
        case "scene-title":
            api.stories.scenes.update(story_id, field.target, title=text)
        case "scene-summary":
            api.stories.scenes.update(story_id, field.target, summary=text)
        case "description":
            api.stories.characters.update(story_id, field.target, description=text)
        case "card":
            api.stories.characters.update(story_id, field.target, card=text)
        case "entry":
            api.stories.journals.update(story_id, field.target, entry=text)
        case _:
            raise ValueError(f"{field.kind} has no write")
