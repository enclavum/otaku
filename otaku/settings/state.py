"""What the app remembers between sessions: state.toml — rewritten
wholesale on every change; there are no user edits to preserve. A key
an older file still carries and this one no longer does (the thinking
level, the model's own since 0.5.0; the model played with and the
extraction model, each a role's in `[models]` since 0.10.0 —
`migrations.state_file` moves them) is read past."""

import enum
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
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


class ModelRole(enum.Enum):
    """What a model is saved for, each under its own key in `[models]`:
    writing the story — the model in use, played with and resumed; lore
    extraction, which asks the writing one while none is saved for it;
    and dictation, which has none then."""

    WRITING = "writing"
    EXTRACTION = "extraction"
    DICTATION = "dictation"


@dataclass(frozen=True)
class StateConfig:
    """What state.toml holds: the settings otaku writes itself as they
    change — the model saved for each role (specs as the file spells
    them, a role without one absent; the writing one resumed, the picker
    opening without it), the story to reattach, the /set switches, and
    what the update check last learned. A config, not the app's state:
    the app reads it from the file whenever it changed."""

    models: Mapping[ModelRole, str] = field(default_factory=dict)  # "provider/model" by role
    story: int = 0  # story id to reattach; 0 = start detached
    verbose: bool = False
    autocorrect: bool = True
    notification: bool = False
    update_checked_at: str = ""  # when the update check last asked, ISO 8601 in UTC; "" = never
    update_latest_version: str = (
        ""  # the version announced as current when it last answered; "" = unknown
    )

    def model(self, role: ModelRole) -> ModelRef | None:
        """The model saved for `role`; None for none."""
        return ModelRef.parse(self.models.get(role, ""))


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
    pinned = raw.get("models")
    pinned = pinned if isinstance(pinned, dict) else {}
    return StateConfig(
        models={role: str(pinned[role.value]) for role in ModelRole if pinned.get(role.value)},
        story=story if isinstance(story, int) and story > 0 else 0,
        verbose=bool(raw.get("verbose", False)),
        autocorrect=bool(raw.get("autocorrect", True)),
        notification=bool(raw.get("notification", False)),
        update_checked_at=str(raw.get("update_checked_at", "")),
        update_latest_version=str(raw.get("update_latest_version", "")),
    ), []


def save(path: Path, state: StateConfig) -> None:
    """state.toml written whole. An extraction model that is the writing
    one is not saved apart: extraction asks the writing one anyway."""
    writing = state.models.get(ModelRole.WRITING, "")
    extraction = state.models.get(ModelRole.EXTRACTION, "")
    # fmt: off
    body = "\n".join(
        [
            "# Written by otaku — what it remembers between sessions.",
            row(f"story = {state.story}", "bare `otaku` reattaches this story (0 = none)"),
            row(f"verbose = {str(state.verbose).lower()}", "/set verbose"),
            row(f"autocorrect = {str(state.autocorrect).lower()}", "/set autocorrect"),
            row(f"notification = {str(state.notification).lower()}", "/set notification"),
            row(f"update_checked_at = {toml_scalar(state.update_checked_at)}", "when otaku last checked for a newer version (UTC)"),
            row(f"update_latest_version = {toml_scalar(state.update_latest_version)}", "the latest release that check found"),
            "",
            "[models]",
            row(f"writing = {toml_scalar(writing)}", 'bare `otaku` resumes this model; "" = open the picker'),
            row(f"extraction = {toml_scalar('' if extraction == writing else extraction)}", 'lore extraction uses this model; "" = the writing one'),
            row(f"dictation = {toml_scalar(state.models.get(ModelRole.DICTATION, ''))}", 'dictation uses this model; "" = none'),
        ]
    )
    # fmt: on
    write_atomic(path, body + "\n")
