"""The app: otaku opened over one state dir — what one launch is made of
(the paths, the store, the providers, the integrations, the worker, the
extraction runs asked for), the channels a frontend holds (the worker's,
the crash log, the idle hook), and the backend's own reads of the
settings files and the model in use, and what the launch that opened
it had to say (`LaunchFacts`). It holds no story, no setting and no
fact of its own: those are the domain's reads and writes, every one
taking the app first; a story's chain is operated by `backend.story.chain`.

The boundary is the DOMAIN: its modules use the underscore names, and
nothing above it does but the launch that composes the app (the
architecture test holds it: no `app._` outside the services and their machinery).

Concurrency: from the end of the launch the app has a thread of its own
(`backend.app.thread`), and every verb of the interface runs on it, one
at a time, whichever thread calls — `run` makes several calls one unit,
`stream` hands a reply's events across. The exceptions run on their
caller's thread: the channels below, the verbs marked `any_thread`
(`backend.base`), and a `WorkerRun`'s `wait`/`poll`/`cancel`, which touch
only the run's own event.
"""

import contextlib
import threading
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self, TypeVar, cast

from otaku.agents import Worker
from otaku.backend.app import passwords
from otaku.backend.app.models import services as models
from otaku.backend.app.models.services import SavedModelSettings
from otaku.backend.app.paths import Paths
from otaku.backend.app.runs import WorkerRun
from otaku.backend.app.thread import AppThread
from otaku.backend.errors import Refused, Unavailable
from otaku.formatting import pretty_path
from otaku.integrations import Integrations
from otaku.logging import ErrorLog
from otaku.providers import (
    Locality,
    OpenAIClient,
    Registry,
    reasoning,
    templates,
)
from otaku.settings import config as config_file
from otaku.settings import models as models_file
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_file
from otaku.settings.config import Config, ConfigError
from otaku.settings.prompts import Prompts
from otaku.settings.state import ModelRef, StateConfig
from otaku.store import Store

T = TypeVar("T")
E = TypeVar("E")


class App:
    """otaku opened over one state dir: the handles every backend call
    reads through, and the channels a frontend holds. A plain class on
    purpose: `start` is the ONE constructor, and it owns the invariants a
    generated `__init__` would skip. Ends with `close()`."""

    # What one launch is made of (package-internal).
    _paths: Paths
    _store: Store
    _providers: Registry
    _integrations: Integrations  # as bound
    _worker: Worker
    # The app's own thread, and the queue every call goes through.
    _thread: AppThread
    # What its launch had to say — set by `open_app` once it is said.
    _launch_facts: "LaunchFacts"
    # The forced extraction passes asked for, the newest per story.
    _extraction_runs: dict[int, WorkerRun]
    # Caches: each settings file as last read, by its stat (`_read_file`);
    # the stories content index behind `api.stories.list_stories` — built
    # on the first search, reset by every chain write (`backend.story.chain`).
    _file_cache: dict[Path, tuple[tuple[int, int, int] | None, Any]]
    _search_index: dict[int, str] | None
    # What a wait on the model does with the app's thread: the reads
    # queued answered, and False once the caller gave up (`AppThread.idle`).
    _on_idle: Callable[[], bool]
    _closed: bool

    @classmethod
    def start(
        cls,
        *,
        config: Config,
        paths: Paths,
        store: Store,
        providers: Registry,
        integrations: Integrations,
        worker: Worker,
    ) -> Self:
        """The app over one state dir. `config` is config.toml as the
        launch read it — a web password hashed where the file could not
        take the hash — standing until the file changes. Nothing is asked
        of a provider here: the launch makes the remembered model current
        (`_make_current`)."""
        app = cls.__new__(cls)
        app._paths = paths
        app._store = store
        app._providers = providers
        app._integrations = integrations
        app._worker = worker
        app._thread = AppThread()
        app._launch_facts = LaunchFacts()
        app._extraction_runs = {}
        app._file_cache = {paths.config_file: (_file_key(paths.config_file), config)}
        app._search_index = None
        app._on_idle = app._thread.idle
        app._closed = False
        return app

    def close(self) -> None:
        """Stop the app's thread — the job in hand given up on and waited
        out, every call still queued answered StoppingError — shut the
        worker down (non-blocking) and close the store. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._thread.close()
        self._worker.shutdown()
        self._store.close()

    # ---------- the channels a frontend holds ----------

    def start_worker(self, *, on_status: Callable[[], None] | None = None) -> None:
        """Start the background worker, the moment the frontend can draw
        (`open_app` builds it unstarted so no status line races the first
        draw) — `on_status` repainting where the worker's line shows
        (`api.app.get_status`), thread-safe on the caller's side. A second
        call starts nothing, and keeps the repaint hook it was given
        before."""
        if on_status is not None:
            self._worker.on_status = on_status
        self._worker.start()

    def note_typing(self) -> None:
        """The reader is typing: a pending extraction pass waits a full
        idle window again. Called per keystroke; stays cheap. Any
        thread."""
        self._worker.touch()

    def note_submission(self) -> None:
        """The reader submitted: the queued background job is dropped and
        the warm-up skipped; a running pass is left alone. Any thread."""
        self._worker.defer()

    def record_crash(self, context: str, exc: BaseException) -> str:
        """A contained crash into the error log; the day-file's pretty
        path, for the one line the frontend prints. Never raises."""
        try:
            return pretty_path(ErrorLog(self._paths.logs_dir).record(context, exc))
        except Exception:
            return ""

    def run(self, work: Callable[[], T], *, reading: bool = False) -> T:
        """`work` as ONE unit on the app's thread, nothing else running
        beside it — several calls that must see one state — the caller
        waiting for its answer or what it raised. `reading` promises it
        only reads, so it may run between a reply's events. Any thread;
        where it is on the app's own."""
        return self._thread.run(work, reading=reading)

    def stream(self, start: Callable[[], Iterator[E]]) -> Iterator[E]:
        """A reply on the app's thread, its events handed to the caller
        as they come: `start` refuses before the first, and closing the
        answer cancels the reply, what arrived kept. Any thread."""
        return self._thread.stream(start)

    # ---------- primitives (backend package internal): the settings files ----------

    def _config(self) -> Config:
        """config.toml as it stands — the last good read where a hand
        edit left it unreadable: a broken line mid-run is never worth a
        failed turn, and the next launch says what is wrong with it."""
        return self._read_file(self._paths.config_file, self._load_config)

    def _state_config(self) -> StateConfig:
        """state.toml as it stands: the settings otaku writes itself."""
        return self._read_file(self._paths.state_file, lambda path: state_file.load(path)[0])

    def _prompts(self) -> Prompts:
        """The prompts, as prompts.toml stands — an override it cannot
        read passed over (and said at the launch)."""
        return self._read_file(self._paths.prompts_file, lambda path: prompts_file.load(path)[0])

    def _model_entries(self) -> dict[str, dict[str, object]]:
        """models.toml as it stands: each model's entry, by bare name."""
        return self._read_file(self._paths.models_file, models_file.load)

    def _update_state_config(self, *, best_effort: bool = False, **fields: Any) -> None:
        """Change what state.toml holds — `fields` are `StateConfig`'s own —
        over the file as it stands, the ONE door, so no setter can change
        a value and forget to persist it. Raises Unavailable where the
        file could not be written: nothing changed. `best_effort` is for
        what is never worth failing the call it rides: the story landed
        in."""
        try:
            state_file.save(self._paths.state_file, replace(self._state_config(), **fields))
        except OSError as e:
            if not best_effort:
                raise Unavailable(f"Not saved — state.toml could not be written: {e}") from e

    def _read_file(self, path: Path, load: Callable[[Path], T]) -> T:
        """A settings file as it stands now: read again whenever its stat
        changed — a write here, a hand edit, another process — and from
        memory otherwise. The inode is in the key because every write
        replaces the file whole (`write_atomic`): two writes within one
        tick of the clock, of one size, still differ there."""
        key = _file_key(path)
        held = self._file_cache.get(path)
        if key is not None and held is not None and held[0] == key:
            return cast(T, held[1])
        value = load(path)
        self._file_cache[path] = (key, value)
        return value

    def _load_config(self, path: Path) -> Config:
        """config.toml read for `_config`: the last good read kept where it
        will not parse, a web password the file still holds typed hashed
        in memory, as the launch hashes it."""
        try:
            config = config_file.load(path)
        except ConfigError:
            held = self._file_cache.get(path)
            if held is None:
                raise
            return cast(Config, held[1])
        if config.web_password and not passwords.is_hashed(config.web_password):
            config = replace(config, web_password=passwords.hash(config.web_password))
        return config

    # ---------- primitives: the model in use ----------

    def _model(self) -> ModelRef | None:
        """The model in use: the one state.toml remembers — None while
        none is, and while it names a provider that is not configured
        (the launch says so)."""
        model = self._state_config().model_ref
        return model if model is not None and model.provider in self._providers.list() else None

    def _extraction_pin(self) -> ModelRef | None:
        """The model saved for lore extraction — kept when the one played
        with changes; None while extraction follows it, and while the
        one saved names a provider that is not configured."""
        pin = self._state_config().extraction_ref
        return pin if pin is not None and pin.provider in self._providers.list() else None

    def _extraction_model(self) -> ModelRef | None:
        """The model lore extraction asks: the one saved for it, else the
        model in use."""
        return self._extraction_pin() or self._model()

    def _client(self, model: ModelRef | None) -> OpenAIClient | None:
        """The client that serves `model`; None for none."""
        return self._providers.get(model.provider) if model is not None else None

    def _model_facts(self) -> "models.ModelFacts":
        """The model in use as it stands (`ModelFacts`) — read off what is
        cached, nothing asked of an engine. What leaves this machine:
        the story when its model is on a hosted catalog, or lore
        extraction, running on idle, when its own is; with idle
        extraction off the story leaves only when a pass is asked for,
        so its model says nothing here. The generic provider is not a
        catalog: its url could name one, but the marker says what is
        known, not what might be."""
        model = self._model()
        client = self._client(model)
        row = client.models.cached(model.name) if client is not None and model is not None else None
        caps = row.capabilities if row is not None else None
        template = (
            templates.resolve(
                self._model_settings(model.name).text_template,
                row.text_template if row is not None else None,
                model.name,
            )
            if model is not None
            else None
        )
        on_cloud = (
            client is not None
            and model is not None
            and client.locality_of(model.name) is Locality.REMOTE
        )
        if not on_cloud and self._config().lore_enabled:
            extraction = self._extraction_model()
            extracting = self._client(extraction)
            on_cloud = (
                extracting is not None
                and extraction is not None
                and extracting.locality_of(extraction.name) is Locality.REMOTE
            )
        return models.ModelFacts(
            model=model,
            client=client,
            row=row,
            vision=caps is not None and caps.vision is True,
            text_completion=caps is not None and caps.text_completion is True,
            text_template=template,
            on_cloud=on_cloud,
        )

    def _make_current(self) -> list[str]:
        """The model in use made current — at launch and on a switch —
        and what its saved settings have to be told, as sentences.

        Its row is read first, so what the menus read off the cache, how
        its thinking is set above all, is there before the first turn's
        ask would fill it in. A LOCAL engine is asked at once, over its
        own socket: a /set think menu must not change under the reader
        between the first keystroke and the first reply. A REMOTE
        catalog's row lives across the internet and a launch does not
        wait for that, so it is asked on a thread of its own and lands
        moments later — until then the menus offer everything, as they do
        for an engine that does not answer. The generic provider is not
        asked: its cache fills on the first listing.

        Then each saved value the vocabulary no longer makes sense of is
        said (`models.entry_notes`). And an entry with no think
        key reads as OFF where the provider says the model thinks — the
        off word of its shape (`reasoning.off_level`), written as `/set
        think off` would write it, so from then on the model has a row and
        every word means what it always did — and as nothing where the
        provider cannot describe the model or says it does not think: no
        row, unset until the provider can say."""
        model = self._model()
        client = self._client(model)
        if model is None or client is None:
            return []
        if client.locality is Locality.LOCAL:
            with contextlib.suppress(Exception):
                client.models.get(model.name)
        elif client.locality is Locality.REMOTE:

            def read() -> None:
                with contextlib.suppress(Exception):
                    client.models.get(model.name)

            threading.Thread(target=read, name="otaku-model-read", daemon=True).start()
        notes = models.entry_notes(self._model_entries().get(model.name, {}), model.name)
        if self._model_settings(model.name).think is None:
            found = client.models.cached(model.name)
            word = reasoning.off_level(found.capabilities if found is not None else None)
            if word is not None:
                try:
                    self._write_model_settings(model.name, {models_file.THINK_KEY: word})
                except Refused as e:
                    notes.append(f"Think for {model.name} stays unset. {e}")
        return notes

    def _model_settings(self, name: str) -> SavedModelSettings:
        """A model's saved settings — its entry in models.toml, saved under
        its bare name — as the vocabulary reads them."""
        return models.read_entry(self._model_entries().get(name, {}) if name else {})

    def _write_model_settings(
        self, name: str, changes: Mapping[str, object], *, drop: Collection[str] = ()
    ) -> None:
        """Write a model's entry: the file read as it stands, `changes`
        laid over it and `drop` taken out — the rest as written, so a hand
        edit or another process's write is never lost. Raises Unavailable
        where the file could not be written: nothing changed."""
        entry = {
            key: value
            for key, value in {**self._model_entries().get(name, {}), **changes}.items()
            if key not in drop
        }
        try:
            models_file.save(self._paths.models_file, name, entry)
        except (OSError, ValueError) as e:
            raise Unavailable(f"Not saved — models.toml could not be written: {e}") from e


@dataclass(frozen=True)
class LaunchFacts:
    """What the launch that opened the app had to say before anything
    else could: `notices`, its REPORTS — created files, stale settings,
    keys that would not open — shown before the banner; and `hint`, the
    one BOLD line shown after the resumed scene (the sample-story hint),
    where a hint belongs and a warning does not; "" for none."""

    notices: tuple[str, ...] = ()
    hint: str = ""


# ---------- the app's own facts, for the interface ----------


def launch_facts(app: App) -> LaunchFacts:
    """What the launch that opened the app had to say."""
    return app._launch_facts


def get_status(app: App) -> str:
    """The background worker's one-line account; "" when idle. Safe from
    any thread."""
    return app._worker.status()


def _file_key(path: Path) -> tuple[int, int, int] | None:
    """A file's identity as `App._read_file` keys it; None where it is
    not there."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)
