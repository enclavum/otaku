"""Session state: what the REPL holds between turns, and how it starts.

`Session.start` builds the session the launcher hands to the REPL: the
chosen model with its saved parameters, the persisted toggles, and the
story the last session was on. `Session.messages` then mirrors that story's
messages (with their ids once stored); the system prompt is its own field,
never a message row. Every change goes to the store through explicit
operations — `record_turn` appends, undo is a `set_head` — and `save_state`
keeps state.toml pointing at the session's model and story so bare `otaku`
resumes them.

A typed message goes to the model verbatim: the only injectors are the
explicit commands (`/me`, `/you`, `/ooc`), and what they inject is a
template written into the turn's `framing`, joined to the body only at
wire time.
"""

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Self

from otaku.chat.markdown import render_markdown
from otaku.lore.worker import LoreWorker
from otaku.paths import Paths
from otaku.providers.registry import Registry as ProviderRegistry
from otaku.settings import models as models_file
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_file
from otaku.settings.config import Config, Provider
from otaku.settings.prompts import Prompts
from otaku.store import Store
from otaku.store.schema import Message
from otaku.store.stories import StoryListing
from otaku.terminal import DIM, RESET
from otaku.terminal.statusline import StatusLine

# The inference parameters otaku understands, and how each is read from the
# saved file or a `/set parameter` argument.
KNOWN_PARAMS: dict[str, type] = {
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "presence_penalty": float,
    "frequency_penalty": float,
    "seed": int,
    "stop": str,
}

# Thinking effort, as `/set think` and state.toml spell it. "default" is not
# a level: it means send nothing and let the model decide (`think = None`).
THINK_LEVELS = {"none", "low", "medium", "high", "max"}
THINK_ALIASES = {"on": "medium", "off": "none"}

# What the story browser hands back on a confirmed selection: the story id,
# its messages up to the picked turn, and the total turn count (so a
# mid-story pick is recognizable).
PickedStory = tuple[int, list[Message], int]

# Turns echoed when a story (re)opens — launch resume, a browser pick, an
# import — so the scene is on screen before the prompt.
RESUME_TURNS = 3


@dataclass(frozen=True)
class TUI:
    """The full-screen surfaces, injected by the CLI. The tui package is
    the adapter; THIS is the port — chat defines what it needs and may not
    import the implementation. Each is None when unavailable."""

    # Opens the model picker on the current "provider/model" and returns
    # the chosen spec — None on cancel.
    pick_model: Callable[[str], str | None] | None = None
    # Opens the story browser over the given listings (the current story
    # pre-selected) and returns a PickedStory — None on cancel.
    pick_story: Callable[[Store, list[StoryListing], int | None], PickedStory | None] | None = None
    # Opens the lore browser on a story, on the given lens ("scenes" or
    # "cast").
    browse_lore: Callable[[Store, int, str], None] | None = None


@dataclass
class Session:
    config: Config
    prompts: Prompts
    paths: Paths
    providers: ProviderRegistry
    provider: Provider
    model: str  # bare model name, as the server expects it
    story_id: int | None = None  # created lazily on the first real turn
    system: str = ""  # the story's system prompt; never a message row
    messages: list[Message] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)
    # Thinking effort: one of THINK_LEVELS; None means send nothing and
    # defer to the model's default. Off unless opted in.
    think: str | None = "none"
    verbose: bool = False  # the stats line after each reply (/set verbose)
    # The REPL's exit flag: /bye (and Ctrl+D) raise it, and the run loop
    # leaves after the current turn instead of unwinding mid-command.
    should_quit: bool = False
    # Verbatim argument text of the slash command being dispatched — set by
    # `dispatch` so handlers taking free text keep the user's exact spacing.
    raw_args: str = ""
    # Set by the in-stream Ctrl+R watcher to request an immediate regenerate
    # after the current reply is cancelled; drained by run_inference.
    regen_after: bool = False
    # The full-screen surfaces the CLI wires in.
    tui: TUI = field(default_factory=TUI)
    # The background lore worker — the REPL schedules passes through it,
    # the manual close waits on it. [lore_extraction].enabled gates only
    # the idle scheduling; the worker itself always exists.
    worker: LoreWorker = field(kw_only=True)
    # The pinned bottom-row activity line, alive while a reply streams —
    # built by the REPL together with its toolbar twin.
    status_line: StatusLine | None = None

    @classmethod
    def start(
        cls,
        *,
        config: Config,
        paths: Paths,
        providers: ProviderRegistry,
        spec: str,
        state: state_file.AppState,
        store: Store,
        tui: TUI | None = None,
        worker: LoreWorker,
    ) -> Self:
        """The session for a launch on `spec` ("provider/model"): the
        model's saved parameters, the persisted toggles, and the remembered
        story, all applied. A value the files no longer make sense of is
        reported and skipped — a stale setting must never cost a launch."""
        provider_name, _, model = spec.partition("/")
        session = cls(
            config=config,
            prompts=prompts_file.load(paths),
            paths=paths,
            providers=providers,
            provider=config.providers[provider_name],
            model=model,
            verbose=state.verbose,
            tui=tui or TUI(),
            worker=worker,
        )
        session._apply_think(state.think)
        session._apply_params(models_file.load(paths).get(model, {}))
        # Reattach the story the previous session was on, so bare `otaku`
        # reopens it mid-scene; one deleted since simply starts fresh.
        if state.story and store.stories.exists(state.story):
            session.switch_to(store, state.story)
        return session

    @property
    def full_model_name(self) -> str:
        """ "<provider>/<model>" for display — derived, so a model switch can
        never leave it stale."""
        return f"{self.provider.name}/{self.model}"

    # The assembler's StoryView: the shaping settings, read off the session
    # so every assemble_story call sees the same values.

    @property
    def recap_header(self) -> str:
        return self.prompts.recap_header

    @property
    def head_messages(self) -> int:
        return self.config.head_messages

    @property
    def tail_messages(self) -> int:
        return self.config.tail_messages

    def story_label(self, store: Store) -> str:
        """The loaded story's display label: its title, else the newest
        story-so-far rollup, else the first user message — the same fallback
        order the story browser uses. "" when nothing exists to show."""
        if self.story_id is None:
            return ""
        story = store.stories.get(self.story_id)
        if story is None:
            return ""
        if story.title:
            return story.title
        story_so_far = store.scenes.get_story_so_far(self.story_id, [m.id for m in self.messages])
        if story_so_far:
            return story_so_far
        return next((m.body for m in self.messages if m.role == "user"), "")

    def switch_to(self, store: Store, story_id: int, messages: list[Message] | None = None) -> None:
        """Attach the session to a story — its system prompt, its messages
        (or the given, already-truncated list), and the remembered state.
        The ONE way a session changes stories, so none of the doors (launch
        resume, the browser, an import) can forget a piece."""
        self.story_id = story_id
        self.system = store.stories.get_system(story_id)
        self.messages = store.stories.get_messages(story_id) if messages is None else messages
        self.save_state()

    def ensure_story(self, store: Store) -> int:
        """The session's story id, creating the story on the first real turn
        — so `/set` or an immediate exit never leaves an empty row behind. A
        story deleted out from under the session is detected here too:
        without this, later writes would fail while the session only looked
        recorded."""
        if self.story_id is not None and not store.stories.exists(self.story_id):
            self.story_id = None
            self.messages = []
            print(f"{DIM}[ the story was deleted — continuing in a new one ]{RESET}")
        if self.story_id is None:
            self.story_id = store.stories.add()
            if self.system:
                store.stories.set_system(self.story_id, self.system)
            self.save_state()
        return self.story_id

    def record_turn(self, store: Store, message: Message) -> None:
        """Append one turn to the session and the store, keeping the
        in-memory copy carrying its assigned id."""
        story_id = self.ensure_story(store)
        message_id = store.stories.append(story_id, message)
        self.messages.append(replace(message, id=message_id))

    def undo(self, store: Store) -> list[Message]:
        """Discard the trailing exchange: the assistant reply (if any) plus
        every trailing user row back to the previous assistant turn — one
        submission can be several rows (a /me or /you direction beside its
        line). Nothing is deleted: the head moves back and the undone turns
        stay in the tree as siblings. Returns the popped messages."""
        popped: list[Message] = []
        if not self.messages:
            return popped
        if self.messages[-1].role == "assistant":
            popped.append(self.messages.pop())
        while self.messages and self.messages[-1].role == "user":
            popped.append(self.messages.pop())
        if popped:
            self._move_head(store)
        return popped

    def drop_last_reply(self, store: Store) -> Message | None:
        """Pop the trailing assistant reply (regenerate's first half): the
        head moves back one; the discarded reply stays in the tree as a
        sibling."""
        if not self.messages or self.messages[-1].role != "assistant":
            return None
        popped = self.messages.pop()
        self._move_head(store)
        return popped

    def set_system(self, store: Store, text: str) -> None:
        """The story's system prompt — persisted with the story when one
        exists; a story created later picks it up at creation."""
        self.system = text
        if self.story_id is not None:
            store.stories.set_system(self.story_id, text)

    def save_state(self) -> None:
        """Persist what bare `otaku` resumes: the model, the story, and the
        /set toggles. Best-effort — remembered state is never worth failing
        a turn."""
        with contextlib.suppress(OSError):
            state_file.save(
                self.paths,
                state_file.AppState(
                    model=self.full_model_name,
                    story=self.story_id or 0,
                    verbose=self.verbose,
                    think=self.think if self.think is not None else "default",
                ),
            )

    def render_last_turns(self, count: int, *, dim: str = DIM, reset: str = RESET) -> str:
        """The last `count` turns, echoed the way they played: a dim `[role]`
        marker with one blank line before it, and the content — its own
        blank lines dropped — with its markdown rendered, as it looked
        streaming."""
        out: list[str] = []
        for message in self.messages[-count:]:
            out.append("")
            out.append(f"{dim}[{message.role}]{reset}")
            body = "\n".join(line for line in message.body.splitlines() if line.strip())
            out.extend(render_markdown(body).splitlines())
        return "\n".join(out).lstrip("\n")

    # ---------- startup internals ----------

    def _apply_think(self, think: str) -> None:
        if think == "default":
            self.think = None
        elif think in THINK_LEVELS:
            self.think = think
        else:
            self.think = "none"

    def _apply_params(self, saved: dict[str, object]) -> None:
        for name, value in saved.items():
            coerce = KNOWN_PARAMS.get(name)
            if coerce is None:
                print(f"Ignoring unknown parameter {name!r} saved for {self.model}.")
                continue
            try:
                self.params[name] = coerce(value)
            except TypeError, ValueError:
                print(f"Ignoring invalid {name} value {value!r} saved for {self.model}.")

    def _move_head(self, store: Store) -> None:
        """Point the story at the session's last message (None when empty)."""
        if self.story_id is None:
            return
        head = self.messages[-1].id if self.messages else None
        store.stories.set_head(self.story_id, head)
