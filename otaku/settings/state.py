"""What the app remembers between sessions: state.toml — rewritten
wholesale on every change; there are no user edits to preserve. A key
an older file still carries and this one no longer does (the thinking
level, the model's own since 0.5.0 — `migrations.state_file` moves it)
is read past."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from otaku.formatting import toml_scalar
from otaku.settings import read_settings, row, write_atomic


@dataclass(frozen=True)
class State:
    model: str = ""  # "provider/model" to resume; "" = open the picker
    story: int = 0  # story id to reattach; 0 = start detached
    verbose: bool = False
    autocorrect: bool = True
    notification: bool = False

    @property
    def provider(self) -> str:
        """The provider half of `model` — split at the FIRST slash, since
        a model name may carry more of them."""
        return self.model.partition("/")[0]

    @property
    def bare_model(self) -> str:
        """The model half, as a server expects it; "" when nothing is
        remembered, and "" for a half-written spec ("ollama/")."""
        return self.model.partition("/")[2]


def load(path: Path) -> tuple[State, list[str]]:
    """Missing file → defaults; a malformed one falls back with a
    returned warning — remembered state is never worth failing a launch,
    and this module never prints."""
    if not path.exists():
        return State(), []
    try:
        raw = tomllib.loads(read_settings(path))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return State(), [f"Ignoring {path.name} ({e})."]
    story = raw.get("story")
    return State(
        model=str(raw.get("model", "")),
        story=story if isinstance(story, int) and story > 0 else 0,
        verbose=bool(raw.get("verbose", False)),
        autocorrect=bool(raw.get("autocorrect", True)),
        notification=bool(raw.get("notification", False)),
    ), []


def save(path: Path, state: State) -> None:
    body = "\n".join(
        [
            "# Written by otaku — what it remembers between sessions.",
            row(f"model = {toml_scalar(state.model)}", "bare `otaku` resumes this model"),
            row(f"story = {state.story}", "and reattaches this story (0 = none)"),
            row(f"verbose = {str(state.verbose).lower()}", "/set verbose"),
            row(f"autocorrect = {str(state.autocorrect).lower()}", "/set autocorrect"),
            row(f"notification = {str(state.notification).lower()}", "/set notification"),
        ]
    )
    write_atomic(path, body + "\n")
