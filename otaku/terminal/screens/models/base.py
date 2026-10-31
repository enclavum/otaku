"""The picker's skeleton, below its two sides: a model as a row lists it
(`ModelEntry`), the row a cursor stands on (`cursor_line`), and the
picker's state — the providers, the rows that arrived, the cursor on
each side — with the hooks the sides and the assembled picker fill."""

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer

from otaku.terminal.api import Api, Locality, ModelRef, Refused
from otaku.terminal.api.providers import ListedProvider, ProviderField
from otaku.terminal.screens.base import ListScreen

_ATTRS: tuple[ProviderField, ...] = ("url", "api_key")  # the panel's rows per provider, in order


@dataclass
class ModelEntry:
    full_spec: str  # "provider/model"
    provider_name: str
    model: str
    loaded: bool
    can_manage: bool = True  # False → served statically
    size_bytes: int | None = None  # None when the provider doesn't expose it
    context_length: int | None = None  # the model's own maximum, when its provider states it
    context_loaded: int | None = None  # what the running instance serves, while one runs
    # A row with no disk to weigh — a catalog's, or the generic
    # provider's: normal weight, no size.
    cloud: bool = False


class ModelPickerBase(ListScreen):
    """The picker's state, and its life."""

    def __init__(
        self,
        api: Api,
        providers: list[ListedProvider],
        entries: list[ModelEntry],
        initial_spec: str | None = None,
        *,
        fetch: list[str] | None = None,
        connected: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.api = api
        # The panel vocabulary — captions, order, and where each runs —
        # from the backend's one source.
        self.providers = providers
        self._order = {provider.id: i for i, provider in enumerate(providers)}
        self._captions = {provider.id: provider.label for provider in providers}
        # Names whose last listing succeeded — the panel's tick: the
        # provider answered, and with the right key where one is needed.
        self.connected: set[str] = set(connected or ())
        self.all: list[ModelEntry] = list(entries)
        self.filtered: list[ModelEntry] = list(entries)
        self._initial_spec = initial_spec
        self._drawn_cursor_line = 0

        # The provider panel (the right side): the walkable field list —
        # two rows per provider, except the cloud catalogs whose url is
        # fixed (their API key alone) — its own cursor (both sides stay
        # visible, so the base's one integer serves the models side and
        # the key handling swaps), the inline editor.
        self.side: str = "models"
        self.fields: list[tuple[str, ProviderField]] = [
            (provider.id, attr)
            for provider in providers
            for attr in _ATTRS
            if attr != "url" or provider.locality is not Locality.REMOTE
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

        # The switch notice on a confirmed pick; None while browsing.
        self.result: str | None = None
        # The settled choice, executed by `run` once the screen is down.
        self._picked: ModelEntry | None = None
        self.application = self._build_application()

        # Every provider answers after the screen is up: each listing
        # marks itself pending, merges its rows in, and clears the mark.
        self.pending: set[str] = set()
        for name in fetch or []:
            self._refresh_provider(name)
        # Nothing to ask and nothing to show — a machine with no provider
        # configured at all opens on the panel, without waiting for an
        # answer that is never coming.
        self._settle_side()

    def run(self) -> str | None:
        # An empty screen still runs: the provider panel is the one door
        # to configuring a provider, so a machine with nothing reachable
        # must reach it — `pick` alone decides when opening is skipped.
        self.application.run()
        if self._picked is not None:
            # Execute the settled choice (the screens' one ownership
            # rule), back on one thread — an "Already using" refusal is a
            # notice too: either way the app is on that model now.
            try:
                self.result = self.api.app.update(
                    model=ModelRef(self._picked.provider_name, self._picked.model)
                ).notice
            except Refused as e:
                self.result = str(e)
        return self.result

    # ---------- filled above: by the provider panel, or by the assembled picker ----------

    def _build_application(self) -> Application[None]:
        raise NotImplementedError

    def _refresh_provider(self, name: str, *, settled: bool = False) -> None:
        raise NotImplementedError

    def _settle_side(self) -> None:
        raise NotImplementedError


def cursor_line(rows: Sequence[Any], cursor: int) -> int:
    """The visual row of `cursor` in the models list as `_items_text` draws
    `rows`: a caption and a blank above each provider's group, a blank
    between groups. A cursor past the rows (a list a refresh shortened
    under it) lands on the last row; an empty list is row 0."""
    line = 0
    prev: str | None = None
    last = min(cursor, len(rows) - 1)
    for i, entry in enumerate(rows[: last + 1]):
        if entry.provider_name != prev:
            line += 2 if prev is None else 3  # (group gap +) caption + blank
            prev = entry.provider_name
        if i == last:
            return line
        line += 1
    return 0
