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

from otaku.paths import Paths
from otaku.providers.registry import Registry as ProviderRegistry
from otaku.settings import models as models_file
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_file
from otaku.settings.config import Config, Provider
from otaku.settings.prompts import Prompts
from otaku.store import Store
from otaku.store.schema import Message

DIM = "\x1b[2m"
RESET = "\x1b[0m"

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
    # Opens the model picker and returns the chosen "provider/model" (None
    # on cancel). Injected by the CLI: the picker is a UI package, which
    # chat may not import.
    pick_model: Callable[[str], str | None] | None = None

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
        pick_model: Callable[[str], str | None] | None = None,
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
            pick_model=pick_model,
        )
        session._apply_think(state.think)
        session._apply_params(models_file.load(paths).get(model, {}))
        session._resume_story(store, state.story)
        return session

    @property
    def full_model_name(self) -> str:
        """ "<provider>/<model>" for display — derived, so a model switch can
        never leave it stale."""
        return f"{self.provider.name}/{self.model}"

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
        """The last `count` turns as `/context` shows them: a dim `[role]`
        marker with one blank line before it, and the content with its own
        blank lines dropped."""
        out: list[str] = []
        for message in self.messages[-count:]:
            out.append("")
            out.append(f"{dim}[{message.role}]{reset}")
            out.extend(line for line in message.body.splitlines() if line.strip())
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

    def _resume_story(self, store: Store, story_id: int) -> None:
        """Reattach the story the previous session was on, so bare `otaku`
        reopens it mid-scene. A story deleted since simply doesn't reattach
        and the session starts fresh."""
        if not story_id:
            return
        story = store.stories.get(story_id)
        if story is None:
            return
        self.story_id = story_id
        self.system = story.system
        self.messages = store.stories.get_messages(story_id)

    def _move_head(self, store: Store) -> None:
        """Point the story at the session's last message (None when empty)."""
        if self.story_id is None:
            return
        head = self.messages[-1].id if self.messages else None
        store.stories.set_head(self.story_id, head)
