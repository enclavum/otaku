"""The moves over state.toml. The thinking level, once one for every
model, is the model's own since 0.5.0 and lives in models.toml — so a
level state.toml still carries goes to the remembered model's entry,
and the key is dropped. The model played with and the extraction model
are roles' since 0.10.0: `model` and `extraction_model` move to
`[models]`'s `writing` and `extraction`. Both rewrite the file whole
(`_rewritten`), so each rewrite carries the other's keys along: the
level's folds the models in, and the models' waits while a level is
still to move. Convergent like the rest of the package: each applicable
while state.toml carries its old key, and rerun at every launch until it
does not."""

import contextlib
from dataclasses import replace
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
    as spelled, and the app reads it back against the wire's ladder
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
    # the model remembered: the writing role's where one is set — it
    # stands over the old key — else the old key's
    models = raw.get("models")
    writing = models.get("writing", "") if isinstance(models, dict) else ""
    model = str(writing or raw.get("model", "")).partition("/")[2]
    if model and level != models_file.THINK_UNSET:
        entry = models_file.load(models_path).get(model, {})
        if models_file.THINK_KEY not in entry:
            try:
                models_file.save(models_path, model, {**entry, models_file.THINK_KEY: level})
            except (OSError, ValueError):
                return
    _rewritten(state_path, raw)


# The keys 0.10.0 moved into `[models]`, by the role each became.
_MOVED_PINS = {
    "model": state_file.ModelRole.WRITING,
    "extraction_model": state_file.ModelRole.EXTRACTION,
}


def move_model_pins(state_path: Path) -> None:
    """`model` and `extraction_model` land as `[models]`'s `writing` and
    `extraction` — unless a role is already set (a hand edit), which
    stands — and the old keys go with the rewrite. A file still holding
    a level is left to `move_think`, whose rewrite moves them too: this
    one's would drop the level before it landed."""
    if not state_path.exists():
        return
    try:
        raw = parse(read_settings(state_path))
    except (OSError, UnicodeDecodeError):
        return
    if raw is None or models_file.THINK_KEY in raw:
        return
    if any(key in raw for key in _MOVED_PINS):
        _rewritten(state_path, raw)


def _rewritten(state_path: Path, raw: dict[str, object]) -> None:
    """state.toml written whole as it reads now, the old keys `raw` holds
    for a model moved into `[models]` first — a role already set there
    standing — and every other old key dropped."""
    with contextlib.suppress(OSError):
        state, _ = state_file.load(state_path)
        models = dict(state.models)
        for key, role in _MOVED_PINS.items():
            spec = str(raw.get(key, ""))
            if spec and role not in models:
                models[role] = spec
        state_file.save(state_path, replace(state, models=models))
