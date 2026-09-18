"""Story browser — opened by `/stories`.

The story list in front of the dossier (`screens.story`): one
Application, so drilling in and backing out are flicker-free. The list
is the level the dossier cannot be — every story at once — and Enter
opens the highlighted one as the four-tab dossier, on its messages
(cursor on the tail, so Enter-Enter still resumes an old story the way
it always has, and a story with nothing played is resumed by that one
Enter); Esc there reveals the list as it was left, filter and all.

A row's label is the story's title, else its newest story-so-far
rollup, else its first prompt. `/` filters; the filter also matches
full message content, indexed lazily on the first keystroke — the ONE
rule both frontends promise (`api.stories.search`). Del deletes a story
after a confirm; every write is EXECUTED inside the screen through
`backend.api` (the screens' one ownership rule), and a landing rides
`result` for the caller to print.
"""

from typing import Any

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition, Filter
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import AnyContainer, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D

from otaku.backend import StoryListing
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Session
from otaku.formatting import flatten, human_age, truncate
from otaku.terminal.screens.base import bordered_box, wrap_text
from otaku.terminal.screens.story import Dossier
from otaku.terminal.tty import latin_key


class StoryPicker(Dossier):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        rows = api_stories.listing(session)
        self.all: list[StoryListing] = list(rows)
        self.filtered: list[StoryListing] = list(rows)
        # The list's cursor and filter, stashed while the dossier is in
        # front; Esc restores them verbatim on the way back.
        self._list_state: tuple[int, bool, str] = (0, False, "")
        self.confirming_delete: bool = False
        # Where the browser opens: on the loaded story.
        if session.story_id is not None:
            for i, row in enumerate(self.all):
                if row.id == session.story_id:
                    self.cursor = i
                    break

    # ---------- the list level's panes (the dossier's are inherited) ----------

    def _header_text(self) -> StyleAndTextTuples:
        if self.dossier_on:
            return super()._header_text()
        n, total = len(self.filtered), len(self.all)
        label = f"Stories ({n} of {total})" if n != total else f"Stories ({n})"
        return [("class:header", " " + label)]

    def _items_text(self) -> StyleAndTextTuples:
        if self.dossier_on:
            return super()._items_text()
        out: StyleAndTextTuples = []
        if not self.filtered:
            msg = (
                "(no matches)"
                if self.query
                else "(none yet — start chatting and they'll show up here)"
            )
            out.append(("class:muted", "  " + msg))
            return out
        avail = self._max_row_content_width() - 4 - len("MM-DD HH:MM ·    N msg · ")
        for i, listing in enumerate(self.filtered):
            ts = listing.updated_at.astimezone().strftime("%m-%d %H:%M")
            head = truncate(flatten(listing.label), max(10, avail))
            line = f"{ts} · {listing.num_messages:>4} msg · {head}"
            self._emit_row(out, i == self.cursor, line)
        return out

    def _panel_header_text(self) -> StyleAndTextTuples:
        if self.dossier_on:
            return super()._panel_header_text()
        return [("", "")]

    def _preview_text(self) -> StyleAndTextTuples:
        if self.dossier_on:
            return super()._preview_text()
        if not self.filtered:
            return [("class:preview.muted", "nothing to preview")]
        width = max(10, self._preview_inner_width())
        row = self.filtered[self.cursor]
        out: StyleAndTextTuples = [
            ("class:preview.title", (row.model or "?") + "\n"),
            ("class:preview.body", "\n"),
            (
                "class:preview.muted",
                row.updated_at.astimezone().strftime("%a %Y-%m-%d %H:%M")
                + " · "
                + human_age(row.updated_at)
                # the number a story's files are named by (`store.files`)
                + f" · id {row.id}\n",
            ),
        ]
        # Title (if any) before the arc, each block separated by a blank
        # line; a story with neither simply shows nothing there.
        if row.title:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(flatten(row.title), width):
                out.append(("class:preview.title", line + "\n"))
        if row.story_so_far:
            out.append(("class:preview.body", "\n"))
            for line in wrap_text(row.story_so_far, width):
                out.append(("class:preview.body", line + "\n"))
        if row.first_user:
            out.append(("class:preview.body", "\n"))
            out.append(("class:preview.muted", "first prompt:\n"))
            for line in wrap_text(flatten(row.first_user), width):
                out.append(("class:preview.body", line + "\n"))
        return out

    def _help_text(self) -> StyleAndTextTuples:
        if self.dossier_on:
            return super()._help_text()
        if self.in_filter:
            return [
                (
                    "class:help",
                    " type to filter · ↑/↓ navigate · enter drill in · esc clear filter",
                )
            ]
        return [
            (
                "class:help",
                " ↑/↓ navigate · / filter · enter drill in · del delete · esc quit",
            )
        ]

    def _confirm_text(self) -> StyleAndTextTuples:
        return [
            ("class:dialog.title", "Delete this story?\n"),
            ("class:dialog.body", "(its messages, scenes, and cast go with it)\n"),
            ("class:dialog.muted", "y to confirm     n / esc to cancel"),
        ]

    # ---------- behavior ----------

    def _split(self) -> tuple[int, int]:
        if self.dossier_on:
            return super()._split()
        return (1, 1)

    def _rows_count(self) -> int:
        if self.dossier_on:
            return super()._rows_count()
        return len(self.filtered)

    def _refilter(self) -> None:
        if self.dossier_on:
            super()._refilter()
            return
        q = self.query.strip().lower()
        if not q:
            self.filtered = list(self.all)
        else:
            # The whole filter rule lives below both frontends
            # (api.stories.search): buried content OR the row's own
            # face — so this browser and the page can never find
            # different stories. Cheap per keystroke (the content index
            # is session-held), and the corpus never crosses the
            # boundary.
            found = set(api_stories.search(self.session, q))
            self.filtered = [row for row in self.all if row.id in found]
        if self.cursor >= len(self.filtered):
            self.cursor = max(0, len(self.filtered) - 1)

    def _filterable(self) -> bool:
        if self.dossier_on:
            return super()._filterable()
        return True

    def _esc_label(self) -> str:
        return "back"  # the list waits under the dossier

    def _on_enter(self) -> None:
        if self.dossier_on:
            super()._on_enter()
            return
        if not self.filtered:
            return
        row = self.filtered[self.cursor]
        # Nothing played: no message to pick, and a dossier opened on an
        # empty tab is a dead end — Enter resumes the story itself.
        if not row.num_messages:
            self._land_on(row.id, None, "resume")
            return
        self._list_state = (self.cursor, self.in_filter, self.query)
        self.in_filter, self.query = False, ""
        # Messages first, cursor on the tail: Enter-Enter resumes an old
        # story the way it always has, and the other tabs are a Tab away.
        self._open_dossier(row.id, "messages")

    def _on_escape(self) -> None:
        if self.dossier_on:
            super()._on_escape()
            return
        self.notice = ""
        if self._clear_filter():
            return
        get_app().exit()

    def _leave_dossier(self) -> None:
        """Esc at the dossier's top level: back out to the list, exactly
        as it was left."""
        self.dossier_on = False
        self.cursor, self.in_filter, self.query = self._list_state

    # ---------- deletion (the list level's one write) ----------

    def _request_delete(self) -> None:
        if self.confirming_delete or not self.filtered:
            return
        self.confirming_delete = True

    def _do_delete(self) -> None:
        if not self.filtered:
            self.confirming_delete = False
            return
        target = self.filtered[self.cursor]
        try:
            api_stories.delete(self.session, target.id)
        except Exception:
            # Silent failure is acceptable here — the row stays visible
            # and the user can try again or check the DB out of band.
            self.confirming_delete = False
            return
        self.all = [row for row in self.all if row.id != target.id]
        self._refilter()
        self.confirming_delete = False

    # ---------- the dossier's hooks ----------

    def _extra_idle(self) -> Filter:
        return Condition(lambda: not self.confirming_delete)

    def _extra_keys(self, kb: KeyBindings, idle: Filter) -> None:
        confirming = Condition(lambda: self.confirming_delete)

        # While the confirm dialog is up: only y/n/esc do anything.
        @kb.add("escape", eager=True, filter=confirming)
        def _confirm_esc(event: Any) -> None:
            self.confirming_delete = False

        @kb.add(Keys.Any, filter=confirming, eager=True)
        def _confirm_any(event: Any) -> None:
            key = latin_key(event.data) if event.data else ""
            if key == "y":
                self._do_delete()
            elif key == "n":
                self.confirming_delete = False

        at_list = Condition(lambda: not self.dossier_on)

        @kb.add("delete", filter=idle & at_list)
        def _delete_key(event: Any) -> None:
            self._request_delete()

        # The key macOS captions "delete" arrives as backspace. Honor the
        # caption wherever no filter is open for backspace to edit — added
        # after the standard keys, so it outranks their no-op exactly there.
        @kb.add("backspace", filter=idle & at_list & Condition(lambda: not self.in_filter))
        def _delete_backspace(event: Any) -> None:
            self._request_delete()

    def _extra_floats(self) -> list[AnyContainer]:
        return [
            ConditionalContainer(
                content=bordered_box(
                    FormattedTextControl(text=self._confirm_text, show_cursor=False),
                    width=D(min=44, max=70, preferred=60),
                    height=D.exact(7),
                    style="class:dialog.body",
                    border_style="class:dialog.border",
                ),
                filter=Condition(lambda: self.confirming_delete),
            )
        ]


def pick(session: Session) -> str | None:
    """Browse; on a confirmed landing the land is EXECUTED and its line
    returned for the caller to print (with the scene re-echo); None when
    cancelled (Esc/Ctrl+C) — the loaded story is pre-selected."""
    picker = StoryPicker(session)
    if not picker.all:
        return None
    return picker.run()
