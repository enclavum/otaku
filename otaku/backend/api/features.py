"""The story's features, operated: what each switch stands at, and the
changes a frontend may ask for.

The open story's — or the session's own until a first turn makes the
story, as the premise is held. Every switch is a story's, the global
reminder's included; only that reminder's TEXT is every story's (the
store's `globals`). What the switches MEAN for a request is
`backend.features`; the session resolves it at every assembly.
"""

from dataclasses import replace

from otaku.backend import features as story_features
from otaku.backend.features import (
    ALL_FEATURES,
    GLOBALS_REMINDER_KEY,
    USE_STORY_REMINDER,
    Feature,
)
from otaku.backend.session import Refused, Session
from otaku.context.assembler import Position


def get(session: Session) -> tuple[Feature, ...]:
    """Every feature as it stands in the open story: the two reminders,
    then the tools."""
    return story_features.read(session._settings)


def update(
    session: Session,
    name: str,
    *,
    on: bool | None = None,
    position: Position | None = None,
    text: str | None = None,
) -> str:
    """Change one feature of the open story; what is not given stays.
    Returns the confirmation. Raises Refused for a feature that does not
    exist, a position outside the feature's `allowed_positions` (a
    reminder has no "system"), and a text asked of anything but the story
    reminder — the global one's is `set_global_reminder`'s."""
    found = {feature.name: feature for feature in story_features.read(session._settings)}
    feature = found.get(name)
    if feature is None:
        raise Refused(f"Unknown feature {name!r}. Features: {', '.join(ALL_FEATURES)}.")
    # By type first: to Python True is 1 and -1.0 is -1, and neither is a position.
    if position is not None and (
        type(position) not in (int, str) or position not in feature.allowed_positions
    ):
        allowed = ", ".join(str(place) for place in feature.allowed_positions)
        raise Refused(f"{feature.label} takes one of these positions: {allowed}.")
    if text is not None and name != USE_STORY_REMINDER:
        raise Refused(f"{feature.label} has no text of its own in a story.")
    changed = replace(
        feature,
        on=feature.on if on is None else on,
        position=feature.position if position is None else position,
        text=feature.text if text is None else text,
    )
    session._set_settings(story_features.write(session._settings, changed))
    return f"{changed.label}: {_stands(changed)}."


def get_global_reminder(session: Session) -> str:
    """The global reminder's text — every story's; a story only switches
    it on."""
    return session._store.globals.get(GLOBALS_REMINDER_KEY)


def set_global_reminder(session: Session, text: str) -> str:
    """The global reminder's text, for every story; "" clears it."""
    session._store.globals.set(GLOBALS_REMINDER_KEY, text.strip())
    return "Global reminder saved." if text.strip() else "Global reminder cleared."


def _stands(feature: Feature) -> str:
    """Where a feature stands, in words."""
    if not feature.on:
        return "off"
    if feature.position == "system":
        return "on, in the system message"
    back = -feature.position
    return f"on, {back} message{'s' if back != 1 else ''} from the end"
