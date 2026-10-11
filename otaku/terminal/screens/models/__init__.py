"""The model picker + provider panel — opened by the launch-time pick
and `/model`.

A single full-screen Application, split 1:1. The left side lists every
model from every reachable provider — grouped under provider captions
in the panel's order (`api.providers.list`, the one source of
the captions), bare model names, providers with no models absent —
color-coded by load state. Only the local providers are waited for before
the screen opens; each cloud catalog's rows arrive when it answers, the
panel naming what is still loading. The user can:
    - move the cursor (↑/↓/PgUp/PgDn/Home/End)
    - type-to-filter by pressing `/` first; Esc cancels the filter
    - toggle load state with `l`/`u` (a confirm dialog, then a spinner)
    - pin lore extraction to the highlighted model with `x`, or on the
      model pinned, unpin it; executed at once, the mark the answer
    - press Enter to pick the highlighted model — the switch is EXECUTED
      here (`api.app.update`, the screens' one ownership
      rule) and its notice returned. A model that is not loaded yet is
      loaded first (same modal + spinner) and only switched on success.
    - press Esc to leave without picking.

Loaded models render bold; not-loaded muted. The story's model reads
"[ writing ]" against its figures and the one saved for extraction
"[ extraction ]", both on a model that is both. The cursor restores to the last-used model
on open. A provider without load/unload serves its models
statically: they all show as loaded, Enter picks them directly, and the
l/u keys (and their help entries) disappear on such a row.

The right side is the provider panel: each provider's caption with its
`URL:` and `API key:` fields, the key's value never displayed, the
cloud catalogs' url fixed (shown dimmed, never walkable). Tab switches
sides; ↑/↓ walk the fields; Enter edits the highlighted one in place
(←/→ move the cursor, paste works, Enter saves) while TYPING replaces
it — the editor opens empty with the keystroke in it, because typing
over a highlighted value is what replacing one looks like everywhere
else, and Esc restores from either way in; a paste onto a CLOSED
field (Cmd+V, Ctrl+V) sets it outright — a url or a key is pasted whole
rather than composed — while inside the editor a paste is an ordinary
paste; Delete on a field, outside the editor, clears it. Saves go
through `api.providers.update` (sealed keys, surgical writes,
live registry — all the backend's), and the provider's models are
re-listed under the new configuration.

The two sides are their modules' (`models`, `providers`), over the state
they share (`base`); the `ModelPicker` assembled here routes the keys by
side and wires the application."""

from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    ScrollOffsets,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.terminal.api import Api
from otaku.terminal.api import app as api_app
from otaku.terminal.screens.base import base_style, bordered_box, text_line
from otaku.terminal.screens.models.models import ModelList
from otaku.terminal.screens.models.providers import ProviderPanel
from otaku.terminal.tty import clipboard, latin_key
from otaku.terminal.tty.theme import theme


class ModelPicker(ModelList, ProviderPanel):
    """The picker as one screen: the models list beside the provider
    panel, with the header, the help and the layout that hold the two."""

    def _header_text(self) -> StyleAndTextTuples:
        n, total = len(self.filtered), len(self.all)
        left = f" Models ({n} of {total})" if n != total else f" Models ({n})"
        # What is still loading is said in the provider panel, on the row
        # of the provider it is about (see `_providers_text`).
        head: StyleAndTextTuples = [("class:header", left)]
        ram = self.api.app.machine.get().memory
        if not ram:
            return head  # a machine that will not say: no gauge at all

        # Right-align RAM to the rows' right edge.
        gap = " " * max(2, self._row_width() - len(left) - len(ram))
        return [*head, ("class:header.detail", gap + ram)]

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            txt = " editing — enter save · esc cancel"
        elif self.side == "providers":
            txt = " ↑/↓ navigate · enter edit · del clear key · tab models · esc back"
        elif self.in_filter:
            txt = " type to filter · ↑/↓ navigate · enter select · esc clear filter"
        else:
            segments = ["↑/↓ navigate", "/ filter"]
            # Load/unload only appear when the SELECTED model's provider
            # supports them.
            if self.filtered and self.filtered[self.cursor].can_manage:
                segments += ["l load", "u unload"]
            if self.filtered:
                # On the model pinned for extraction, the way back to the
                # story's model; on any other, the pin.
                if self.filtered[self.cursor].full_spec == api_app.specs(self.api)[1]:
                    segments.append("x unset extraction model")
                else:
                    segments.append("x use for extraction")
            segments += ["enter select", "tab providers", "esc quit"]
            txt = " " + " · ".join(segments)
        return [("class:help", txt)]

    def _cursor_line(self) -> int:
        """Visual row of the cursor as the LAST paint drew it — the
        control reads this after `_items_text`, over the same rows."""
        return self._drawn_cursor_line

    def _rows_count(self) -> int:
        return len(self.fields) if self.side == "providers" else len(self.filtered)

    def _move_cursor(self, delta: int) -> None:
        # The panel side moves its OWN cursor — both sides stay visible,
        # so the base's one integer keeps the models side.
        self.notice = ""
        if self.side == "providers":
            self.field_cursor = max(0, min(len(self.fields) - 1, self.field_cursor + delta))
        else:
            super()._move_cursor(delta)

    def _type(self, data: str) -> None:
        # Letters and the `/` filter belong to the models side.
        if self.side == "models":
            super()._type(data)
            return
        # On a field, a keystroke IS the edit: it opens on an empty value
        # with what was typed already in it. Typing over a highlighted
        # field is replacing what is there — the same as everywhere else
        # a value is selected — and Enter on the field is the other way
        # in, for correcting rather than replacing. Esc restores either.
        self._start_field_edit(blank=True)
        if self.editing:
            self.edit_buffer.insert_text(data)

    def _settle_side(self) -> None:
        """Move to the provider panel once every provider has answered and
        none of them offered a model. The models side is then a message
        rather than a list, and the panel is the one door to fixing that —
        so the cursor is already on it rather than one Tab away. Only
        while nobody is still answering: an empty list mid-listing is the
        question, not the answer."""
        if not self.pending and not self.all:
            self.side = "providers"

    def _toggle_side(self) -> None:
        self.notice = ""
        self._clear_filter()
        self.side = "providers" if self.side == "models" else "models"

    def _on_enter(self) -> None:
        if self.side == "providers":
            self._start_field_edit()
            return
        if not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if entry.loaded or not entry.can_manage:
            # Loaded — or a statically served engine (llama.cpp, a
            # KoboldCpp between admin swaps): the engine serves what it
            # serves, so Enter just picks.
            self._finish_pick(entry)
            return
        # Not loaded — load it, then switch on success.
        self._start_action(entry, load=True, exit_on_success=True)

    def _on_escape(self) -> None:
        if self.side == "providers":
            self.notice = ""
            # Back to the models side — unless there is no models side to
            # go back to. A picker that listed nothing and has nothing
            # left to hear from moved the cursor here itself, and Esc
            # there means leave: stepping onto an empty list first is a
            # keystroke spent on nothing.
            if not self.pending and not self.all:
                get_app().exit()
                return
            self.side = "models"
            return
        if not self._clear_filter():
            get_app().exit()

    def _on_key(self, data: str) -> None:
        key = latin_key(data)
        if key == "l":
            self._request_load()
        elif key == "u":
            self._request_unload()
        elif key == "x":
            self._toggle_extraction()

    def _build_application(self) -> Application[None]:
        kb = KeyBindings()

        idle = Condition(
            lambda: not self.busy and self.busy_error is None and self.confirming_action is None
        )
        confirming = Condition(lambda: self.confirming_action is not None)
        showing_error = Condition(lambda: self.busy_error is not None)

        # Dismiss any-key while the error dialog is up.
        @kb.add(Keys.Any, filter=showing_error, eager=True)
        def _dismiss_err(event: Any) -> None:
            self.busy_error = None

        # While the confirm dialog is up: only y/n/esc do anything.
        @kb.add("escape", eager=True, filter=confirming)
        def _confirm_esc(event: Any) -> None:
            self._confirm_no()

        @kb.add(Keys.Any, filter=confirming, eager=True)
        def _confirm_any(event: Any) -> None:
            key = latin_key(event.data) if event.data else ""
            if key == "y":
                self._confirm_yes()
            elif key == "n":
                self._confirm_no()

        self._standard_keys(kb, when=idle)

        @kb.add("tab", filter=idle)
        def _tab(event: Any) -> None:
            self._toggle_side()

        @kb.add("delete", filter=idle)
        def _clear(event: Any) -> None:
            self._clear_field()

        @kb.add("c-v", filter=idle)
        def _set_from_clipboard(event: Any) -> None:
            # One key from clipboard to configured: no Enter to open the
            # field, none to settle it.
            self._set_field(clipboard.paste())

        @kb.add(Keys.BracketedPaste, filter=idle)
        def _pasted(event: Any) -> None:
            # However the terminal chose to deliver it: many turn Ctrl+V
            # into a paste of their own rather than sending the control
            # byte, and then the app never sees `c-v` at all. On the
            # models side a paste is filter text, as it has always been.
            if self.side == "providers":
                self._set_field(clipboard.one_line(event.data))
            else:
                self._type(clipboard.one_line(event.data))

        # While a field is being edited, every binding above is suspended —
        # keystrokes belong to the inline editor. Ctrl+C stays live.
        editing = Condition(lambda: self.editing)
        edit_kb = KeyBindings()

        @edit_kb.add("enter", filter=editing)
        def _save(event: Any) -> None:
            self._finish_field_edit(save=True)

        @edit_kb.add("escape", filter=editing, eager=True)
        def _cancel(event: Any) -> None:
            self._finish_field_edit(save=False)

        @edit_kb.add("backspace", filter=editing)
        def _erase(event: Any) -> None:
            self.edit_buffer.delete_before_cursor(1)

        @edit_kb.add("delete", filter=editing)
        def _erase_ahead(event: Any) -> None:
            self.edit_buffer.delete(1)

        @edit_kb.add("left", filter=editing)
        def _left(event: Any) -> None:
            self.edit_buffer.cursor_position = max(0, self.edit_buffer.cursor_position - 1)

        @edit_kb.add("right", filter=editing)
        def _right(event: Any) -> None:
            buffer = self.edit_buffer
            buffer.cursor_position = min(len(buffer.text), buffer.cursor_position + 1)

        @edit_kb.add("home", filter=editing)
        @edit_kb.add("c-a", filter=editing)
        def _home(event: Any) -> None:
            self.edit_buffer.cursor_position = 0

        @edit_kb.add("end", filter=editing)
        @edit_kb.add("c-e", filter=editing)
        def _end(event: Any) -> None:
            self.edit_buffer.cursor_position = len(self.edit_buffer.text)

        @edit_kb.add("c-u", filter=editing)
        def _wipe(event: Any) -> None:
            self.edit_buffer.reset()

        # Inside the editor a paste is an ordinary paste — inserted at the
        # cursor, Enter still saves. Only a CLOSED field is set outright.
        @edit_kb.add(Keys.BracketedPaste, filter=editing)
        def _paste(event: Any) -> None:
            self.edit_buffer.insert_text(clipboard.one_line(event.data))

        @edit_kb.add("c-v", filter=editing)
        def _paste_key(event: Any) -> None:
            self.edit_buffer.insert_text(clipboard.paste())

        @edit_kb.add(Keys.Any, filter=editing)
        def _typed(event: Any) -> None:
            if event.data and event.data.isprintable():
                self.edit_buffer.insert_text(event.data)

        always_kb = KeyBindings()

        @always_kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        bindings = merge_key_bindings([ConditionalKeyBindings(kb, ~editing), edit_kb, always_kb])

        items_window = Window(
            content=self._make_items_control(cursor_line=self._cursor_line),
            wrap_lines=False,
            always_hide_cursor=True,
            # The window spans its whole half, so its style paints every
            # cell the rows leave bare. It must be a class that carries no
            # ATTRIBUTE: a window's style is what its fragments build on,
            # and each of them overrides only the colors it names — a `dim`
            # here would grey out every row and caption in the pane.
            # Two lines of margin above the cursor: exactly the caption
            # and its blank, so a group's header scrolls into view when
            # the cursor stands on the group's first model.
            scroll_offsets=ScrollOffsets(top=2),
            style="class:row.plain",
        )

        # Bottom row: the filter input when filtering, otherwise the help
        # line. Same height in both states so the list above doesn't shift.
        bottom_row = VSplit(
            [
                text_line(self._filter_text, filter=Condition(lambda: self.in_filter)),
                text_line(
                    self._help_text,
                    style="class:help",
                    filter=Condition(lambda: not self.in_filter),
                ),
            ]
        )

        # This picker assembles its own left pane, so it records the
        # items window itself (see ListScreen._max_row_content_width).
        self._items_window = items_window
        left_pane = HSplit(
            [
                text_line(self._header_text, style="class:header"),
                Window(height=1, char=" ", always_hide_cursor=True),
                items_window,
                Window(height=1, char=" ", always_hide_cursor=True),
                bottom_row,
            ],
            width=D(weight=1),
        )

        # Named _preview_window so the base class's measured pane width
        # serves the field cells.
        self._preview_window = Window(
            FormattedTextControl(
                text=self._providers_text,
                get_cursor_position=lambda: Point(0, self._panel_cursor_line()),
                show_cursor=False,
            ),
            wrap_lines=True,
            always_hide_cursor=True,
            scroll_offsets=ScrollOffsets(top=2),  # caption + blank, as on the left
            style="class:preview.body",
        )
        provider_panel = bordered_box(
            self._preview_window, width=D(weight=1), style="class:preview.body"
        )

        busy_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._dialog_text, show_cursor=False),
                width=D(min=60, max=80, preferred=64),
                height=D.exact(9),
                style="class:dialog.body",
                border_style="class:dialog.border",
                wrap=False,  # truncate long model names instead of wrapping
            ),
            filter=Condition(lambda: self.busy or self.busy_error is not None),
        )

        confirm_dialog = ConditionalContainer(
            content=bordered_box(
                FormattedTextControl(text=self._confirm_text, show_cursor=False),
                width=D(min=60, max=80, preferred=64),
                height=D.exact(7),
                style="class:dialog.body",
                border_style="class:dialog.border",
                wrap=False,
            ),
            filter=confirming,
        )

        dialog = HSplit([busy_dialog, confirm_dialog])
        root = VSplit([left_pane, self._preview_gap(), provider_panel])
        return self._finish_application(root, bindings, _style(), floats=[dialog])


def pick(api: Api, initial_spec: str | None = None) -> str | None:
    """Show the picker; a confirmed choice is EXECUTED here
    (the settings' model — the screens' one ownership rule) and
    the switch notice returned; None when the user leaves without
    choosing — silently: at launch the app opens model-less and the
    first turn explains itself, from /model everything stays as it was.
    Opens even with nothing to list — the panel is the one door to
    configuring a provider.

    NOTHING is waited for: the screen opens on an empty list and every
    configured provider answers into it, each saying "loading…" in the
    panel until it does. A local engine is usually quick, but usually is
    not always — a laptop that has gone to sleep, an ollama that is
    starting, a url pointing at nothing — and a picker that opens after
    the slowest of them reads as a hang. The rows land as they arrive,
    and a remembered model takes the cursor whenever its provider is the
    one that answered."""
    listing = api.providers.list(probe=False)
    picker = ModelPicker(
        api,
        list(listing.providers),
        [],
        initial_spec=initial_spec,
        # Every CONFIGURED provider, not every supported one: one with no
        # section has nothing to ask and would spend a thread learning it.
        fetch=sorted(provider.id for provider in listing.providers if provider.configured),
        connected=set(),
    )
    return picker.run()


def _style() -> Style:
    """Shared chrome from `base_style` plus the picker's row, preview and
    panel overrides, in the shades the terminal background asked for."""
    colors = theme()
    panel = f"bg:{colors.panel.style}"
    band = f"bg:{colors.selection.style}"
    raised = f"bg:{colors.raised.style}"
    return Style.from_dict(
        {
            **base_style(),
            "row.loaded": f"bold fg:{colors.text.style} {panel}",
            "row.notloaded": f"dim fg:{colors.muted.style} {panel}",
            "row.plain": f"fg:{colors.text.style} {panel}",
            "row.selected.loaded": f"bold fg:{colors.ink.style} {band}",
            "row.selected.notloaded": f"dim fg:{colors.ink.style} {band}",
            "row.selected.plain": f"fg:{colors.ink.style} {band}",
            # the light parts of the bold header
            "header.detail": f"nobold fg:{colors.title.style} {panel}",
            "row.selected": f"bold fg:{colors.ink.style} {band}",
            "dialog.error": f"bold fg:{colors.error.style} {raised}",
            "preview.title": f"bold fg:{colors.title.style} {panel}",
            "preview.body": f"fg:{colors.text.style} {panel}",
            "preview.muted": f"dim fg:{colors.muted.style} {panel}",  # the unloaded models' look
            # The text cursor, drawn as a block: whatever it sits on,
            # inverted — the one styling that needs no color at all.
            "field.cursor": "reverse",
            "tick": f"fg:{colors.ok.style} {panel}",
        }
    )
