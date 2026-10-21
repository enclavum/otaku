"""The texts every story's settings inject, shared by every story: the
shared reminder, the reader's own words, kept sealed in the settings
table (`get_shared_reminder`, `update_shared_reminder`); and the prompts
an injection setting sends, as prompts.toml holds them, each by its key
there: a tool's — what tells the model how to use it,
the one its switch sends (`of_tool`) — and the rest, which no tool owns,
the text-completion emulation's (`named`). Read as an assembly reads
them (`story.settings.services`), so a hand edit shows too; written that
key alone, the pre-edit file backed up (`settings.prompts.set_prompt`),
and read back — what the next assembly injects is what was saved, or the
write is refused. An emptied text drops the key, so the shipped text
stands again."""

from otaku.backend.app.core.services import App
from otaku.backend.errors import NotFound, Unavailable
from otaku.backend.story.settings.services import InjectingSetting, StorySettings, ToolSetting
from otaku.settings import prompts as prompts_file


def of_tool(app: App, tool: str) -> str:
    """The key of the prompt that tells the model how to use `tool`, by
    the tool's name — the one its switch sends. Raises NotFound for a
    name no tool answers to."""
    keys = {
        setting.tool.name: setting.prompt_name
        for setting in _defaults(app)
        if isinstance(setting, ToolSetting)
    }
    key = keys.get(tool)
    if key is None:
        raise NotFound(f"Unknown tool {tool!r}. Tools: {', '.join(keys)}.")
    return key


def named(app: App, name: str) -> str:
    """`name`, checked as the key of a prompt no tool owns. Raises
    NotFound for any other name — a tool's prompt is reached by its tool
    (`of_tool`)."""
    keys = [
        setting.prompt_name
        for setting in _defaults(app)
        if isinstance(setting, InjectingSetting)
        and not isinstance(setting, ToolSetting)
        and setting.prompt_name is not None
    ]
    if name not in keys:
        raise NotFound(f"Unknown prompt {name!r}. Prompts: {', '.join(keys)}.")
    return name


def get_prompt(app: App, key: str) -> str:
    """The prompt kept under `key`, as the file holds it now."""
    return str(getattr(app._prompts(), key))


def update_prompt(app: App, key: str, text: str) -> tuple[str, str]:
    """The prompt written and read back — answered as it now stands,
    with the sentence. An empty text is the restore."""
    text = text.strip()
    if not text:
        return delete_prompt(app, key)
    _write(app, key, text)
    return text, "Prompt saved."


def delete_prompt(app: App, key: str) -> tuple[str, str]:
    """The prompt dropped from the file, so the built-in stands again —
    answered as it now stands, with the sentence."""
    _write(app, key, "")
    return str(getattr(prompts_file.Prompts(), key)), "Prompt restored to the built-in."


def get_shared_reminder(app: App) -> str:
    """The shared reminder's text — one text, which every story that
    switched `use_shared_reminder` on is sent; kept in the settings
    table, sealed with the rest of the reader's words."""
    return app._store.settings.get_shared_reminder()


def update_shared_reminder(app: App, text: str) -> str:
    """The shared reminder's text, and the sentence; "" clears it."""
    if not text.strip():
        return _clear_shared_reminder(app)
    app._store.settings.set_shared_reminder(text.strip())
    return "Shared reminder saved."


def _clear_shared_reminder(app: App) -> str:
    """The shared reminder cleared — a story that switched it on is sent
    nothing for it — and the sentence."""
    app._store.settings.set_shared_reminder("")
    return "Shared reminder cleared."


def _write(app: App, key: str, text: str) -> None:
    paths = app._paths
    prompts_file.set_prompt(paths.prompts_file, paths.config_backups_dir, key, text)
    loaded, _ = prompts_file.load(paths.prompts_file)
    if str(getattr(loaded, key)) != (text or str(getattr(prompts_file.Prompts(), key))):
        raise Unavailable("Prompt not saved — prompts.toml could not be edited.")


def _defaults(app: App) -> StorySettings:
    """The settings a story starts with: any story's name the same
    prompts, so the defaults are asked."""
    return StorySettings((), app._store, app._paths.prompts_file, can_complete_text=False)
