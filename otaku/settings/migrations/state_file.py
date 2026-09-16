"""The move over state.toml that reaches another file: the thinking
level, once one for every model, is the model's own since 0.5.0 and
lives in models.toml — so a level state.toml still carries goes to the
remembered model's entry, and the key is dropped. Convergent like the
rest of the package: applicable while state.toml carries `think`, and
rerun at every launch until it does not."""

import contextlib
from pathlib import Path

from otaku.settings import models as models_file
from otaku.settings import read_settings
from otaku.settings import state as state_file
from otaku.settings.migrations.surgery import parse

# How releases before 0.5.0 spelled "send nothing" in state.toml.
_RETIRED = {"default": models_file.THINK_UNSET}


def move_think(state_path: Path, models_path: Path) -> None:
    """The level lands in the remembered model's entry — "provider/model"
    in state.toml, the bare name in models.toml — or nowhere when no
    model is remembered; an entry that already has one keeps it (a
    launch that wrote models.toml and could not rewrite state.toml).
    The old spelling of unset ("default") is read as unset, and unset
    is the ABSENCE of a row, so it is not written; any other word moves
    as spelled, and the session reads it back against the wire's ladder
    as it reads every saved level. The key leaves state.toml only once
    the level has landed, so a write that failed is retried."""
    if not state_path.exists():
        return
    try:
        raw = parse(read_settings(state_path))
    except (OSError, UnicodeDecodeError):
        return
    if raw is None or models_file.THINK_KEY not in raw:
        return
    spelled = str(raw[models_file.THINK_KEY])
    level = _RETIRED.get(spelled, spelled)
    model = str(raw.get("model", "")).partition("/")[2]
    if model and level != models_file.THINK_UNSET:
        entry = models_file.load(models_path).get(model, {})
        if models_file.THINK_KEY not in entry:
            try:
                models_file.save(models_path, model, {**entry, models_file.THINK_KEY: level})
            except (OSError, ValueError):
                return
    with contextlib.suppress(OSError):
        state, _ = state_file.load(state_path)
        state_file.save(state_path, state)
