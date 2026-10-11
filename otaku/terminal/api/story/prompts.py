"""The prompts a story's settings send, as the terminal reaches them —
the twin of `backend.api.story.prompts`: everything it answers,
re-exported, and a setting's prompt read and written where it is kept —
its tool's, by the tool's name, or the one it names (`read_prompt`,
`write_prompt`)."""

from otaku.backend.api import Api
from otaku.backend.api.story.prompts import Prompt as Prompt
from otaku.backend.api.story.prompts import SharedReminder as SharedReminder
from otaku.terminal.api.story.settings import StorySetting


def read_prompt(api: Api, setting: StorySetting) -> str:
    """The prompt an injection setting sends: its tool's, by the tool's
    name, or — a setting that switches no tool on — the one it names."""
    if setting.tool is not None:
        return api.tools.prompt.get(setting.tool).text
    assert setting.prompt_name is not None
    return api.prompts.get(setting.prompt_name).text


def write_prompt(api: Api, setting: StorySetting, text: str) -> str:
    """The prompt an injection setting sends, written where
    `read_prompt` reads it — and the sentence."""
    if setting.tool is not None:
        return api.tools.prompt.update(setting.tool, text=text).notice
    assert setting.prompt_name is not None
    return api.prompts.update(setting.prompt_name, text=text).notice
