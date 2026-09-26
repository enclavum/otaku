"""Story operations: what the browser and the story commands do.

Every story-level write a frontend may ask for lives here and keeps the
session in sync — the reason a frontend never edits or deletes through
the store itself. (Chain-changing writes exist in play/cards/transfer
too; ALL of them go through the session's own primitives, which carry
the sync and the search-index invalidation.)
"""

from dataclasses import replace
from typing import Literal

from otaku.backend.session import Refused, Session
from otaku.backend.story import StorySettings
from otaku.formatting import flatten, truncate_label
from otaku.store.ops.stories import StoryListing
from otaku.store.schema import InjectionPosition, Message

# What picking an earlier turn settles to: continue in a copy (the
# default), rewind the head (later turns stay as siblings), or stay.
LandAction = Literal["resume", "fork", "truncate"]

# A label INSIDE a backend sentence (the landed line, a fork notice, the
# info report) is cut here, as part of the wording — an unbounded name
# breaks a one-line sentence on any medium. `headline` itself stays
# uncut: raw material for surfaces that own their width (the banner cuts
# to this same number via `formatting.truncate_label`).
LABEL_WIDTH = 50


def listing(session: Session) -> list[StoryListing]:
    """Every story, most recently played first."""
    return session._store.stories.list()


def messages_of(session: Session, story_id: int) -> list[Message]:
    """One story's current chain, for the browser's drill-in."""
    return session._store.stories.get_messages(story_id)


def search(session: Session, query: str) -> list[int]:
    """Ids of the stories that match `query`, case-insensitive — the ONE
    filter rule both browsers promise, one call per keystroke: a story
    matches on its buried content (the CURRENT CHAIN's text) or on the
    face its listing row shows (label, arc summary, first prompt,
    model). Declared here because two frontends filtering by different
    halves would be two browsers finding different stories.

    The content half is served from the session-held index
    (`_search_index`), built on the first search and invalidated by the
    SESSION's write primitives — where every chain-changing write
    already funnels (play, undo, cards, imports, this module) — so a
    keystroke never re-decrypts the library and the corpus never crosses
    the boundary. Best-effort throughout: a filter is never worth a
    failure, so a store hiccup narrows the match rather than raising."""
    if session._search_index is None:
        try:
            session._search_index = session._store.stories.get_texts()
        except Exception:
            session._search_index = {}
    needle = query.strip().lower()
    if not needle:
        return list(session._search_index)
    ids = {sid for sid, text in session._search_index.items() if needle in text}
    try:
        rows = session._store.stories.list()
    except Exception:
        rows = []
    for row in rows:
        if needle in f"{row.label} {row.story_so_far} {row.first_user} {row.model}".lower():
            ids.add(row.id)
    return sorted(ids)


def land(
    session: Session,
    story_id: int,
    upto_message_id: int | None = None,
    action: LandAction = "resume",
) -> str:
    """Execute what the browser settled — resume as-is, fork at the
    picked turn, or truncate to it — switch the session there, and return
    the landing line ("Story: …. Resumed at message 14."). A resume never
    used the pick, so it may come without one: a story with nothing
    played has no message to pick and is resumed by its id alone. Forking
    and truncating cut AT a message, so without one they are a caller's
    error, not a refusal."""
    store = session._store
    if upto_message_id is None:
        if action != "resume":
            raise ValueError(f"{action} needs a message to land on")
        session._switch_to(story_id)
        return _landed(session)
    messages = store.stories.get_messages(story_id)
    position = next((i for i, m in enumerate(messages) if m.id == upto_message_id), None)
    if position is None:
        raise Refused("That message is not on the story's current chain.")
    if action == "fork":
        forked = store.stories.fork(story_id, from_message_id=upto_message_id)
        session._search_index = None
        session._switch_to(forked)
        label = truncate_label(headline(session), LABEL_WIDTH)
        lead = f"Forked to: {label}." if label else "Forked."
        return f"{lead} Continued from message {len(session.messages)}."
    if action == "truncate":
        store.stories.set_head(story_id, upto_message_id)
        session._search_index = None
        session._switch_to(story_id, messages[: position + 1])
        return _landed(session, verb="Truncated")
    # Resume attaches to the story as-is — the tail was picked, so the
    # chain and the pick agree and nothing changes in the store.
    session._switch_to(story_id)
    return _landed(session)


def fork(session: Session, raw: str = "", story_id: int | None = None) -> str:
    """Continue in a copy of a story from its head; the original stays.
    `raw` is the optional TITLE — "" inherits a numbered one ("<title>
    - N", or none when the story has none). `story_id` names a story
    that is not open, for a browser copying one from the outside — the
    open story when it is None, which is the only form the terminal
    ever uses. Returns the notice. Raises Refused when there is nothing
    to fork."""
    story_id = session.story_id if story_id is None else story_id
    if story_id is None or not session._store.stories.get_messages(story_id):
        raise Refused("Nothing to fork yet — send a message first.")
    forked = session._store.stories.fork(story_id, title=raw.strip() or None)
    session._search_index = None
    session._switch_to(forked)
    # Named the way the browser's fork names it.
    label = truncate_label(headline(session), LABEL_WIDTH)
    return f"Forked to: {label}." if label else "Forked."


def new(session: Session, raw: str = "") -> str:
    """Start a brand-new story, created AT ONCE — it is in the browser
    and carries its title before its first turn, not after. `raw` is the
    optional TITLE. Returns the notice."""
    title = raw.strip()
    session._switch_to(session._store.stories.add(title=title or None))
    session._search_index = None
    if not title:
        return "Started a new story."
    return f'Started a new story: "{truncate_label(title, LABEL_WIDTH)}".'


def set_title(session: Session, raw: str, story_id: int | None = None) -> str:
    """Title a story: the open one, or the one `story_id` names — a
    browser can reach every story, and a title is how a reader finds one
    again. Returns the confirmation.

    An empty title means two different things, and both are answered:
    asked of the OPEN story it is the bare command, which reports the
    title instead; asked of a NAMED one it is a rename that emptied the
    field, and it is refused rather than applied — a story with no title
    falls back to its own opening text, and clearing a title is a
    different decision from giving one."""
    title = raw.strip()
    if not title:
        if story_id is not None:
            raise Refused("A story needs a title — or leave the one it has.")
        story = (
            session._store.stories.get(session.story_id) if session.story_id is not None else None
        )
        current = story.title if story else ""
        return f'Title: "{current}"' if current else "Usage: /title NEW-TITLE"
    session._store.stories.set_title(story_id or session._ensure_story(), title)
    return f'Story title set to "{title}".'


def get_system(session: Session, story_id: int) -> str:
    """The system prompt — the premise — of ANY story, for a browser
    reading one that is not open; the open story's arrives with the
    session facts. Named for its writer, `set_system` below."""
    return session._store.stories.get_system(story_id)


def set_system(session: Session, text: str, story_id: int | None = None) -> str:
    """Set a story's system prompt (the premise) to `text` verbatim — ""
    CLEARS it: every caller is an editor or a command that has already
    decided to write, so an emptied premise is a premise removed, never
    a question (the bare `/system` report is `report_system`'s). It
    lives on the story, never on the model. `story_id` names a story
    that is not open, for a browser correcting one from the outside —
    the open story's own when it is None, which is the only form the
    terminal ever uses. The terminal's file affordance (`/system FILE`)
    resolves the file to text on ITS side — over HTTP a path must never
    name a server-side file. Returns the confirmation."""
    if story_id is not None and story_id != session.story_id:
        session._store.stories.set_system(story_id, text)
    else:
        session._set_system(text)
    return f"System prompt set ({len(text)} chars)." if text else "System prompt cleared."


def report_system(session: Session) -> str:
    """What the bare `/system` answers: the open story's premise, or
    that there is none. A report, so nothing changes."""
    return f'System: "{session.system}"' if session.system else "System: (none)"


def get_settings(session: Session, story_id: int | None = None) -> StorySettings:
    """Every setting of a story as it stands — the open story's, or the
    one `story_id` names: what it stored laid over the defaults, each with
    its label and, where it injects, the positions it may take."""
    if story_id is not None and story_id != session.story_id:
        from_db = session._store.stories.get_settings(story_id)
    else:
        from_db = session._settings_db
    return StorySettings(from_db, session._store, session._paths.prompts_file)


def update_setting(
    session: Session,
    name: str,
    *,
    story_id: int | None = None,
    enabled: bool | None = None,
    position: InjectionPosition | None = None,
    reminder_text: str | None = None,
    display_notes: bool | None = None,
) -> str:
    """Change one setting of a story — the open one, or the one
    `story_id` names; what is not given stays. Returns the confirmation.
    Raises Refused for a setting that does not exist, and for what the
    setting does not take (`StorySetting.to_db`)."""
    settings = get_settings(session, story_id)
    setting = settings.get(name)
    if setting is None:
        known = ", ".join(each.name for each in settings)
        raise Refused(f"Unknown setting {name!r}. Settings: {known}.")
    row = setting.to_db(
        enabled=enabled,
        position=position,
        reminder_text=reminder_text,
        display_notes=display_notes,
    )
    if story_id is not None and story_id != session.story_id:
        session._store.stories.set_setting(story_id, row)
    else:
        session._set_setting(row)
    # Where it now stands, in words.
    if not row.enabled:
        return f"{setting.label}: off."
    if row.position is None:
        return f"{setting.label}: on."
    if row.position.depth is None:
        return f"{setting.label}: on, in the system message."
    # before which of the reader's messages, counted from the end
    depth = row.position.depth
    if depth == 1:
        return f"{setting.label}: on, before your latest message."
    if depth == 2:
        return f"{setting.label}: on, before your previous message."
    suffix = "rd" if depth == 3 else "th"
    return f"{setting.label}: on, before your {depth}{suffix}-last message."


def get_shared_reminder(session: Session) -> str:
    """The shared reminder's text — one text, which every story that
    switched `use_shared_reminder` on is sent."""
    return session._store.settings.get_shared_reminder()


def set_shared_reminder(session: Session, text: str) -> str:
    """The shared reminder's text; "" clears it. Returns the confirmation."""
    session._store.settings.set_shared_reminder(text.strip())
    return "Shared reminder saved." if text.strip() else "Shared reminder cleared."


def delete(session: Session, story_id: int) -> None:
    """Drop a story and everything it owns — the one destructive act,
    and the user's. A session attached to it detaches."""
    session._store.stories.delete(story_id)
    session._search_index = None
    if session.story_id == story_id:
        session._story_id = None
        session._system = ""
        session._messages = []
        session._update_state()


def edit_message(session: Session, message_id: int, body: str, story_id: int | None = None) -> None:
    """The author's correction of one message — the session's in-memory
    copy follows the store. `story_id` is the story the caller believes
    the message belongs to, and is CHECKED: a browser addresses any
    story, and a correction that landed on a message of another one
    would rewrite a story nobody was looking at. Raises Refused on an
    empty body, or on a message that is not that story's."""
    if not body.strip():
        raise Refused("A message cannot be emptied — undo the exchange instead.")
    if story_id is not None:
        chain = session._store.stories.get_messages(story_id)
        if not any(message.id == message_id for message in chain):
            raise Refused("That message is not on the story's current chain.")
    session._store.messages.update(message_id, body)
    session._search_index = None
    for i, m in enumerate(session._messages):
        if m.id == message_id:
            session._messages[i] = replace(m, body=body)
            break


def headline(session: Session) -> str:
    """The loaded story's name — `StoryListing.label`'s fallback rule
    applied to the loaded story (the rule has that ONE home), flattened
    to one line, UNCUT (display width is the frontend's). "" when
    nothing exists to name."""
    if session.story_id is None:
        return ""
    story = session._store.stories.get(session.story_id)
    if story is None:
        return ""
    label = story.title
    if not label:
        label = session._store.scenes.get_story_so_far(
            session.story_id, [m.id for m in session.messages]
        )
    if not label:
        label = next((m.body for m in session.messages if m.role == "user"), "")
    return flatten(label)


def landed_line(session: Session) -> str:
    """The one line a session prints when it lands in a story at launch
    ("Story: …. Resumed at message 14."). `land` composes its own line —
    the verb follows the action, and no frontend injects wording."""
    return _landed(session)


def _landed(session: Session, *, verb: str = "Resumed") -> str:
    """The landing line, `verb` naming what the pick settled. An unnamed
    story drops the first half rather than show an empty name, and one
    with nothing played says so rather than count to zero."""
    label = truncate_label(headline(session), LABEL_WIDTH)
    head = f"Story: {label}. " if label else ""
    if not session.messages:
        return f"{head}{verb}, nothing played yet."
    return f"{head}{verb} at message {len(session.messages)}."
