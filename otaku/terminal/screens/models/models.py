"""The picker's left side, the model list: every model of every provider
that answered, grouped under the providers' captions, its marks — the
model written with, the one extraction asks — its size and its window;
a model loaded or unloaded after a confirm, picked, or pinned for lore
extraction."""

import contextlib
import threading
import time

from prompt_toolkit.formatted_text import StyleAndTextTuples

from otaku.formatting import format_context, format_size, truncate
from otaku.terminal.api import ModelRef, ModelState, Refused
from otaku.terminal.api import app as api_app
from otaku.terminal.screens.models.base import ModelEntry, ModelPickerBase, cursor_line
from otaku.terminal.tty.spinner import FRAMES as SPINNER_FRAMES

# A model row's shape: a 4-column prefix ("  > "), the model name, then
# two right-aligned columns held at a FIXED width — the widest label
# either formatter can produce for a real model ("999.9 GB", "128K";
# see formatting.format_size / format_context) — each set off by three
# spaces. Fixed because the columns must not move: a cloud catalog
# arriving mid-session, or a resize, may not shove them off the pane.
# The name is the one thing that gives, shrinking to whatever is left —
# to a stub on a pane too narrow to hold one, because a row wider than
# the pane would wrap, and the scroll maths counts one line per model.
_ROW_PREFIX = 4


# The fewest columns a name keeps beside its marks ("[ writing ]"):
# on a pane narrower than that the marks give way — the name is what
# tells the row.
_MIN_NAME = 8


_MARK_GAP = 4


_SIZE_COL = 8


_CONTEXT_COL = 4


_COL_GAP = 3


class ModelList(ModelPickerBase):
    """The model list, and what is done to a model."""

    def _row_width(self) -> int:
        """Full width of a rendered model row: the whole items pane, so
        the selection band ends exactly `_preview_gap` columns short of
        the provider panel's border, like every other picker's rows. The
        pane alone decides — never the data — so no model name can push a
        row past the edge of the screen."""
        return self._max_row_content_width()

    def _name_width(self) -> int:
        """Columns the model name gets: the row, less its prefix and the
        two fixed columns with their gaps. This is where a narrow
        terminal and a late-arriving cloud catalog are absorbed."""
        tail = _COL_GAP + _SIZE_COL + _COL_GAP + _CONTEXT_COL
        return max(0, self._row_width() - _ROW_PREFIX - tail)

    def _marks(self, entry: ModelEntry, story: str, pinned: str) -> str:
        """What the model is to the app — "[ writing ]",
        "[ extraction ]", or both — standing against the figures; "" for
        neither."""
        roles = [
            role
            for role, spec in (("writing", story), ("extraction", pinned))
            if spec == entry.full_spec
        ]
        return " ".join(f"[ {role} ]" for role in roles)

    def _items_text(self) -> StyleAndTextTuples:
        # One snapshot for the whole frame: a background refresh swaps
        # self.filtered, and the column lists must match the row loop —
        # the cursor's visual row included, which the control asks for
        # right after the text and must find inside it (a row computed
        # on a list swapped in between once pointed past the text drawn,
        # and prompt_toolkit's IndexError took the screen down).
        rows = self.filtered
        self._drawn_cursor_line = cursor_line(rows, self.cursor)
        if not rows:
            # An empty list while a provider is still answering is not an
            # answer — it is the question, not yet returned. Saying "no
            # models" there would be a verdict the screen has not earned,
            # and one it would take back a moment later.
            if self.query:
                msg = "(no matches)"
            elif self.pending:
                msg = "(loading…)"
            else:
                msg = "(no models)"
            return [("class:muted", "  " + msg)]

        # Every row spans one width: the model name on the left, cut to
        # what the pane leaves it, then the two fixed columns at the
        # right edge — same place on every row, whatever arrives later.
        # The marks stand against the figures, and the name gives way to
        # them — down to `_MIN_NAME`, past which the marks give way.
        story, pinned = api_app.specs(self.api)
        marks = [self._marks(e, story, pinned) for e in rows]
        # A catalog row has no size at all — not even the unknown dash.
        sizes = ["" if e.cloud else format_size(e.size_bytes) for e in rows]
        labels: list[str] = []
        for i, entry in enumerate(rows):
            # The marks sit `_MARK_GAP` before the size's own text, so what
            # they take from the name is whatever overflows the size column.
            marked = sizes[i] if not marks[i] else marks[i] + " " * _MARK_GAP + sizes[i]
            room = self._name_width() - max(0, len(marked) - _SIZE_COL)
            if marks[i] and room < min(len(entry.model), _MIN_NAME):
                marked, room = sizes[i], self._name_width()
            sizes[i] = marked
            labels.append(truncate(entry.model, max(0, room)))
        # The context column says what a request would get: the running
        # instance's size while one runs, the model's own otherwise.
        contexts = [format_context(e.context_loaded or e.context_length) for e in rows]
        width = self._row_width()

        out: StyleAndTextTuples = []
        prev_provider: str | None = None
        for i, entry in enumerate(rows):
            if entry.provider_name != prev_provider:
                # The provider panel's structure, mirrored: a caption, a
                # blank, the rows — a provider with no models is absent.
                if prev_provider is not None:
                    out.append(("", "\n"))
                caption = self._captions.get(entry.provider_name, entry.provider_name)
                out.append(("class:preview.title", "  " + caption + "\n"))
                out.append(("", "\n"))
                prev_provider = entry.provider_name
            # The cursor shows only while this side has the focus — on the
            # providers side the left list carries no selection at all.
            selected = i == self.cursor and self.side == "models"
            head = ("  > " if selected else "    ") + labels[i]
            tail = (
                " " * _COL_GAP
                + sizes[i].rjust(_SIZE_COL)
                + " " * _COL_GAP
                + contexts[i].rjust(_CONTEXT_COL)
            )
            gap = max(0, width - len(head) - len(tail))
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
                self.api.providers.models.update(entry.provider_name, entry.model, loaded=load)
                # ollama's /api/ps lags ~100-200ms behind /api/generate's
                # response — without this sleep the refresh below sees the
                # model still loaded after an unload.
                time.sleep(0.2)
                # Refresh load state for this provider only — under the
                # lock, a concurrent catalog refresh may be swapping the
                # list.
                fresh = {
                    model.name
                    for model in self.api.providers.models.list(entry.provider_name)
                    if model.state is ModelState.LOADED
                }
                with self._lock:
                    for e in self.all:
                        if e.provider_name == entry.provider_name:
                            e.loaded = e.model in fresh
            except Refused as ex:
                err = str(ex)
            except Exception as ex:
                err = f"{type(ex).__name__}: {ex}"

            with self._lock:
                self.busy = False
                self.busy_error = err
                exit_now = err is None and self._exit_on_success
            if exit_now:
                self._finish_pick(entry)
                return
            with contextlib.suppress(Exception):
                self.application.invalidate()

        def animator() -> None:
            while True:
                with self._lock:
                    if not self.busy:
                        return
                    self.spinner_frame = (self.spinner_frame + 1) % len(SPINNER_FRAMES)
                try:
                    self.application.invalidate()
                except Exception:
                    return
                time.sleep(0.1)

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=animator, daemon=True).start()

    def _finish_pick(self, entry: ModelEntry) -> None:
        """Settle the choice and leave. The switch is EXECUTED in `run`,
        on the caller's thread: this is reached from a load's worker
        thread too, and that one may not be the thread reloading the
        app's parameters and writing state.toml."""
        self._picked = entry
        with contextlib.suppress(Exception):
            self.application.exit()

    def _request_load(self) -> None:
        if self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if not entry.can_manage or entry.loaded:
            return  # can't load on this provider, or already loaded — a no-op
        self.confirming_action = "load"
        self.confirming_entry = entry

    def _request_unload(self) -> None:
        if self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        if not entry.can_manage or not entry.loaded:
            return  # can't unload on this provider, or not loaded — a no-op
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

    def _toggle_extraction(self) -> None:
        """`x`: pin lore extraction to the highlighted model, or — on the
        one pinned — back to the model played with, through `backend.api`
        at once. The row's mark is the answer; a refusal is kept in
        `notice`, drawn nowhere."""
        if self.side != "models" or self.in_filter or self.confirming_action or not self.filtered:
            return
        entry = self.filtered[self.cursor]
        try:
            if entry.full_spec == api_app.specs(self.api)[1]:
                self.notice = self.api.app.update(extraction_model=None).notice
            else:
                self.notice = self.api.app.update(
                    extraction_model=ModelRef(entry.provider_name, entry.model)
                ).notice
        except Refused as e:
            self.notice = str(e)
