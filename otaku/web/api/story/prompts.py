"""The texts a story's settings send, shared by every story, as the page
reaches them: each tool's prompt, by the tool's name, and each prompt
no tool owns, by its prompts.toml key — edited in place; "" or a delete
restores the built-in — and the shared reminder, one text for every
story that switched it on; "" clears it."""

from otaku.web.api._request import Route, changed, plain

# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/tools/{tool}/prompt"): lambda api, ask: plain(
        api.tools.prompt.get(ask.params["tool"])
    ),
    ("PATCH", "/api/tools/{tool}/prompt"): lambda api, ask: changed(
        api.tools.prompt.update(ask.params["tool"], text=ask.need("text"))
    ),
    ("DELETE", "/api/tools/{tool}/prompt"): lambda api, ask: changed(
        api.tools.prompt.delete(ask.params["tool"])
    ),
    ("GET", "/api/prompts/{prompt}"): lambda api, ask: plain(api.prompts.get(ask.params["prompt"])),
    ("PATCH", "/api/prompts/{prompt}"): lambda api, ask: changed(
        api.prompts.update(ask.params["prompt"], text=ask.need("text"))
    ),
    ("DELETE", "/api/prompts/{prompt}"): lambda api, ask: changed(
        api.prompts.delete(ask.params["prompt"])
    ),
    ("GET", "/api/shared-reminder"): lambda api, ask: plain(api.shared_reminder.get()),
    ("PATCH", "/api/shared-reminder"): lambda api, ask: changed(
        api.shared_reminder.update(text=ask.need("text"))
    ),
}
