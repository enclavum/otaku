"""Model picker — opened by the bare `otaku` invocation and `/model`.

A single full-screen Application, split 1:1. The left side lists every
model from every reachable provider — grouped under provider captions
in the panel's order, bare model names, providers with no models
absent — color-coded by load state. Only the local engines are waited
for before the screen opens; each cloud catalog's rows arrive when it
answers, the header naming what is still loading. The user can:
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

The right side is the provider panel: the app's backends in a fixed
order — llama.cpp, KoboldCpp, Ollama, oMLX, LM Studio, OpenRouter,
NanoGPT — each
a caption with its `URL:` and `API key:` fields, the key's value never
displayed, the cloud catalogs' url fixed (shown dimmed, never
walkable). Tab switches sides; ↑/↓ walk the fields; Enter edits the
highlighted one in place (←/→ move the cursor, paste works, Enter
saves); Delete on an api key, outside the editor, clears it. A saved
url is written into config.toml surgically, a saved api key is sealed
first (see `settings.sealed`), and a backend not configured yet gets
its section written — that is how a cloud provider is added. Both take
effect in the running session at once, and the provider's models are
re-listed under the new configuration.
"""

import contextlib
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

import psutil
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import (
    ConditionalKeyBindings,
    KeyBindings,
    merge_key_bindings,
)
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

from otaku.formatting import format_context, format_size, truncate
from otaku.paths import Paths
from otaku.providers.base import ManagedClient
from otaku.providers.registry import CLIENTS
from otaku.providers.registry import Registry as ProviderRegistry
from otaku.settings import migrations, sealed
from otaku.settings.config import ProviderConfig
from otaku.settings.files import toml_scalar
from otaku.terminal import latin_key
from otaku.terminal.spinner import FRAMES as SPINNER_FRAMES
from otaku.tui.screen import BASE_STYLE, ListScreen, bordered_box, text_line

_STYLE = Style.from_dict(
    {
        **BASE_STYLE,
        "row.loaded": "bold fg:#000000 bg:#ffffff",
        "row.notloaded": "fg:#767676 bg:#ffffff",
        "row.plain": "fg:#000000 bg:#ffffff",
        "row.selected.loaded": "bold fg:#000000 bg:#e4e4e4",
        "row.selected.notloaded": "bold fg:#767676 bg:#e4e4e4",
        "row.selected.plain": "fg:#000000 bg:#e4e4e4",
        "header.detail": "nobold fg:#303030 bg:#ffffff",  # the light parts of the bold header
        "row.selected": "bold fg:#000000 bg:#e4e4e4",
        "dialog.error": "bold fg:#c0392b bg:#ffffff",
        "preview.title": "bold fg:#303030 bg:#ffffff",
        "preview.body": "fg:#000000 bg:#ffffff",
        "preview.muted": "fg:#767676 bg:#ffffff",  # the unloaded models' look
        "field.cursor": "fg:#ffffff bg:#303030",
        "tick": "fg:#2f9e44 bg:#ffffff",
        "notice": "fg:#767676 bg:#ffffff",
    }
)

_ROW_HEAD_LIMIT = 100

# The provider panel's rows: every backend the app speaks natively, in
# this order, captioned the way its project spells itself. The config
# section name is the kind on the left.
_PANEL_PROVIDERS: list[tuple[str, str]] = [
    ("llamacpp", "llama.cpp"),
    ("koboldcpp", "KoboldCpp"),
    ("ollama", "Ollama"),
    ("omlx", "oMLX"),
    ("lmstudio", "LM Studio"),
    ("openrouter", "OpenRouter"),
    ("nanogpt", "NanoGPT"),
]

_FIELD_LABELS = {"url": "URL:", "api_key": "API key:"}

_PANEL_ORDER = {kind: i for i, (kind, _) in enumerate(_PANEL_PROVIDERS)}
_PANEL_CAPTIONS = dict(_PANEL_PROVIDERS)


@dataclass
class ModelEntry:
    full_spec: str  # "provider/model"
    provider_name: str
    model: str
    loaded: bool
    can_load_unload: bool = True  # False → served statically
    size_bytes: int | None = None  # None when the provider doesn't expose it
    context: int | None = None  # the model's context window, when reported
    cloud: bool = False  # a hosted catalog's row: normal weight, no size


class ModelPicker(ListScreen):
    def __init__(
        self,
        providers: ProviderRegistry,
        entries: list[ModelEntry],
        initial_spec: str | None = None,
        *,
        paths: Paths,
        fetch: list[str] | None = None,
        connected: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.providers = providers
        self.paths = paths
        # Names whose last listing succeeded — the panel's tick: the
        # provider answered, and with the right key where one is needed.
        self.connected: set[str] = set(connected or ())
        self.all: list[ModelEntry] = list(entries)
        self.filtered: list[ModelEntry] = list(entries)
        self.cursor: int = 0
        self._initial_spec = initial_spec

        # The provider panel (the right side): the walkable field list —
        # two rows per backend, except the cloud catalogs whose url is
        # fixed (their API key alone) — its cursor, the inline editor.
        self.side: str = "models"
        self.fields: list[tuple[str, str]] = [
            (kind, attr)
            for kind, _ in _PANEL_PROVIDERS
            for attr in ("url", "api_key")
            if attr != "url" or CLIENTS[kind].local
        ]
        self.field_cursor: int = 0
        self.editing: bool = False
        self.edit_buffer = Buffer(multiline=False)

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

        # The named providers (the cloud catalogs) answer after the screen
        # is up: each fetch merges its rows in and leaves the pending set.
        self.pending: set[str] = set(fetch or [])
        # A snapshot: a fetch that fails instantly (a keyless catalog)
        # discards from the live set while this loop still walks it.
        for name in list(self.pending):
            self._refresh_provider(name)

    def run(self) -> str | None:
        # An empty screen still runs: the provider panel is the one door
        # to configuring a backend, so a machine with nothing reachable
        # must reach it — `pick` alone decides when opening is skipped.
        self.app.run()
        return self.result

    # ---------- text content ----------

    def _table_width(self) -> int:
        """Natural width of the model table (row prefix + label + gap +
        size + gap + context). One snapshot of the list — a background
        refresh may swap it mid-call."""
        rows = self.filtered
        if not rows:
            return 0
        max_label = max(len(truncate(e.model, _ROW_HEAD_LIMIT)) for e in rows)
        max_size = max(len(format_size(e.size_bytes)) for e in rows)
        max_context = max(len(format_context(e.context)) for e in rows)
        return 4 + max_label + 2 + max_size + 2 + max_context  # 4 = the row prefix

    def _row_width(self) -> int:
        """Full width of a rendered model row: stretched so the selection
        ends 5 empty columns before the provider panel's border (3 of
        them the pane gap), never narrower than the table itself."""
        return max(self._table_width(), self._pane_cols()[0] - 2)

    def _header_text(self) -> StyleAndTextTuples:
        n, total = len(self.filtered), len(self.all)
        left = f" Models ({n} of {total})" if n != total else f" Models ({n})"
        loading = ""
        if self.pending:
            names = ", ".join(sorted(_PANEL_CAPTIONS.get(p, p) for p in self.pending))
            loading = f" · loading {names}…"
        try:
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            ram = f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({vm.percent:.0f}%)"
        except Exception:
            ram = ""

        head: StyleAndTextTuples = [("class:header", left), ("class:header.detail", loading)]
        if not ram:
            return head

        # Right-align RAM to the rows' right edge (never past the pane).
        width = min(self._row_width(), self._pane_cols()[0])
        gap = " " * max(2, width - len(left) - len(loading) - len(ram))
        return [*head, ("class:header.detail", gap + ram)]

    def _items_text(self) -> StyleAndTextTuples:
        # One snapshot for the whole frame: a background refresh swaps
        # self.filtered, and the column lists must match the row loop.
        rows = self.filtered
        if not rows:
            msg = "(no matches)" if self.query else "(no models)"
            return [("class:muted", "  " + msg)]

        # Every row spans one stretched width: the model name on the
        # left, the size and context columns flushed to the right edge.
        labels = [truncate(e.model, _ROW_HEAD_LIMIT) for e in rows]
        # A catalog row has no size at all — not even the unknown dash.
        sizes = ["" if e.cloud else format_size(e.size_bytes) for e in rows]
        contexts = [format_context(e.context) for e in rows]
        max_size = max((len(s) for s in sizes), default=0)
        max_context = max((len(s) for s in contexts), default=0)
        width = self._row_width()

        out: StyleAndTextTuples = []
        prev_provider: str | None = None
        for i, entry in enumerate(rows):
            if entry.provider_name != prev_provider:
                # The provider panel's structure, mirrored: a caption, a
                # blank, the rows — a provider with no models is absent.
                if prev_provider is not None:
                    out.append(("", "\n"))
                caption = _PANEL_CAPTIONS.get(entry.provider_name, entry.provider_name)
                out.append(("class:preview.title", "  " + caption + "\n"))
                out.append(("", "\n"))
                prev_provider = entry.provider_name
            # The cursor shows only while this side has the focus — on the
            # providers side the left list carries no selection at all.
            selected = i == self.cursor and self.side == "models"
            head = ("  > " if selected else "    ") + labels[i]
            tail = f"{sizes[i].rjust(max_size)}  {contexts[i].rjust(max_context)}".rstrip()
            gap = max(2, width - len(head) - len(tail))
            if entry.cloud:
                klass = "class:row.selected.plain" if selected else "class:row.plain"
            elif selected:
                klass = (
                    "class:row.selected.loaded" if entry.loaded else "class:row.selected.notloaded"
                )
            else:
                klass = "class:row.loaded" if entry.loaded else "class:row.notloaded"
            out.append((klass, head + " " * gap + tail + "\n"))
        return out

    def _cursor_line(self) -> int:
        """Visual row of the cursor, counting the caption and blank lines
        `_items_text` renders around each provider group."""
        line = 0
        prev: str | None = None
        for i, entry in enumerate(self.filtered[: self.cursor + 1]):
            if entry.provider_name != prev:
                line += 2 if prev is None else 3  # (group gap +) caption + blank
                prev = entry.provider_name
            if i == self.cursor:
                return line
            line += 1
        return line

    def _help_text(self) -> StyleAndTextTuples:
        if self.editing:
            txt = " editing — enter save · esc cancel"
        elif self.side == "providers":
            txt = " ↑/↓ navigate · enter edit · del clear key · tab models · esc back"
        elif self.in_filter:
            txt = " type to filter · ↑/↓ navigate · enter select · esc clear filter"
        else:
            segments = ["↑/↓ navigate", "/ filter"]
            # Load/unload only appear when the SELECTED model's backend
            # supports them.
            if self.filtered and self.filtered[self.cursor].can_load_unload:
                segments += ["l load", "u unload"]
            segments += ["enter select", "tab providers", "esc quit"]
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

    def _providers_text(self) -> StyleAndTextTuples:
        """The provider panel: per backend a caption, a blank, the URL
        field, the API key field (its value never displayed), a blank."""
        out: StyleAndTextTuples = []
        for kind, caption in _PANEL_PROVIDERS:
            provider_config = self._provider_config(kind)
            if kind in self.connected:
                out.append(("class:preview.title", caption))
                out.append(("class:tick", " ✓"))
            else:
                # Not connected: the name alone reads disabled.
                out.append(("class:preview.muted", caption))
            out.append(("", "\n"))
            out.append(("class:preview.body", "\n"))
            out.extend(self._field_line(kind, "url", provider_config.url))
            out.extend(
                self._field_line(kind, "api_key", "(set)" if provider_config.api_key else "")
            )
            out.append(("class:preview.body", "\n"))
        return out

    def _field_line(self, kind: str, attr: str, value: str) -> StyleAndTextTuples:
        """One field row: the label outside the selection, the value cell
        highlighted when the cursor is on it — labels padded to one
        column so the input fields left-align. A field that is not
        walkable (a cloud catalog's fixed url) renders dimmed."""
        if (kind, attr) not in self.fields:
            head = f"  {_FIELD_LABELS[attr]:<9}"
            return [("class:preview.muted", head + value + "\n")]
        selected = self.side == "providers" and self.fields[self.field_cursor] == (kind, attr)
        head = f"{'> ' if selected else '  '}{_FIELD_LABELS[attr]:<9}"
        if selected and self.editing:
            return [("class:preview.body", head), *self._editor_segments(attr, len(head))]
        if selected:
            width = max(1, self._preview_inner_width() - len(head))
            return [("class:preview.body", head), ("class:row.selected", value.ljust(width) + "\n")]
        return [("class:preview.body", (head + value).rstrip() + "\n")]

    def _editor_segments(self, attr: str, head_len: int) -> StyleAndTextTuples:
        """The value cell while editing: the buffer's text — bullets for
        an api key — with the character under the cursor as the caret."""
        text = self.edit_buffer.text
        pos = min(self.edit_buffer.cursor_position, len(text))
        shown = "•" * len(text) if attr == "api_key" else text
        width = max(len(shown) + 1, self._preview_inner_width() - head_len)
        caret = shown[pos] if pos < len(shown) else " "
        return [
            ("class:row.selected", shown[:pos]),
            ("class:field.cursor", caret),
            ("class:row.selected", shown[pos + 1 :].ljust(width - pos - 1) + "\n"),
        ]

    def _panel_cursor_line(self) -> int:
        """Visual row of the highlighted field — each provider block is 5
        rows (caption, blank, url, api key, blank)."""
        kind, attr = self.fields[self.field_cursor]
        return _PANEL_ORDER[kind] * 5 + 2 + (0 if attr == "url" else 1)

    # ---------- behavior ----------

    def _rows_count(self) -> int:
        return len(self.fields) if self.side == "providers" else len(self.filtered)

    def _cursor(self) -> int:
        return self.field_cursor if self.side == "providers" else self.cursor

    def _set_cursor(self, value: int) -> None:
        if self.side == "providers":
            self.field_cursor = value
        else:
            self.cursor = value

    def _move_cursor(self, delta: int) -> None:
        self.notice = ""
        super()._move_cursor(delta)

    def _type(self, data: str) -> None:
        # Letters and the `/` filter belong to the models side.
        if self.side == "models":
            super()._type(data)

    def _toggle_side(self) -> None:
        self.notice = ""
        self._clear_filter()
        self.side = "providers" if self.side == "models" else "models"

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
                # Refresh load state for this provider only — under
                # the lock, a concurrent catalog refresh may be swapping
                # the list.
                fresh = {m.name for m in client.models() if m.loaded}
                with self._lock:
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
        if self.side == "providers":
            self._start_field_edit()
            return
        if not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if entry.loaded or not entry.can_load_unload:
            # Loaded — or a statically served backend (llama.cpp, a
            # KoboldCpp between admin swaps): the engine serves what it
            # serves, so Enter just picks.
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
        if self.side == "providers":
            self.notice = ""
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

    # ---------- provider field editing ----------

    def _provider_config(self, name: str) -> ProviderConfig:
        """The backend's current provider: the configured section when
        there is one, its autoconfigured default otherwise."""
        configured = {p.name: p for p in self.providers.configured()}
        if name in configured:
            return configured[name]
        return CLIENTS[name].autoconfigure()

    def _start_field_edit(self) -> None:
        if not self.fields:
            return
        name, attr = self.fields[self.field_cursor]
        self.notice = ""
        self.editing = True
        # The url edits in place; the api key always starts blank — its
        # current value is never displayed, not even to edit.
        prefill = self._provider_config(name).url if attr == "url" else ""
        self.edit_buffer.document = Document(prefill, len(prefill))

    def _finish_field_edit(self, *, save: bool) -> None:
        self.editing = False
        value = self.edit_buffer.text.strip()
        self.edit_buffer.reset()
        if not save:
            self.notice = "(cancelled)"
            return
        if not value:
            self.notice = "(empty — not saved)"
            return
        name, attr = self.fields[self.field_cursor]
        provider_config = self._provider_config(name)
        if attr == "url":
            value = value.rstrip("/")
            line = f"url = {toml_scalar(value)}"
            updated = replace(
                provider_config, url=value
            )  # saved silently — the field shows the new value
        else:
            try:
                line = f"api_key = {toml_scalar(sealed.seal(self.paths, value))}"
            except sealed.SealedError as e:
                self.notice = f"save failed: {e}"
                return
            updated = replace(
                provider_config, api_key=value
            )  # saved silently — the (set) mark says it
        # A backend not in providers.toml yet gets its section written
        # first — this is how a cloud provider is added deliberately.
        block = f"[{name}]\nurl = {toml_scalar(provider_config.url)}\n" + 'api_key = ""'
        migrations.update_providers(
            self.paths,
            [migrations.ensure_section(name, block), migrations.set_key(name, attr, line)],
        )
        self.providers.update_provider(updated)
        self._refresh_provider(name)

    def _clear_field(self) -> None:
        """Delete on an api key field, outside the editor: forget the
        stored key — the config and the running session both."""
        if self.side != "providers":
            return
        name, attr = self.fields[self.field_cursor]
        if attr != "api_key":
            return
        provider_config = self._provider_config(name)
        if not provider_config.api_key:
            return  # nothing to clear — and no hint: the field is visibly bare
        migrations.update_providers(
            self.paths, [migrations.set_key(name, "api_key", 'api_key = ""')]
        )
        self.providers.update_provider(replace(provider_config, api_key=""))
        self._refresh_provider(name)  # the vanished (set) mark reports it

    def _refresh_provider(self, name: str) -> None:
        """Re-list one provider after any of its settings changed, so the
        models side follows the edit without a relaunch — a provider that
        stopped answering simply loses its rows."""

        def worker() -> None:
            client = self.providers.get_client(name)
            try:
                fresh = client.models(timeout=5.0)
                self.connected.add(name)
            except Exception:
                fresh = []  # the reread found nothing — honest emptiness
                self.connected.discard(name)
            can = isinstance(client, ManagedClient)
            cloud = not client.local
            rows = [
                ModelEntry(
                    full_spec=f"{name}/{model.name}",
                    provider_name=name,
                    model=model.name,
                    loaded=model.loaded if can else True,
                    can_load_unload=can,
                    size_bytes=model.size,
                    context=model.context,
                    cloud=cloud,
                )
                for model in fresh
            ]
            # Concurrent refreshes (both catalogs at open, say) rebuild
            # the same list — the swap happens under the lock, so no
            # worker starts from a list another is replacing.
            with self._lock:
                entries = [e for e in self.all if e.provider_name != name]
                self.all = _ordered(entries + rows)
                self.pending.discard(name)
                self._refilter()
                # A remembered model whose rows just arrived gets the
                # cursor, unless the user already moved it somewhere.
                if self._initial_spec and self.cursor == 0:
                    for i, e in enumerate(self.filtered):
                        if e.full_spec == self._initial_spec:
                            self.cursor = i
                            break
            with contextlib.suppress(Exception):
                self.app.invalidate()

        threading.Thread(target=worker, daemon=True).start()

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

        @kb.add("tab", filter=idle)
        def _tab(event: Any) -> None:
            self._toggle_side()

        @kb.add("delete", filter=idle)
        def _clear(event: Any) -> None:
            self._clear_field()

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

        @edit_kb.add(Keys.BracketedPaste, filter=editing)
        def _paste(event: Any) -> None:
            # A url or an api key is one line — a pasted newline is noise.
            self.edit_buffer.insert_text(event.data.replace("\r", "").replace("\n", ""))

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
            # cell the rows leave bare — a shrunk window would leave the
            # leftover columns to the terminal's own (maybe dark)
            # background.
            # Two lines of margin above the cursor: exactly the caption
            # and its blank, so a group's header scrolls into view when
            # the cursor stands on the group's first model.
            scroll_offsets=ScrollOffsets(top=2),
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
                text_line(
                    lambda: [("class:notice", "  " + self.notice if self.notice else "")],
                    filter=Condition(lambda: bool(self.notice)),
                ),
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
        return self._finish_app(root, bindings, _STYLE, floats=[dialog])


def pick(
    providers: ProviderRegistry, initial_spec: str | None = None, *, paths: Paths
) -> str | None:
    """Show the model picker. Returns the chosen 'provider/model' spec,
    or None when the user leaves without choosing — silently: the caller
    decides what a missing model means. The screen opens even with
    nothing to list: the provider panel is the one place a backend is
    configured, so an empty machine must still get there. Loading errors
    stay inside the picker; the provider panel on the right edits urls
    and api keys in place (hence `paths`). Only the local engines are
    waited for: the screen opens on their answers, and each cloud
    catalog's rows arrive when it responds."""
    catalogs = [p.name for p in providers.configured() if not providers.get_client(p.name).local]
    rows, reachable = providers.inventory(skip=set(catalogs))
    entries: list[ModelEntry] = []
    for row in rows:
        can = row.can_load_unload
        cloud = not providers.get_client(row.provider_config.name).local
        for model in row.models:
            entries.append(
                ModelEntry(
                    full_spec=f"{row.provider_config.name}/{model.name}",
                    provider_name=row.provider_config.name,
                    model=model.name,
                    # A backend you can't load/unload serves its models
                    # statically, so they are ALWAYS available — shown
                    # loaded, and Enter picks them directly.
                    loaded=model.loaded if can else True,
                    can_load_unload=can,
                    size_bytes=model.size,
                    context=model.context,
                    cloud=cloud,
                )
            )
    picker = ModelPicker(
        providers,
        _ordered(entries),
        initial_spec=initial_spec,
        paths=paths,
        fetch=catalogs,
        connected=reachable,
    )
    # Leaving without a choice says nothing: at launch the session
    # opens model-less and the first turn explains itself; from /model
    # everything simply stays as it was.
    return picker.run()


def _ordered(entries: list[ModelEntry]) -> list[ModelEntry]:
    """Panel order: the six backends as the provider panel lists them,
    any other configured provider after, by name; models keep their
    client order within a provider."""
    return sorted(
        entries,
        key=lambda e: (_PANEL_ORDER.get(e.provider_name, len(_PANEL_ORDER)), e.provider_name),
    )
