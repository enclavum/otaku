"""The installation's settings as a frontend reads and writes them: its
own (`SettingsResource`: the story last landed in, the /set
switches, the context limit) and each frontend's slice of config.toml. A write answers with what now
stands and the sentence that says so, and raises Refused for what it
declines; one that cannot be saved is refused, nothing changed. The
model in use and the one lore extraction asks are the app's
(`api.app`), the shared reminder the prompts' (`api.story.prompts`); where
each value persists, and how, is the domain's
(`app.settings.services`)."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.app.settings import services as domain_settings
from otaku.backend.app.settings.schemas import Settings, WebFrontendSettings
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged
from otaku.backend.errors import Invalid
from otaku.settings.config import TerminalSettings


class SettingsResource(Resource):
    """/settings — the installation's own settings."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.terminal = TerminalSettingsResource(app)
        self.web = WebSettingsResource(app)

    def get(self) -> Settings:
        """The settings as their files stand now — a story deleted since
        reads as none."""
        held = domain_settings.get_settings(self._app)
        return Settings(
            last_story=held.last_story,
            verbose=held.verbose,
            autocorrect=held.autocorrect,
            notification=held.notification,
            context_limit=held.context_limit,
            context_limit_text=domain_settings.context_limit_text(held.context_limit),
            idle_seconds=held.idle_seconds,
        )

    def update(
        self,
        *,
        last_story: int | Unchanged | None = UNCHANGED,
        verbose: bool | Unchanged = UNCHANGED,
        autocorrect: bool | Unchanged = UNCHANGED,
        notification: bool | Unchanged = UNCHANGED,
        context_limit: int | Unchanged = UNCHANGED,
    ) -> Changed[Settings]:
        """The fields given, each written where it persists, in this
        order — a refusal stops there, the fields before it written — and
        what each write says. `last_story` is the story a frontend landed
        in, the one a launch resumes, None none (best effort);
        `context_limit` 0 is none: the model's own window. Raises Invalid
        with nothing to change, and whatever each write refuses."""
        app = self._app
        said: list[str] = []
        if not isinstance(last_story, Unchanged):
            said.append(domain_settings.set_last_story(app, last_story))
        if not isinstance(verbose, Unchanged):
            said.append(domain_settings.set_verbose(app, verbose))
        if not isinstance(autocorrect, Unchanged):
            said.append(domain_settings.set_autocorrect(app, autocorrect))
        if not isinstance(notification, Unchanged):
            said.append(domain_settings.set_notification(app, notification))
        if not isinstance(context_limit, Unchanged):
            said.append(domain_settings.set_context_limit(app, context_limit))
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(), " ".join(said))

    def check_password(self, password: str) -> None:
        """Check `password` against the web sign-in's, whose hash
        config.toml holds — slow on purpose. Safe from any thread: it
        reads the config alone, so the web asks it off the app's thread.
        Raises Conflict where no password is set, Invalid where it is not
        the one."""
        domain_settings.check_password(self._app, password)


class TerminalSettingsResource(Resource):
    """/settings/terminal — the terminal frontend's slice of config.toml."""

    def get(self) -> TerminalSettings:
        """Its looks."""
        return domain_settings.get_terminal_settings(self._app)


class WebSettingsResource(Resource):
    """/settings/web — the web frontend's slice of config.toml."""

    def get(self) -> WebFrontendSettings:
        """Where it listens, the sign-in password's hash, and the two
        directories it serves from."""
        config, custom_dir, cert_dir = domain_settings.get_web_settings(self._app)
        return WebFrontendSettings(config=config, custom_dir=custom_dir, cert_dir=cert_dir)
