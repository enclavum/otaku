"""The texts a story's settings send, shared by every story, as a
frontend reads and edits them: each tool's prompt — what tells the model
how to use it — by the tool's name (`StorySetting.tool`, `question`,
`web-search`; `ToolsResource`); the prompts no tool owns — today the
text-completion emulation's — by their prompts.toml key
(`StorySetting.prompt_name`; `PromptsResource`); and the shared
reminder, the reader's own text, kept sealed in the settings table
(`SharedReminderResource`). A prompt is edited in place: what the next
assembly injects is what was saved, or the write is refused, and an
empty text restores the built-in. The work is the domain's
(`domain.story.prompts`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.api._writes import Changed
from otaku.backend.domain.app import App
from otaku.backend.domain.story import prompts as domain_prompts


class ToolsResource:
    """/tools — the tools a model may call: nothing of its own, only each
    tool's prompt."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.prompt = ToolPromptResource(app)


class ToolPromptResource:
    """/tools/{tool}/prompt — the prompt that tells the model how to use
    a tool."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, tool: str) -> Prompt:
        """The prompt as the file holds it now, a hand edit included.
        Raises NotFound for a name no tool answers to."""
        key = domain_prompts.of_tool(self._app, tool)
        return Prompt(key, domain_prompts.get_prompt(self._app, key))

    def update(self, tool: str, *, text: str) -> Changed[Prompt]:
        """The prompt written into prompts.toml and read back. An empty
        text restores the built-in."""
        key = domain_prompts.of_tool(self._app, tool)
        standing, said = domain_prompts.update_prompt(self._app, key, text)
        return Changed(Prompt(key, standing), said)

    def delete(self, tool: str) -> Changed[Prompt]:
        """The prompt dropped from prompts.toml, so the built-in stands
        again."""
        key = domain_prompts.of_tool(self._app, tool)
        standing, said = domain_prompts.delete_prompt(self._app, key)
        return Changed(Prompt(key, standing), said)


class PromptsResource:
    """/prompts/{prompt} — a prompt no tool owns, by its prompts.toml key
    (`StorySetting.prompt_name`)."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, prompt: str) -> Prompt:
        """The prompt as the file holds it now, a hand edit included.
        Raises NotFound for a key no setting sends, and for a tool's,
        which its tool answers."""
        key = domain_prompts.named(self._app, prompt)
        return Prompt(key, domain_prompts.get_prompt(self._app, key))

    def update(self, prompt: str, *, text: str) -> Changed[Prompt]:
        """The prompt written into prompts.toml and read back. An empty
        text restores the built-in."""
        key = domain_prompts.named(self._app, prompt)
        standing, said = domain_prompts.update_prompt(self._app, key, text)
        return Changed(Prompt(key, standing), said)

    def delete(self, prompt: str) -> Changed[Prompt]:
        """The prompt dropped from prompts.toml, so the built-in stands
        again."""
        key = domain_prompts.named(self._app, prompt)
        standing, said = domain_prompts.delete_prompt(self._app, key)
        return Changed(Prompt(key, standing), said)


class SharedReminderResource:
    """/shared-reminder — the one text every story that switched it on
    is sent."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self) -> SharedReminder:
        """The text as it stands; "" for none."""
        return SharedReminder(domain_prompts.get_shared_reminder(self._app))

    def update(self, *, text: str) -> Changed[SharedReminder]:
        """The text saved; "" clears it, and a story that switched it on
        is then sent nothing for it."""
        said = domain_prompts.update_shared_reminder(self._app, text)
        return Changed(self.get(), said)


@dataclass(frozen=True)
class Prompt:
    """A prompt otaku sends: its name — the prompts.toml key it is kept
    under — and its text as it stands."""

    name: str
    text: str


@dataclass(frozen=True)
class SharedReminder:
    """The shared reminder: its text, "" for none."""

    text: str
