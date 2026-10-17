"""A story's settings, as the page reads and moves them: each switch and
value by the name the story stores it under, the fields given laid over
it."""

from typing import Any

from otaku.backend.api import UNCHANGED, Api, InjectionPosition, Layout, Unchanged
from otaku.web.api._request import Ask, Route, changed, plain
from otaku.web.api.story.stories import tagged


def _update_setting(api: Api, ask: Ask) -> Any:
    """One setting of a story, the fields given laid over it; what each
    may take is the setting's to refuse. A field of the wrong type is a
    malformed request: a flag that is no boolean, a position that is
    neither "system" nor a count from 1, a layout that is none of the
    four."""
    flags: dict[str, bool | Unchanged] = {}
    for name in ("enabled", "display_notes"):
        flags[name] = ask.flag(name) if name in ask.body else UNCHANGED
    reminder_text = ask.need("reminder_text") if "reminder_text" in ask.body else UNCHANGED
    position: InjectionPosition | Unchanged = UNCHANGED
    if "position" in ask.body:
        read = InjectionPosition.from_value(ask.body["position"])
        if read is None:
            raise TypeError('position is not "system" or a count from 1')
        position = read
    layout: Layout | Unchanged = UNCHANGED
    if "layout" in ask.body:
        named = Layout.from_value(ask.body["layout"])
        if named is None:
            raise TypeError("layout is not one of novel, canvas, manuscript, chat")
        layout = named
    return changed(
        api.stories.settings.update(
            ask.id("story"),
            ask.params["setting"],
            enabled=flags["enabled"],
            position=position,
            reminder_text=reminder_text,
            display_notes=flags["display_notes"],
            layout=layout,
        )
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/stories/{story}/settings"): tagged(
        lambda api, ask: {"items": plain(api.stories.settings.list(ask.id("story")))}
    ),
    ("GET", "/api/stories/{story}/settings/{setting}"): tagged(
        lambda api, ask: plain(api.stories.settings.get(ask.id("story"), ask.params["setting"]))
    ),
    ("PATCH", "/api/stories/{story}/settings/{setting}"): tagged(_update_setting),
}
