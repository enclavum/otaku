"""What the app remembers between sessions: configs/state.toml.

The app-owned counterpart of config.toml: config.toml holds what you edit in
a file, this holds what commands change — the model bare `otaku` resumes, the
story it reattaches, /set verbose, /set think. Rewritten wholesale on every
change; there are no user edits to preserve.
"""

import sys
import tomllib
from dataclasses import dataclass

from otaku.paths import Paths
from otaku.settings.files import row, toml_scalar, write_atomic


@dataclass(frozen=True)
class AppState:
    model: str = ""  # "provider/model" to resume; "" = open the picker
    story: int = 0  # story id to reattach; 0 = start detached
    verbose: bool = False  # show the stats line after each reply
    think: str = "none"  # thinking effort sent to the model


def load(paths: Paths) -> AppState:
    """Read state.toml. Missing file → defaults; a malformed one warns and
    falls back — remembered state is a convenience, never worth failing a
    launch over."""
    path = paths.state_file
    if not path.exists():
        return AppState()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"otaku: ignoring {path} ({e})", file=sys.stderr)
        return AppState()
    story = raw.get("story")
    return AppState(
        model=str(raw.get("model", "")),
        story=story if isinstance(story, int) and story > 0 else 0,
        verbose=bool(raw.get("verbose", False)),
        think=str(raw.get("think", "none")),
    )


def save(paths: Paths, state: AppState) -> None:
    body = "\n".join(
        [
            "# Written by otaku — what it remembers between sessions.",
            row(f"model = {toml_scalar(state.model)}", "bare `otaku` resumes this model"),
            row(f"story = {state.story}", "and reattaches this story (0 = none)"),
            row(f"verbose = {str(state.verbose).lower()}", "/set verbose"),
            row(f"think = {toml_scalar(state.think)}", "/set think"),
        ]
    )
    write_atomic(paths.state_file, body + "\n")
