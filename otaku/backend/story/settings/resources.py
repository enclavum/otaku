"""A story's settings as a frontend reaches them (`StorySettingsResource`):
each switch and value, by the name a story stores it under, read as it
stands — what the story stored laid over the defaults — and changed one
field at a time. The work is the domain's (`story.settings.services`)."""

from __future__ import annotations

from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged
from otaku.backend.story.settings import services as domain_settings
from otaku.backend.story.settings.schemas import StorySetting
from otaku.backend.story.settings.services import InjectingSetting
from otaku.backend.story.settings.services import StorySetting as DomainStorySetting
from otaku.store.schema import InjectionPosition, Layout


class StorySettingsResource(Resource):
    """/stories/{story}/settings — a story's settings, each by its name."""

    def list(self, story_id: int) -> list[StorySetting]:
        """Every setting of a story as it stands — what it stored laid
        over the defaults — in the order they are sent where they share a
        place. Raises NotFound for a story that is not there."""
        return [_setting(setting) for setting in domain_settings.list_settings(self._app, story_id)]

    def get(self, story_id: int, setting: str) -> StorySetting:
        """One setting of a story as it stands. Raises NotFound for a
        story or a setting that is not there."""
        return _setting(domain_settings.get_setting(self._app, story_id, setting))

    def update(
        self,
        story_id: int,
        setting: str,
        *,
        enabled: bool | Unchanged = UNCHANGED,
        position: InjectionPosition | Unchanged = UNCHANGED,
        reminder_text: str | Unchanged = UNCHANGED,
        display_notes: bool | Unchanged = UNCHANGED,
        layout: Layout | Unchanged = UNCHANGED,
    ) -> Changed[StorySetting]:
        """Change one setting of a story; what is not given stays. The
        notice is the setting's own confirmation. Raises NotFound for a
        story or a setting that is not there, Invalid for what the
        setting does not take."""
        said = domain_settings.update_setting(
            self._app,
            story_id,
            setting,
            enabled=None if isinstance(enabled, Unchanged) else enabled,
            position=None if isinstance(position, Unchanged) else position,
            reminder_text=None if isinstance(reminder_text, Unchanged) else reminder_text,
            display_notes=None if isinstance(display_notes, Unchanged) else display_notes,
            layout=None if isinstance(layout, Unchanged) else layout,
        )
        return Changed(self.get(story_id, setting), said)


def _setting(setting: DomainStorySetting) -> StorySetting:
    """A setting as a frontend draws it."""
    injecting = setting if isinstance(setting, InjectingSetting) else None
    return StorySetting(
        name=setting.name,
        label=setting.label,
        tool=setting.tool.name if setting.tool is not None else None,
        enabled=setting.enabled,
        injection_label=injecting.injection_label if injecting is not None else None,
        prompt_name=injecting.prompt_name if injecting is not None else None,
        injection_position=setting.injection_position,
        allowed_positions=setting.allowed_positions,
        reminder_text=setting.reminder_text,
        display_notes=setting.display_notes,
        layout=setting.layout,
    )
