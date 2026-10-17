"""The installation's reports, typed, carried out — the twin of
`api.app.reports`: the tokens spent (`/usage [all]`), what otaku knows
of this run (`/info`) and what each cloud account has left (`/balance`)
— each the report's `text()`. A story's next request is the story's
(`dispatch.story.context`)."""

from collections.abc import Callable

from otaku.terminal.api.app import reports as api_reports
from otaku.terminal.chat.chat import Chat

# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    "/usage": lambda chat, raw: api_reports.usage(chat.api, chat.story_id, raw).text(),
    "/info": lambda chat, raw: chat.api.info.get(story_id=chat.story_id).text(),
    "/balance": lambda chat, raw: chat.api.balance.get().text(),
}
