"""The live session: what a frontend holds, and the state primitives the
backend's own modules build on.

Product state is readable through read-only properties — every mutation
goes through a backend operation, so a frontend write is a type error,
not a latent divergence from state.toml. The channel methods are the
worker made frontend-safe; everything underscore is the implementation.
The boundary is the PACKAGE: backend's own modules use the underscore
names, frontends never do (grep-enforceable: no `session._` outside
otaku/backend).

Concurrency: the backend is single-threaded by contract — operations
run one at a time on the caller's one session thread (the web runs
every call on a single executor). The exceptions are the channel
methods, safe from any thread: `touch`, `defer`, `status`,
`set_on_status`, `set_on_notice` — and a `WorkerRun`'s
`wait`/`poll`/`cancel`, which
touch only the run's own event. Frontends inherit this rule from here.
"""

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Self

from otaku.backend.paths import Paths
from otaku.context import assembler
from otaku.context.assembler import AssembledPrompt, ContextShape
from otaku.formatting import pretty_path
from otaku.logging import ErrorLog
from otaku.providers import Locality, OpenAIClient, ProviderConfig, Registry, reasoning
from otaku.settings import models as models_file
from otaku.settings import state as state_file
from otaku.settings.config import Config, TerminalSettings, WebSettings
from otaku.settings.prompts import Prompts
from otaku.settings.state import THINK_DEFAULT, State
from otaku.store import Store
from otaku.store.schema import Message
from otaku.worker import Worker

# The /set think ladder as every menu offers it: default first (the way
# out), then the levels by effort. A frontend-shared ORDER, so it lives
# here with the rest of the /set vocabulary — the file's own vocabulary
# is `settings.state.THINK_LEVELS` (a set), and a unit test pins the
# two consistent. The typed sugar (`on`/`off`) is the command surface's
# and stays out of a menu of VALUES.
THINK_MENU: tuple[str, ...] = (THINK_DEFAULT, "none", "low", "medium", "high", "xhigh", "max")

# The reasoning efforts in the wire's order, weakest to strongest, for
# a frontend listing the ones a model honours.
EFFORTS: tuple[str, ...] = reasoning.EFFORTS

# The inference parameters otaku understands, and how each is read from
# the saved file or a `/set parameter` argument.
KNOWN_PARAMS: dict[str, type] = {
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "min_p": float,
    "max_tokens": int,
    "presence_penalty": float,
    "frequency_penalty": float,
    "repetition_penalty": float,
    "seed": int,
    "stop": str,
}

# What every model-facing door says while no model is selected, and
# every story-facing one while nothing has been played — each spelled
# once, so no door (or frontend) drifts into its own wording.
NO_MODEL_HINT = "No model selected — pick one with /model."
NO_STORY_HINT = "No story yet — send a message first."


class Refused(Exception):  # noqa: N818 — a refusal is an expected answer, not an error
    """An operation declined for an expected reason; str(e) is the exact
    sentence to show. The backend's one refusal channel — frontends catch
    it at every call site and print, so no operation needs a `| str`
    return. Always raised EAGERLY — never mid-stream (that is the
    `Declined` event's job)."""


class Session:
    """One user's live session over one state dir — the object the
    frontends hold and every backend operation takes first. A plain
    class on purpose: `start` is the ONE constructor, and it owns the
    invariants a generated `__init__` would skip. Ends with `close()`."""

    # What the launch has to say. Two channels, both one-shot (the
    # frontend prints and clears; the two deliberately mutable public
    # fields): `notices` are the launch's REPORTS — created files, stale
    # settings, keys that would not open — shown before the banner;
    # `notice` is the one BOLD line shown after the resumed scene (the
    # sample-story hint), where a hint belongs and a warning does not.
    notices: list[str]
    notice: str
    # Where a notice goes once the launch is over: the frontend attaches
    # a sink (`set_on_notice`) and later notices go straight out, the way
    # they were printed on the spot before the backend existed.
    _notify: Callable[[str], None] | None
    # What to do with this thread while a reply is waited on — a
    # frontend that runs its own work on it attaches one (`set_on_idle`).
    _on_idle: Callable[[], bool | None] | None
    # Product state (read through the properties below). The model is
    # `_state`'s — it is what state.toml remembers, and the halves the
    # app works in are its own to split.
    _story_id: int | None
    _system: str
    _messages: list[Message]
    _params: dict[str, object]
    # The stories content index behind `api.stories.search` — built on
    # the first search, invalidated by the write primitives below.
    _search_index: dict[int, str] | None
    # The loaded settings files and the machinery (package-internal).
    # `_state` is the one that also carries product state: the model and
    # the /set toggles read through it, since state.toml is where they
    # persist. Only the story id stays a live field above, projected in
    # whenever `_update_state` writes.
    _config: Config
    _prompts: Prompts
    _state: State
    _paths: Paths
    _store: Store
    _providers_registry: Registry
    _worker: Worker
    _closed: bool

    @classmethod
    def start(
        cls,
        *,
        config: Config,
        prompts: Prompts,
        paths: Paths,
        store: Store,
        registry: Registry,
        worker: Worker,
        state: State,
    ) -> Self:
        """The session over `state` — what the last one remembered, with
        the launch's own corrections already in it (a model whose
        provider is gone comes in blank: validating it is the launch's
        job, and has one home there). Saved parameters, persisted
        toggles, and the remembered story applied; a stale remembered
        value lands in `notices` and is skipped — never a failed
        launch."""
        session = cls.__new__(cls)
        session.notices = []
        session.notice = ""
        session._notify = None
        session._on_idle = None
        session._story_id = None
        session._system = ""
        session._messages = []
        session._params = {}
        session._search_index = None
        session._config = config
        session._prompts = prompts
        session._state = state.settled()
        session._paths = paths
        session._store = store
        session._providers_registry = registry
        session._worker = worker
        session._closed = False
        session._reload_params()
        # Reattach the story the previous session was on, so bare `otaku`
        # reopens it mid-scene; one deleted since simply starts fresh.
        if state.story and store.stories.exists(state.story):
            session._switch_to(state.story)
        return session

    def close(self) -> None:
        """Shut the worker down (non-blocking) and close the store.
        Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._worker.shutdown()
        self._store.close()

    # ---------- the read-only product state ----------

    @property
    def provider(self) -> str:
        """The active provider's name; "" while no model is selected."""
        return self._state.provider

    @property
    def on_cloud(self) -> bool:
        """Whether the story is played against a hosted catalog — the
        prompt marker's question, answered per turn. The generic provider
        is not one: its url could name a catalog, but the marker says
        what is known, not what might be."""
        client = self._client()
        return client is not None and client.locality is Locality.REMOTE

    @property
    def model(self) -> str:
        """The bare model name, as the server expects it."""
        return self._state.bare_model

    @property
    def story_id(self) -> int | None:
        return self._story_id

    @property
    def system(self) -> str:
        """The story's system prompt; never a message row."""
        return self._system

    @property
    def messages(self) -> Sequence[Message]:
        """The story as loaded, root → head."""
        return self._messages

    @property
    def params(self) -> Mapping[str, object]:
        """The model's saved parameters in force."""
        return self._params

    @property
    def think(self) -> str | None:
        """A `state.THINK_LEVELS` value; None = defer to the model."""
        return None if self._state.think == THINK_DEFAULT else self._state.think

    @property
    def verbose(self) -> bool:
        return self._state.verbose

    @property
    def autocorrect(self) -> bool:
        return self._state.autocorrect

    @property
    def notification(self) -> bool:
        """Whether a landed reply should call the user back to the
        screen; HOW is the frontend's own business."""
        return self._state.notification

    @property
    def max_context_setting(self) -> int:
        """Tokens the prompt may use at most — config.toml's [context]
        value, which /set max_context edits in place; 0 means the
        model's own max context."""
        return self._config.max_context

    @property
    def full_model_name(self) -> str:
        """ "provider/model" as remembered, for display; "" without a
        model, a half-written spec included."""
        return self._state.model if self._state.bare_model else ""

    @property
    def terminal(self) -> TerminalSettings:
        """The terminal frontend's configured looks, for its own launch."""
        return self._config.terminal

    @property
    def web(self) -> WebSettings:
        """Where the web frontend listens — its slice, as `terminal` is
        that one's. A frontend reads one slice and only its own; both
        arrive here rather than being read from the file twice."""
        return self._config.web

    @property
    def custom_web_dir(self) -> Path:
        """The reader's OWN directory in the state dir — the stylesheet
        and typefaces the page loads last, which otaku never writes. One
        of the two paths a frontend is handed, and handed because it
        belongs to the reader rather than to the app; the rest of the
        layout stays the composition root's."""
        return self._paths.custom_web_dir

    @property
    def cert_dir(self) -> Path:
        """Where the web frontend's TLS pair lives. The other path handed
        out, for the opposite reason: the medium's own file, which only
        the frontend that speaks HTTP has any use for."""
        return self._paths.cert_dir

    # ---------- what frontends may call ----------

    def max_context(self) -> int | None:
        """The context the model gets, for a header to state — None when
        nobody can say. Best-effort and never blocking on the internet:
        a CLOUD catalog is not asked, because its answer lives across
        the internet and a launch does not wait for that; the generic
        provider, whose url could name one, answers from its cache
        alone, nothing over the wire — None until a listing warmed it.
        Not a property: a local engine is asked over its own socket."""
        client = self._client()
        if client is None or client.locality is Locality.REMOTE:
            return None
        try:
            if client.locality is Locality.UNKNOWN:
                found = client.models.cached(self.model)
            else:
                found = client.models.get(self.model)
        except Exception:
            return None
        return found.max_context if found else None

    def start_worker(self) -> None:
        """Start the background actor — called once by the frontend, the
        moment it can repaint (`open_session` builds it unstarted so no
        status line races the first draw)."""
        self._worker.start()

    def record_crash(self, context: str, exc: BaseException) -> str:
        """A contained crash into the error log; the day-file's pretty
        path, for the one line the frontend prints. Never raises."""
        try:
            return pretty_path(ErrorLog(self._paths.logs_dir).record(context, exc))
        except Exception:
            return ""

    def touch(self) -> None:
        """The user is typing — a pending extraction pass waits for real
        idle. Called per keystroke; stays cheap. Any thread."""
        self._worker.touch()

    def defer(self) -> None:
        """The user submitted — queued background work is dropped. Any
        thread."""
        self._worker.defer()

    def status(self) -> str:
        """The background worker's one-line account; "" when idle. Any
        thread."""
        return self._worker.status()

    def set_on_status(self, repaint: Callable[[], None]) -> None:
        """The status repaint hook (thread-safe on the caller's side)."""
        self._worker.on_status = repaint

    def set_on_idle(
        self, tick: Callable[[], bool | None] | None
    ) -> Callable[[], bool | None] | None:
        """What to do with this thread while a reply is being waited on.
        Called on the session's OWN thread, many times a second, from
        the moment a request goes out until the last token — a frontend
        that shares that thread (the web serves its reads on it) uses
        this to stay answerable while the model talks. It may answer
        False to say nobody reads the reply any more (a page that hung
        up): the reply ends at once, its request cut and what arrived
        kept. The terminal needs no answer, its Ctrl+C lands in the wait
        itself. Whatever the hook raises is swallowed: it may not break
        a reply.

        Returns the hook it replaces, so a frontend borrowing the
        session for a while (`/web`) can put the owner's back."""
        previous = self._on_idle
        self._on_idle = tick
        return previous

    def set_on_notice(self, say: Callable[[str], None] | None) -> Callable[[str], None] | None:
        """Where a notice goes from now on. The launch's own reports are
        collected in `notices` because nothing can print yet; everything
        after — a story deleted under the session, a saved parameter the
        model's vocabulary rejects — is said when it happens, so the
        frontend attaches this once it owns the screen.

        Returns the sink it replaces, so a frontend borrowing the
        session for a while (`/web`) can put the owner's back — a chat
        that lost its sink here would never hear the worker again."""
        previous = self._notify
        self._notify = say
        return previous

    def _note(self, text: str) -> None:
        """Tell the user something the session had to decide on its own.
        Before a frontend is listening it joins `notices`, which the
        launch prints; after, it is said where it happens."""
        if self._notify is None:
            self.notices.append(text)
        else:
            self._notify(text)

    def history(self) -> list[str]:
        """The prompt's Up/Down input history, most recent first —
        store-backed, so it survives sessions. Best-effort: a store
        hiccup yields an empty list, never a broken prompt."""
        try:
            return self._store.history.get_recent()
        except Exception:
            return []

    def record_history(self, text: str) -> None:
        """Remember one submitted line (blanks and immediate repeats
        are skipped). Best-effort; never raises."""
        with contextlib.suppress(Exception):
            self._store.history.add(text)

    def assemble(self, max_context: int | None) -> AssembledPrompt:
        """The next request — the one binding of the session's fields to
        `assembler.assemble_story`, so the turn, the preview, and every
        other call site can never disagree on what is sent. Raises
        `ContextOverflowError` when the story cannot fit the limit even
        fully degraded."""
        return assembler.assemble_story(
            self._store,
            self._story_id,
            system=self._system,
            messages=list(self._messages),
            shape=self._shape(),
            max_context=max_context,
        )

    # ---------- state primitives (backend package internal) ----------

    def _provider_config(self) -> ProviderConfig | None:
        """The active provider's CURRENT configuration, resolved through
        the registry by name — never a snapshot."""
        if not self.provider:
            return None
        client = self._providers_registry.get(self.provider)
        return client.config if client is not None else None

    def _client(self) -> OpenAIClient | None:
        if not self.model:
            return None
        return self._providers_registry.get(self.provider)

    def _shape(self) -> ContextShape:
        """The assembly shape: config's window settings + the prompts'
        recap header and card template, read fresh each call."""
        return ContextShape(
            head_messages=self._config.head_messages,
            min_tail_messages=self._config.min_tail_messages,
            max_context_setting=self.max_context_setting,
            recap_header=self._prompts.recap_header,
            card_framing=self._prompts.card_framing,
        )

    def _switch_to(self, story_id: int, messages: list[Message] | None = None) -> None:
        """Attach to a story — system, messages (or the given truncated
        list), remembered state. The ONE way a session changes stories,
        so none of the doors (launch resume, the browser, an import) can
        forget a piece."""
        self._story_id = story_id
        self._system = self._store.stories.get_system(story_id)
        self._messages = (
            self._store.stories.get_messages(story_id) if messages is None else messages
        )
        self._update_state()

    def _ensure_story(self) -> int:
        """The story id, creating the story on the first real turn — so a
        knob or an immediate exit never leaves an empty row behind. A
        story deleted out from under the session is detected here too:
        without this, later writes would fail while the session only
        looked recorded."""
        if self._story_id is not None and not self._store.stories.exists(self._story_id):
            self._story_id = None
            self._messages = []
            self._note("The story was deleted — continuing in a new one.")
        if self._story_id is None:
            self._story_id = self._store.stories.add()
            if self._system:
                self._store.stories.set_system(self._story_id, self._system)
            self._update_state()
        return self._story_id

    def _record_turn(self, message: Message) -> None:
        """Append one turn to the session and the store, the in-memory
        copy carrying its assigned id."""
        story_id = self._ensure_story()
        message_id = self._store.stories.append(story_id, message)
        self._messages.append(replace(message, id=message_id))
        self._search_index = None

    def _undo(self) -> list[Message]:
        """Discard the trailing exchange: the assistant reply (if any)
        plus the ONE user row that prompted it — every submission is a
        single row (a /me or /you direction rides its row's template),
        and an imported backlog of consecutive user rows is story, not
        one submission. Nothing is deleted: the head moves back and the
        undone turns stay in the tree as siblings. Returns the popped
        messages."""
        popped: list[Message] = []
        if not self._messages:
            return popped
        if self._messages[-1].role == "assistant":
            popped.append(self._messages.pop())
        if self._messages and self._messages[-1].role == "user":
            popped.append(self._messages.pop())
        if popped:
            self._move_head()
            self._search_index = None
        return popped

    def _drop_last_reply(self) -> Message | None:
        """Pop the trailing assistant reply (regenerate's first half): the
        head moves back one; the discarded reply stays in the tree as a
        sibling."""
        if not self._messages or self._messages[-1].role != "assistant":
            return None
        popped = self._messages.pop()
        self._move_head()
        self._search_index = None
        return popped

    def _set_system(self, text: str) -> None:
        """The story's system prompt — persisted with the story when one
        exists; a story created later picks it up at creation."""
        self._system = text
        if self._story_id is not None:
            self._store.stories.set_system(self._story_id, text)

    def _reload_params(self) -> None:
        """Replace the live parameters with the current model's saved
        ones — parameters follow the model, at startup and on a switch.
        A saved value the vocabulary no longer makes sense of lands in
        `notices` and is skipped."""
        self._params = {}
        saved = models_file.load(self._paths.models_file).get(self.model, {})
        for name, value in saved.items():
            coerce = KNOWN_PARAMS.get(name)
            if coerce is None:
                self._note(f"Ignoring unknown parameter {name!r} saved for {self.model}.")
                continue
            try:
                self._params[name] = coerce(value)
            except (TypeError, ValueError):
                self._note(f"Ignoring invalid {name} value {value!r} saved for {self.model}.")

    def _update_state(self, **fields: Any) -> None:
        """Change what state.toml remembers — `fields` are `State`'s own —
        and write it; called bare, it just writes what the live session
        made true. The ONE door either way, so no setter can change a
        toggle and forget to persist it.

        What is written is the remembered state with the story id — the
        one thing it is not the home of, `None` being a better absent
        than 0 — projected in from the live session.
        Best-effort — remembered state is never worth failing a turn."""
        if fields:
            self._state = replace(self._state, **fields)
        with contextlib.suppress(OSError):
            state_file.save(
                self._paths.state_file,
                replace(self._state, story=self._story_id or 0),
            )

    def _move_head(self) -> None:
        """Point the story at the session's last message (None when empty)."""
        if self._story_id is None:
            return
        head = self._messages[-1].id if self._messages else None
        self._store.stories.set_head(self._story_id, head)
