"""Model picker — opened by the bare `otaku` invocation.

A single full-screen Application listing every model from every configured
provider, color-coded by load state. The user can:
    - move the cursor (↑/↓/PgUp/PgDn/Home/End)
    - type-to-filter by pressing `/` first; Esc cancels the filter
    - toggle load state with `l` (opens a modal dialog with a spinner)
    - press Enter to save the highlighted model to ~/.otaku/last_model and exit.
      If the highlighted model is not loaded yet, Enter loads it first
      (same modal + spinner) and only saves on success.
    - press Esc to cancel without saving.

Loaded models are rendered green (#5fa703); not-loaded are muted (#767676).
The cursor restores to the last-saved model on open.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psutil  # type: ignore[import-untyped]
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

from otaku.client import client_for, map_providers, probing_notice, unreachable_help
from otaku.config import Provider
from otaku.pickers._widgets import BASE_STYLE, KEY_NO, KEY_YES, bordered_box, page_step, text_line
from otaku.spinner import FRAMES as SPINNER_FRAMES
from otaku.text import format_size, truncate

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

ROW_HEAD_LIMIT = 100

_KEY_LOAD = {"l", "д"}
_KEY_UNLOAD = {"u", "г"}  # noqa: RUF001


@dataclass
class ModelEntry:
    full_spec: str  # "provider/model"
    provider_name: str
    model: str
    loaded: bool
    size_bytes: int | None = None  # None when the provider doesn't expose it


class ModelPicker:
    def __init__(
        self,
        providers: Mapping[str, Provider],
        entries: list[ModelEntry],
        initial_spec: str | None = None,
    ) -> None:
        self.providers = dict(providers)
        self.all: list[ModelEntry] = list(entries)
        self.filtered: list[ModelEntry] = list(entries)
        self.cursor: int = 0
        self.in_filter: bool = False
        self.query: str = ""

        if initial_spec is not None:
            for i, e in enumerate(self.all):
                if e.full_spec == initial_spec:
                    self.cursor = i
                    break

        # Confirmation state (set when user presses load/unload key)
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

    def _header_text(self) -> StyleAndTextTuples:
        n, total = len(self.filtered), len(self.all)
        left = f" Models ({n} of {total})" if n != total else f" Models ({n})"
        try:
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            center = f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({vm.percent:.0f}%)"
        except Exception:
            center = ""

        if not center:
            return [("class:header", left)]

        try:
            cols = get_app().output.get_size().columns
        except Exception:
            cols = 120

        # Position the RAM string at the absolute centre of the row;
        # if the terminal is too narrow, fall back to a single space gap
        # after the left-side text.
        target = (cols - len(center)) // 2
        gap = " " * max(2, target - len(left))
        return [("class:header", left + gap + center)]

    def _filter_text(self) -> StyleAndTextTuples:
        if not self.in_filter:
            return [("", "")]
        return [
            ("class:filter", "  filter: "),
            ("class:filter.query", self.query),
        ]

    def _items_text(self) -> StyleAndTextTuples:
        if not self.filtered:
            msg = "(no matches)" if self.query else "(no models)"
            return [("class:muted", "  " + msg)]

        # Pre-compute label + size column widths so the size column right-
        # aligns and the selection background extends across the full row.
        labels = [truncate(e.full_spec, ROW_HEAD_LIMIT) for e in self.filtered]
        sizes = [format_size(e.size_bytes) for e in self.filtered]
        max_label = max((len(s) for s in labels), default=0)
        max_size = max((len(s) for s in sizes), default=0)
        rule = "    " + "─" * (max_label + 2 + max_size)

        out: StyleAndTextTuples = []
        prev_provider: str | None = None
        for i, e in enumerate(self.filtered):
            if prev_provider is not None and e.provider_name != prev_provider:
                out.append(("class:separator", rule + "\n"))
            prev_provider = e.provider_name
            label = labels[i].ljust(max_label)
            size = sizes[i].rjust(max_size)
            row = f"{label}  {size}"  # 2-space gap before size
            selected = i == self.cursor
            if selected:
                klass = "class:row.selected.loaded" if e.loaded else "class:row.selected.notloaded"
                out.append((klass, f"  > {row}\n"))
            else:
                klass = "class:row.loaded" if e.loaded else "class:row.notloaded"
                out.append((klass, f"    {row}\n"))
        return out

    def _cursor_line(self) -> int:
        """Visual row of the cursor, counting the provider separators
        rendered above it — keeps scroll-to-cursor aligned with the
        extra lines `_items_text` inserts between provider groups."""
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
            txt = " ↑/↓ navigate · / filter · l load · u unload · enter select · esc quit"
        return [("class:help", txt)]

    def _confirm_text(self) -> StyleAndTextTuples:
        if not self.confirming_action or self.confirming_entry is None:
            return [("", "")]
        verb = "Load" if self.confirming_action == "load" else "Unload"
        # Dialog inner content area is ~54 chars wide at preferred 60.
        # 'Unload model ' + quotes + '?' = 18 chars fixed → cap spec at 40.
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

    def _refilter(self) -> None:
        q = self.query.strip().lower()
        if not q:
            self.filtered = list(self.all)
        else:
            self.filtered = [e for e in self.all if q in e.full_spec.lower()]
        if self.cursor >= len(self.filtered):
            self.cursor = max(0, len(self.filtered) - 1)

    def _move_cursor(self, delta: int) -> None:
        n = len(self.filtered)
        if n == 0:
            self.cursor = 0
            return
        self.cursor = max(0, min(n - 1, self.cursor + delta))

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

        provider = self.providers.get(entry.provider_name)

        def worker() -> None:
            err: str | None = None
            try:
                if provider is None:
                    raise RuntimeError(f"provider '{entry.provider_name}' not configured")
                cli = client_for(provider)
                if load:
                    cli.load_model(entry.model)
                else:
                    cli.unload_model(entry.model)
                # ollama's /api/ps lags ~100-200ms behind /api/generate's
                # response — without this sleep the refresh below sees the
                # model still loaded after an unload.
                time.sleep(0.2)
                # Refresh load state for this provider only.
                fresh = cli.loaded_models()
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
        if entry.loaded:
            return  # already loaded; 'l' is a no-op
        self.confirming_action = "load"
        self.confirming_entry = entry

    def _request_unload(self) -> None:
        if self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if not entry.loaded:
            return  # not loaded; 'u' is a no-op
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
        if self.in_filter:
            self.in_filter = False
            self.query = ""
            self._refilter()
            return
        get_app().exit()

    # ---------- application wiring ----------

    def _build_app(self) -> Application[None]:
        kb = KeyBindings()

        idle = Condition(
            lambda: not self.busy and self.busy_error is None and self.confirming_action is None
        )
        confirming = Condition(lambda: self.confirming_action is not None)
        showing_error = Condition(lambda: self.busy_error is not None)

        # Dismiss any-key while error dialog is up.
        @kb.add(Keys.Any, filter=showing_error, eager=True)
        def _dismiss_err(event: Any) -> None:
            self.busy_error = None

        # While the confirm dialog is up: only y/n/esc do anything.
        @kb.add("escape", eager=True, filter=confirming)
        def _confirm_esc(event: Any) -> None:
            self._confirm_no()

        @kb.add(Keys.Any, filter=confirming, eager=True)
        def _confirm_any(event: Any) -> None:
            key = event.data.lower() if event.data else ""
            if key in KEY_YES:
                self._confirm_yes()
            elif key in KEY_NO:
                self._confirm_no()

        @kb.add("up", filter=idle)
        def _up(event: Any) -> None:
            self._move_cursor(-1)

        @kb.add("down", filter=idle)
        def _down(event: Any) -> None:
            self._move_cursor(1)

        @kb.add("pageup", filter=idle)
        def _pgup(event: Any) -> None:
            self._move_cursor(-page_step())

        @kb.add("pagedown", filter=idle)
        def _pgdn(event: Any) -> None:
            self._move_cursor(page_step())

        @kb.add("home", filter=idle)
        def _home(event: Any) -> None:
            self.cursor = 0

        @kb.add("end", filter=idle)
        def _end(event: Any) -> None:
            self.cursor = max(0, len(self.filtered) - 1)

        @kb.add("enter", filter=idle)
        def _enter(event: Any) -> None:
            self._on_enter()

        @kb.add("escape", eager=True, filter=idle)
        def _esc(event: Any) -> None:
            self._on_escape()

        @kb.add("c-c")
        def _ctrlc(event: Any) -> None:
            self.result = None
            event.app.exit()

        @kb.add("backspace", filter=idle)
        def _bs(event: Any) -> None:
            if not self.in_filter:
                return
            if not self.query:
                self.in_filter = False
                self._refilter()
                return
            self.query = self.query[:-1]
            self._refilter()

        @kb.add(Keys.Any, filter=idle)
        def _any(event: Any) -> None:
            data = event.data
            if not data or len(data) != 1 or not data.isprintable():
                return
            if not self.in_filter:
                if data == "/":
                    self.in_filter = True
                    self.query = ""
                    self._refilter()
                    return
                key = data.lower()
                if key in _KEY_LOAD:
                    self._request_load()
                    return
                if key in _KEY_UNLOAD:
                    self._request_unload()
                    return
                return
            # filter mode: append
            self.query += data
            self._refilter()

        items_window = Window(
            content=FormattedTextControl(
                text=self._items_text,
                get_cursor_position=lambda: Point(0, self._cursor_line()),
                show_cursor=False,
            ),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_width=True,
            style="class:row.notloaded",
        )

        # Bottom row: shows the filter input when filtering, otherwise the
        # help line. Same height in both states so the items list above
        # doesn't shift.
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
                wrap=False,  # truncate long model names instead of wrapping
            ),
            filter=confirming,
        )

        dialog = HSplit([busy_dialog, confirm_dialog])

        root = FloatContainer(
            content=left_pane,
            floats=[Float(content=dialog)],
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
        app.timeoutlen = 0.05
        app.ttimeoutlen = 0.05
        return app


def _collect(providers: Mapping[str, Provider]) -> tuple[list[ModelEntry], set[str]]:
    """Fetch all models + load state + (when available) on-disk size across every
    provider, concurrently. Returns the entries plus the set of provider names
    that responded (so the caller can diagnose a no-models result). Providers
    that fail to list models are skipped."""

    def gather(name: str, provider: Provider) -> tuple[str, list[ModelEntry]] | None:
        cli = client_for(provider)
        try:
            models = cli.list_models()
        except Exception:
            return None  # unreachable
        try:
            loaded = cli.loaded_models()
        except Exception:
            loaded = set()
        try:
            sizes = cli.model_sizes()
        except Exception:
            sizes = {}
        rows = [
            ModelEntry(
                full_spec=f"{name}/{m}",
                provider_name=name,
                model=m,
                loaded=m in loaded,
                size_bytes=sizes.get(m),
            )
            for m in models
        ]
        return name, rows

    # sorted() keeps the picker's provider grouping stable regardless of
    # thread completion order.
    ordered = {name: providers[name] for name in sorted(providers)}
    with probing_notice(ordered):
        results = map_providers(ordered, gather)
    reachable = {r[0] for r in results if r is not None}
    entries = [e for r in results if r is not None for e in r[1]]
    return entries, reachable


def pick_model(providers: Mapping[str, Provider], initial_spec: str | None = None) -> str | None:
    """Show the model picker. Returns the chosen 'provider/model' spec or
    None on cancel. Loading errors stay inside the picker.
    """
    entries, reachable = _collect(providers)
    if not entries:
        print(unreachable_help(providers, reachable))
        return None
    return ModelPicker(providers, entries, initial_spec=initial_spec).run()
