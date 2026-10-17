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
class ModelRef:
    """A model as state.toml names it: the provider section it is served
    from, and its bare name as that provider lists it."""

    provider: str
    name: str

    @property
    def spec(self) -> str:
        """ "provider/name", as the file and a typed `/model` spell it."""
        return f"{self.provider}/{self.name}"

    @classmethod
    def parse(cls, spec: str) -> "ModelRef | None":
        """A spec split at its FIRST slash — a model name may carry more;
        None for "" and for a half-written spec ("ollama/")."""
        provider, _, name = spec.partition("/")
        return cls(provider, name) if provider and name else None


@dataclass(frozen=True)
class StateConfig:
    """What state.toml holds: the settings otaku writes itself as they
    change — the model to resume and the one saved for lore extraction
    (specs as the file spells them), the story to reattach, the /set
    switches. A config, not the app's state: the app reads it from the
    file whenever it changed."""

    model: str = ""  # "provider/model" to resume; "" = open the picker
    extraction_model: str = ""  # "provider/model" lore extraction uses; "" = `model`
    story: int = 0  # story id to reattach; 0 = start detached
    verbose: bool = False
    autocorrect: bool = True
    notification: bool = False

    @property
    def model_ref(self) -> ModelRef | None:
        return ModelRef.parse(self.model)

    @property
    def extraction_ref(self) -> ModelRef | None:
        return ModelRef.parse(self.extraction_model)


def load(path: Path) -> tuple[StateConfig, list[str]]:
    """Missing file → defaults; a malformed one falls back with a
    returned warning — remembered state is never worth failing a launch,
    and this module never prints."""
    if not path.exists():
        return StateConfig(), []
    try:
        raw = tomllib.loads(read_settings(path))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return StateConfig(), [f"Ignoring {path.name} ({e})."]
    story = raw.get("story")
    return StateConfig(
        model=str(raw.get("model", "")),
        extraction_model=str(raw.get("extraction_model", "")),
        story=story if isinstance(story, int) and story > 0 else 0,
        verbose=bool(raw.get("verbose", False)),
        autocorrect=bool(raw.get("autocorrect", True)),
        notification=bool(raw.get("notification", False)),
    ), []


def save(path: Path, state: StateConfig) -> None:
    body = "\n".join(
        [
            "# Written by otaku — what it remembers between sessions.",
            row(f"model = {toml_scalar(state.model)}", "bare `otaku` resumes this model"),
            row(
                f"extraction_model = {toml_scalar(state.extraction_model)}",
                'lore extraction uses this model; "" = the one above',
            ),
            row(f"story = {state.story}", "and reattaches this story (0 = none)"),
            row(f"verbose = {str(state.verbose).lower()}", "/set verbose"),
            row(f"autocorrect = {str(state.autocorrect).lower()}", "/set autocorrect"),
            row(f"notification = {str(state.notification).lower()}", "/set notification"),
        ]
    )
    write_atomic(path, body + "\n")
