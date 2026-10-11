"""The picker's right side, the provider panel: each provider's caption
with its `URL:` and `API key:` fields — the key's value never shown —
edited in place, saved through the door (`api.app.providers`), and the
provider's models listed again under its new section."""

import contextlib
import threading
import time

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.terminal.api import KeySource, Locality, ModelState, Refused
from otaku.terminal.api.app import providers as api_providers
from otaku.terminal.api.app.providers import ListedProvider
from otaku.terminal.screens.models.base import ModelEntry, ModelPickerBase

_FIELD_LABELS = {"url": "URL:", "api_key": "API key:"}


# How the api key field reads: its value is never displayed, only where
# it comes from. The page captions the same fact
# (`web/static/js/screens/models.js keyField`).
_KEY_CAPTIONS = {KeySource.CONFIG: "(set)", KeySource.ENV: "(from environment)", None: ""}


# How long an edit's "loading…" holds before the fresh rows land — see
# `_refresh_provider(settled=...)`.
_EDIT_DWELL = 0.5


class ProviderPanel(ModelPickerBase):
    """The provider panel, and its fields' edits."""

    def _providers_text(self) -> StyleAndTextTuples:
        """The provider panel: per provider a caption, a blank, the URL
        field, the API key field (its value never displayed, only where
        it comes from), a blank."""
        out: StyleAndTextTuples = []
        for provider in self.providers:
            standing = self.api.providers.get(provider.id)
            if provider.id in self.pending:
                # Still being listed: the answer decides the other two, so
                # say so here rather than let the name read as a verdict.
                out.append(("class:preview.muted", provider.label + " - loading…"))
            elif provider.id in self.connected:
                out.append(("class:preview.title", provider.label))
                out.append(("class:tick", " ✓"))
            else:
                # Not connected: the name alone reads disabled.
                out.append(("class:preview.muted", provider.label))
            out.append(("", "\n"))
            out.append(("class:preview.body", "\n"))
            out.extend(self._field_line(provider.id, "url", standing.url))
            out.extend(self._field_line(provider.id, "api_key", _KEY_CAPTIONS[standing.key_source]))
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
        return self._order[kind] * 5 + 2 + (0 if attr == "url" else 1)

    def _start_field_edit(self, *, blank: bool = False) -> None:
        """Open the inline editor on the highlighted field. `blank` is the
        way in that a keystroke takes: it replaces rather than corrects,
        so the value it starts from is nothing."""
        if not self.fields:
            return
        name, attr = self.fields[self.field_cursor]
        self.notice = ""
        self.editing = True
        # The url edits in place; the api key always starts blank — its
        # current value is never displayed, not even to edit.
        edits_in_place = attr == "url" and not blank
        prefill = self.api.providers.get(name).url if edits_in_place else ""
        # The cursor lands at the END: Enter is for correcting a value,
        # and correcting one starts from its last character, not before
        # its first. A blank field puts the two in the same place.
        self.edit_buffer.document = Document(prefill, len(prefill))

    def _finish_field_edit(self, *, save: bool) -> None:
        """Close the editor, saving what is in it unless `save` is off.
        Neither leaving without saving nor saving nothing says anything:
        the field is back on screen showing what it holds, which is the
        whole report. The strip below is for what the SCREEN cannot show
        — a refusal, or a value the file would not take."""
        self.editing = False
        value = self.edit_buffer.text.strip()
        self.edit_buffer.reset()
        if not save or not value:
            return
        name, attr = self.fields[self.field_cursor]
        try:
            api_providers.update_field(self.api, name, attr, value)
        except Refused as e:
            # A file that would not take the value took none of it: the
            # field shows what still stands, and this says why.
            self.notice = str(e)
            return
        self._refresh_provider(name, settled=True)

    def _set_field(self, text: str) -> None:
        """A paste onto a CLOSED field sets it outright: the field opens,
        becomes the clipboard, and is saved in the one gesture — a url or
        an api key is pasted whole, never composed, so there is nothing to
        position and nothing to confirm. Inside the editor a paste stays
        an ordinary paste: there the user IS composing."""
        if self.side != "providers" or self.editing or not text:
            return
        self._start_field_edit()
        if not self.editing:
            return  # nothing walkable under the cursor
        self.edit_buffer.document = Document(text, len(text))
        self._finish_field_edit(save=True)

    def _clear_field(self) -> None:
        """Delete on a field, outside the editor: forget the url or the
        stored key — the config and the running app both."""
        if self.side != "providers":
            return
        name, attr = self.fields[self.field_cursor]
        standing = self.api.providers.get(name)
        held = standing.url if attr == "url" else standing.key_source is KeySource.CONFIG
        if not held:
            return  # nothing to clear — and no hint: the field is visibly bare
        try:
            api_providers.update_field(self.api, name, attr, "")
        except Refused as e:
            self.notice = str(e)
            return
        # The bare field reports it — and a provider with no url loses
        # its rows, the listing having nowhere to go.
        self._refresh_provider(name, settled=True)

    def _fetch_rows(self, name: str) -> list[ListedProvider]:
        """One provider's fresh listing, through the picker's one query —
        every OTHER configured provider skipped, so a catalog refresh
        never costs a sweep of dead providers — its answer alone."""
        listing = self.api.providers.list(scope=name)
        if name in listing.reachable:
            self.connected.add(name)
        else:
            self.connected.discard(name)
        return [provider for provider in listing.providers if provider.answered]

    def _refresh_provider(self, name: str, *, settled: bool = False) -> None:
        """Re-list one provider — at the open, and again whenever its
        settings change, so the models side follows an edit without a
        relaunch; a provider that stopped answering simply loses its rows.

        Marked pending BEFORE the thread starts, and by this method rather
        than by its callers: every listing is one of these, so every
        listing says so in the panel and nothing has to remember to. The
        add is under the lock the worker's discard takes, or a fetch that
        answers instantly could clear the mark before it was made.

        `settled` is the edit-triggered shape: the edited provider's old
        rows leave with the edit and the fresh ones arrive only when the
        loading mark goes, held to a minimum dwell — a local engine
        answers in milliseconds, and an instant blink reads as "nothing
        happened" rather than "asked again". The wait sleeps on the
        fetch's own worker thread, where the network wait already lives;
        the screen never blocks."""
        with self._lock:
            self.pending.add(name)
            if settled:
                entries = [e for e in self.all if e.provider_name != name]
                self.all = _ordered(entries, self._order)
                self._refilter()
        started = time.monotonic()

        def worker() -> None:
            try:
                fetched = self._fetch_rows(name)
            except Exception:
                fetched = []  # the reread found nothing — honest emptiness
                self.connected.discard(name)
            if settled:
                remaining = _EDIT_DWELL - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
            rows = [
                ModelEntry(
                    full_spec=f"{name}/{model.name}",
                    provider_name=name,
                    model=model.name,
                    loaded=model.state is ModelState.LOADED if _manages(row) else True,
                    can_manage=_manages(row),
                    size_bytes=model.size,
                    context_length=model.context_length,
                    context_loaded=model.context_loaded,
                    cloud=row.locality is not Locality.LOCAL,
                )
                for row in fetched
                for model in row.models
            ]
            # Concurrent refreshes (both catalogs at open, say) rebuild
            # the same list — the swap happens under the lock, so no
            # worker starts from a list another is replacing.
            with self._lock:
                before = [e.full_spec for e in self.filtered]
                under = before[self.cursor] if before else None
                entries = [e for e in self.all if e.provider_name != name]
                self.all = _ordered(entries + rows, self._order)
                self.pending.discard(name)
                self._refilter()
                self._settle_side()
                self._keep_cursor(under, before)
            with contextlib.suppress(Exception):
                self.application.invalidate()

        threading.Thread(target=worker, daemon=True).start()

    def _keep_cursor(self, under: str | None, before: list[str]) -> None:
        """Rows land in PROVIDER order, not arrival order, so a listing
        that lands above the cursor would shove another model under it.
        The cursor follows a model, never an index: the remembered one
        the moment its provider answers — it may be the last to — and
        otherwise whatever was under the cursor, which is the remembered
        model until the user moves off it. `under` and `before` are the
        cursor's row and the rows as they were before the swap."""
        wanted = under
        if self._initial_spec and self._initial_spec not in before:
            wanted = self._initial_spec
        specs = [e.full_spec for e in self.filtered]
        for spec in (wanted, under):
            if spec in specs:
                self.cursor = specs.index(spec)
                return


def _ordered(entries: list[ModelEntry], order: dict[str, int]) -> list[ModelEntry]:
    """Panel order: the supported providers as the panel lists them, any
    other configured provider after, by name; models keep their client
    order within a provider."""
    return sorted(
        entries,
        key=lambda e: (order.get(e.provider_name, len(order)), e.provider_name),
    )


def _manages(provider: ListedProvider) -> bool:
    """Whether a provider that answered loads and unloads its models."""
    return provider.capabilities is not None and provider.capabilities.model_management
