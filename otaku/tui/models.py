"""Model picker — opened by the bare `otaku` invocation and `/model`.

A single full-screen Application listing every model from every configured
provider, color-coded by load state. The user can:
    - move the cursor (↑/↓/PgUp/PgDn/Home/End)
    - type-to-filter by pressing `/` first; Esc cancels the filter
    - toggle load state with `l`/`u` (a confirm dialog, then a spinner)
    - press Enter to pick the highlighted model and exit. A model that is
      not loaded yet is loaded first (same modal + spinner) and only picked
      on success.
    - press Esc to cancel without picking.

Loaded models render bold; not-loaded muted. The cursor restores to the
last-used model on open. A backend without load/unload serves its models
statically: they all show as loaded, Enter picks them directly, and the
l/u keys (and their help entries) disappear on such a row.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import psutil
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from otaku.formatting import format_size, truncate
from otaku.providers.base import ManagedClient
from otaku.providers.registry import Registry as ProviderRegistry
from otaku.terminal import latin_key
from otaku.terminal.spinner import FRAMES as SPINNER_FRAMES
from otaku.tui.screen import BASE_STYLE, ListScreen, bordered_box, term_cols, text_line

_STYLE = Style.from_dict(
    {
        **BASE_STYLE,
        "row.loaded": "bold fg:#000000 bg:#ffffff",
        "row.notloaded": "fg:#767676 bg:#ffffff",
        "row.selected.loaded": "bold fg:#000000 bg:#e4e4e4",
        "row.selected.notloaded": "bold fg:#767676 bg:#e4e4e4",
        "separator": "fg:#bcbcbc bg:#ffffff",
        "dialog.error": "bold fg:#c0392b bg:#ffffff",
    }
)

_ROW_HEAD_LIMIT = 100


@dataclass
class ModelEntry:
    full_spec: str  # "provider/model"
    provider_name: str
    model: str
    loaded: bool
    can_load_unload: bool = True  # False → served statically
    size_bytes: int | None = None  # None when the provider doesn't expose it


class ModelPicker(ListScreen):
    def __init__(
        self,
        providers: ProviderRegistry,
        entries: list[ModelEntry],
        initial_spec: str | None = None,
    ) -> None:
        super().__init__()
        self.providers = providers
        self.all: list[ModelEntry] = list(entries)
        self.filtered: list[ModelEntry] = list(entries)
        self.cursor: int = 0

        if initial_spec is not None:
            for i, entry in enumerate(self.all):
                if entry.full_spec == initial_spec:
                    self.cursor = i
                    break

        # Confirmation state (set when the user presses load/unload)
        self.confirming_action: str | None = None  # "load" or "unload"
        self.confirming_entry: ModelEntry | None = None

        # Modal state for the in-flight HTTP action
        self.busy: bool = False
        self.busy_action: str = ""  # "Loading" or "Unloading"
        self.busy_target: str = ""  # full_spec of the model being acted on
        self.busy_error: str | None = None
        self.spinner_frame: int = 0
        self._exit_on_success: bool = False
        self._lock = threading.Lock()

        self.result: str | None = None  # full_spec on confirm, None on cancel
        self.app = self._build_app()

    def run(self) -> str | None:
        if not self.all:
            return None
        self.app.run()
        return self.result

    # ---------- text content ----------

    def _table_width(self) -> int:
        """Rendered width of the model table (row prefix + label + gap +
        size), so the header can right-align to its right edge."""
        if not self.filtered:
            return 0
        max_label = max(len(truncate(e.full_spec, _ROW_HEAD_LIMIT)) for e in self.filtered)
        max_size = max(len(format_size(e.size_bytes)) for e in self.filtered)
        return 4 + max_label + 2 + max_size  # 4 = the "    " / "  > " row prefix

    def _header_text(self) -> StyleAndTextTuples:
        n, total = len(self.filtered), len(self.all)
        left = f" Models ({n} of {total})" if n != total else f" Models ({n})"
        try:
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            ram = f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({vm.percent:.0f}%)"
        except Exception:
            ram = ""

        if not ram:
            return [("class:header", left)]

        # Right-align RAM to the table's right edge (never past the screen).
        width = min(self._table_width(), term_cols())
        gap = " " * max(2, width - len(left) - len(ram))
        return [("class:header", left + gap + ram)]

    def _items_text(self) -> StyleAndTextTuples:
        if not self.filtered:
            msg = "(no matches)" if self.query else "(no models)"
            return [("class:muted", "  " + msg)]

        # Pre-compute label + size column widths so the size column right-
        # aligns and the selection background extends across the full row.
        labels = [truncate(e.full_spec, _ROW_HEAD_LIMIT) for e in self.filtered]
        sizes = [format_size(e.size_bytes) for e in self.filtered]
        max_label = max((len(s) for s in labels), default=0)
        max_size = max((len(s) for s in sizes), default=0)
        rule = "    " + "─" * (max_label + 2 + max_size)

        out: StyleAndTextTuples = []
        prev_provider: str | None = None
        for i, entry in enumerate(self.filtered):
            if prev_provider is not None and entry.provider_name != prev_provider:
                out.append(("class:separator", rule + "\n"))
            prev_provider = entry.provider_name
            label = labels[i].ljust(max_label)
            size = sizes[i].rjust(max_size)
            row = f"{label}  {size}"  # 2-space gap before size
            selected = i == self.cursor
            if selected:
                klass = (
                    "class:row.selected.loaded" if entry.loaded else "class:row.selected.notloaded"
                )
                out.append((klass, f"  > {row}\n"))
            else:
                klass = "class:row.loaded" if entry.loaded else "class:row.notloaded"
                out.append((klass, f"    {row}\n"))
        return out

    def _cursor_line(self) -> int:
        """Visual row of the cursor, counting the provider separators
        rendered above it — keeps scroll-to-cursor aligned with the extra
        lines `_items_text` inserts between provider groups."""
        seps = sum(
            1
            for i in range(1, min(self.cursor + 1, len(self.filtered)))
            if self.filtered[i].provider_name != self.filtered[i - 1].provider_name
        )
        return self.cursor + seps

    def _help_text(self) -> StyleAndTextTuples:
        if self.in_filter:
            txt = " type to filter · ↑/↓ navigate · enter select · esc clear filter"
        else:
            segments = ["↑/↓ navigate", "/ filter"]
            # Load/unload only appear when the SELECTED model's backend
            # supports them.
            if self.filtered and self.filtered[self.cursor].can_load_unload:
                segments += ["l load", "u unload"]
            segments += ["enter select", "esc quit"]
            txt = " " + " · ".join(segments)
        return [("class:help", txt)]

    def _confirm_text(self) -> StyleAndTextTuples:
        if not self.confirming_action or self.confirming_entry is None:
            return [("", "")]
        verb = "Load" if self.confirming_action == "load" else "Unload"
        # Dialog inner content area is ~54 chars wide at preferred 60.
        spec = truncate(self.confirming_entry.full_spec, 40)
        return [
            ("class:dialog.title", f"{verb} model {spec!r}?\n"),
            ("class:dialog.body", "\n"),
            ("class:dialog.muted", "y to confirm     n / esc to cancel"),
        ]

    def _dialog_text(self) -> StyleAndTextTuples:
        if self.busy_error is not None:
            return [
                ("class:dialog.title", f"{self.busy_action} failed\n"),
                ("class:dialog.body", "\n"),
                ("class:dialog.error", self.busy_error[:120] + "\n"),
                ("class:dialog.body", "\n"),
                ("class:dialog.muted", "press any key to dismiss"),
            ]
        spin = SPINNER_FRAMES[self.spinner_frame % len(SPINNER_FRAMES)]
        return [
            ("class:dialog.title", f"{self.busy_action}\n"),
            ("class:dialog.body", "\n"),
            ("class:dialog.body", truncate(self.busy_target, 50) + "\n"),
            ("class:dialog.body", "\n"),
            ("class:dialog.muted", f"  {spin}  please wait…"),
        ]

    # ---------- behavior ----------

    def _rows_count(self) -> int:
        return len(self.filtered)

    def _cursor(self) -> int:
        return self.cursor

    def _set_cursor(self, value: int) -> None:
        self.cursor = value

    def _refilter(self) -> None:
        q = self.query.strip().lower()
        if not q:
            self.filtered = list(self.all)
        else:
            self.filtered = [e for e in self.all if q in e.full_spec.lower()]
        if self.cursor >= len(self.filtered):
            self.cursor = max(0, len(self.filtered) - 1)

    def _start_action(self, entry: ModelEntry, *, load: bool, exit_on_success: bool) -> None:
        with self._lock:
            if self.busy:
                return
            self.busy = True
            self.busy_action = "Loading" if load else "Unloading"
            self.busy_target = entry.full_spec
            self.busy_error = None
            self.spinner_frame = 0
            self._exit_on_success = exit_on_success

        def worker() -> None:
            err: str | None = None
            try:
                client = self.providers.get_client(entry.provider_name)
                if not isinstance(client, ManagedClient):
                    raise RuntimeError(f"{entry.provider_name} cannot load or unload models")
                if load:
                    client.load_model(entry.model)
                else:
                    client.unload_model(entry.model)
                # ollama's /api/ps lags ~100-200ms behind /api/generate's
                # response — without this sleep the refresh below sees the
                # model still loaded after an unload.
                time.sleep(0.2)
                # Refresh load state for this provider only.
                fresh = client.get_loaded_models()
                for e in self.all:
                    if e.provider_name == entry.provider_name:
                        e.loaded = e.model in fresh
            except Exception as ex:
                err = f"{type(ex).__name__}: {ex}"

            with self._lock:
                self.busy = False
                self.busy_error = err
                exit_now = err is None and self._exit_on_success
                if exit_now:
                    self.result = entry.full_spec
            try:
                if exit_now:
                    self.app.exit()
                else:
                    self.app.invalidate()
            except Exception:
                pass

        def animator() -> None:
            while True:
                with self._lock:
                    if not self.busy:
                        return
                    self.spinner_frame = (self.spinner_frame + 1) % len(SPINNER_FRAMES)
                try:
                    self.app.invalidate()
                except Exception:
                    return
                time.sleep(0.1)

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=animator, daemon=True).start()

    def _on_enter(self) -> None:
        if not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if entry.loaded:
            self.result = entry.full_spec
            self.app.exit()
            return
        # Not loaded — load it, then exit on success.
        self._start_action(entry, load=True, exit_on_success=True)

    def _request_load(self) -> None:
        if self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if not entry.can_load_unload or entry.loaded:
            return  # can't load this backend, or already loaded — a no-op
        self.confirming_action = "load"
        self.confirming_entry = entry

    def _request_unload(self) -> None:
        if self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if not entry.can_load_unload or not entry.loaded:
            return  # can't unload this backend, or not loaded — a no-op
        self.confirming_action = "unload"
        self.confirming_entry = entry

    def _confirm_yes(self) -> None:
        if self.confirming_action is None or self.confirming_entry is None:
            return
        action = self.confirming_action
        entry = self.confirming_entry
        self.confirming_action = None
        self.confirming_entry = None
        self._start_action(entry, load=(action == "load"), exit_on_success=False)

    def _confirm_no(self) -> None:
        self.confirming_action = None
        self.confirming_entry = None

    def _on_escape(self) -> None:
        if not self._clear_filter():
            get_app().exit()

    def _on_key(self, data: str) -> None:
        key = latin_key(data)
        if key == "l":
            self._request_load()
        elif key == "u":
            self._request_unload()

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
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

        @kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        items_window = Window(
            content=self._make_items_control(cursor_line=self._cursor_line),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_width=True,
            style="class:row.notloaded",
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

        left_pane = HSplit(
            [
                text_line(self._header_text, style="class:header"),
                Window(height=1, char=" ", always_hide_cursor=True),
                items_window,
                Window(height=1, char=" ", always_hide_cursor=True),
                bottom_row,
            ]
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
        return self._finish_app(left_pane, kb, _STYLE, floats=[dialog])


def pick(providers: ProviderRegistry, initial_spec: str | None = None) -> str | None:
    """Show the model picker. Returns the chosen 'provider/model' spec,
    None on cancel, or "" when no models are reachable at all — the
    caller opens without a model, and the printed diagnostic says how to
    fix that. Loading errors stay inside the picker."""
    rows, reachable = providers.inventory()
    entries: list[ModelEntry] = []
    # Sorted by provider name so the grouping is stable regardless of
    # completion order.
    for row in sorted(rows, key=lambda r: r.provider.name):
        can = row.can_load_unload
        for model in row.models:
            entries.append(
                ModelEntry(
                    full_spec=f"{row.provider.name}/{model.name}",
                    provider_name=row.provider.name,
                    model=model.name,
                    # A backend you can't load/unload serves its models
                    # statically, so they are ALWAYS available — shown
                    # loaded, and Enter picks them directly.
                    loaded=model.is_loaded if can else True,
                    can_load_unload=can,
                    size_bytes=model.size,
                )
            )
    if not entries:
        print(providers.unreachable_help(reachable))
        print("Opening without a model — pick one with /model once a server is up.")
        return ""
    return ModelPicker(providers, entries, initial_spec=initial_spec).run()
