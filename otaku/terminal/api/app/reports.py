"""The installation's reports as the terminal reaches them — the twin of
`backend.api.app.reports`: everything it answers, re-exported, and what
`/usage [all]` means — the story's tokens, or every story's."""

from otaku.backend.api import Api, Conflict, Invalid
from otaku.backend.api.app.reports import USAGE_PURPOSES as USAGE_PURPOSES
from otaku.backend.api.app.reports import USAGE_SCOPES as USAGE_SCOPES
from otaku.backend.api.app.reports import Balance as Balance
from otaku.backend.api.app.reports import BalanceReport as BalanceReport
from otaku.backend.api.app.reports import InfoReport as InfoReport
from otaku.backend.api.app.reports import InfoSection as InfoSection
from otaku.backend.api.app.reports import UsageReport as UsageReport
from otaku.backend.api.app.reports import UsageRow as UsageRow

# What `/usage` says with no story to count.
_NO_STORY_FOR_USAGE = "No story yet — send a message first, or use /usage all."


def usage(api: Api, story_id: int | None, raw: str) -> UsageReport:
    """`/usage [all]`: the tokens spent on the story, or with "all" on
    every story — the one argument the command knows."""
    argument = raw.strip().lower()
    if argument not in ("", "all"):
        raise Invalid("Usage: /usage [all]")
    if argument == "all":
        return api.usage.get()
    if story_id is None:
        raise Conflict(_NO_STORY_FOR_USAGE)
    return api.usage.get(story_id=story_id)
