"""The skeleton every tab of the dossier stands on: a story opened and
read again, the tab strip and what each tab keeps while another is in
front (`_TabState`), the field list of an open scene or character
(`Field`), the panes' split, and the seams the story browser fills
(`_leave_dossier`, the `_extra_*` hooks). What routes by tab — the rows,
the keys, the editing, the wiring — is the assembled `Dossier`'s, and
declared here as the hooks it fills."""

from dataclasses import dataclass
from typing import Literal

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, Filter
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import (
    KeyBindings,
)
from prompt_toolkit.layout.containers import (
    AnyContainer,
)

from otaku.formatting import flatten, truncate
from otaku.terminal.api import Api
from otaku.terminal.api.app.integrations import WebSearch
from otaku.terminal.api.story.lore import Lore
from otaku.terminal.api.story.messages import Message
from otaku.terminal.api.story.settings import StorySetting
from otaku.terminal.screens.base import (
    ListScreen,
    ansi_fragments,
    wrap_text,
)
from otaku.terminal.tty.render import message as render_message
from otaku.terminal.tty.typography import highlight_toml

Tab = Literal["premise", "messages", "scenes", "cast", "tools"]


_TABS: tuple[Tab, ...] = ("premise", "messages", "scenes", "cast", "tools")


_TAB_LABELS: dict[Tab, str] = {
    "premise": "Premise",
    "messages": "Messages",
    "scenes": "Scenes",
    "cast": "Cast",
    "tools": "Tools",
}


# List-to-preview split, list:preview. The message rows carry the content
# and the preview only echoes one, so the list gets twice the width; the
# lens lists and their field details share it evenly.
_TURN_SPLIT = (2, 1)


_LENS_SPLIT = (1, 1)


# Every field a detail view shows, by the resource and field it is.
# Hand-editable is what is written once and read from then on (titles,
# summaries, entries, descriptions, profiles); the extractor's own — `state`
# (superseded by the next scene's row), `history` and `scene-history`
# (rebuilt from the primitives) — is shown and has no write.
FieldKind = Literal[
    "scene-title",
    "scene-summary",
    "scene-history",
    "description",
    "profile",
    "entry",
    "state",
    "history",
]


@dataclass(frozen=True)
class Field:
    """One row of a detail view: a label, the text it holds, and the
    address (`kind`, `target`) an edit or a pivot on it names."""

    label: str
    kind: FieldKind
    text: str
    target: int  # scene id / character id / journal id, per kind
    editable: bool
    pivot: int | None = None  # the other parent: character id / scene id
    scene_no: int | None = None  # a journal row's scene number, in the cast view


@dataclass
class _TabState:
    """What a tab keeps while another is in front: its cursor and its
    filter. An open detail is deliberately NOT kept — leaving the tab
    closes it, and coming back lands on the list."""

    cursor: int = 0
    in_filter: bool = False
    query: str = ""


class DossierBase(ListScreen):
    """The dossier's state and its shared machinery, below the tabs."""

    def __init__(self, api: Api) -> None:
        super().__init__()
        self.api = api
        # How a message body looks — chat's one renderer, reached from
        # below it.
        self._render = render_message

        # The dossier's subject; unset until `_open_dossier` (the picker
        # subclass starts on its list level instead).
        self.dossier_on: bool = False
        self.story_id: int = 0
        self.tab: Tab = "messages"
        self.tabs: dict[Tab, _TabState] = {}

        # The loaded story: its premise, its chain, its memory.
        self.premise: str = ""
        self.msgs: list[Message] = []
        self.view: Lore | None = None

        # Per-tab row indices (the filters narrow these); the open
        # detail's field list.
        self.turn_filtered: list[int] = []
        self.scenes_f: list[int] = []
        self.cast_f: list[int] = []
        self.detail: tuple[str, int] | None = None
        self.fields: list[Field] = []
        self._list_pos: int = 0
        self._premise_rows: int = 1

        # The tools tab: the story's settings that have a caption, in the
        # caption table's order, and the shared reminder's text; whether
        # the story displays the model's notes rides along, for the
        # messages tab's rows and preview. `in_panel` is the cursor having
        # moved into the panel, `field` which of the setting's fields it
        # is on; `choosing` is the depth dropdown, `choice` where ↑/↓ have
        # walked it over the allowed places.
        self.settings: list[StorySetting] = []
        self.shared_text: str = ""
        # where web search stands: its engine, and whether it holds a key
        self.web_search = WebSearch(False)
        self.prompts: dict[str, str] = {}  # each injection setting's prompt, by its name
        self.shown_tools: frozenset[str] = frozenset()
        self.in_panel: bool = False
        self.field: int = 0
        self.choosing: bool = False
        self.choice: int = 0

        # The resume dialog (Enter on an earlier message): up or not, and
        # which _RESUME_OPTIONS row is highlighted (fork is the default).
        self.confirming_resume: bool = False
        self.resume_choice: int = 0
        # The delete confirm (Del on a cast row): up or not.
        self.confirming_cast_delete: bool = False

        # A prompt open for editing: the tools panel is the editor alone
        # until Ctrl+S or Esc (`_start_edit` sets it, `_finish_edit` clears).
        self.prompt_editing: bool = False
        # Inline editing: while True the text under the cursor — the
        # premise, a message, a field — is the edit buffer and every
        # navigation binding is suspended.
        self.editing: bool = False
        self.edit_buffer = Buffer(multiline=True)

        # The story the landing settled in and its line (`_land_on`);
        # None until then.
        self.result: tuple[int, str] | None = None
        # Built last: the layout is callables over the state above, and a
        # subclass's hooks are already its own (nothing renders until run).
        self.application = self._build_application()

    def run(self) -> tuple[int, str] | None:
        self.application.run()
        return self.result

    def _open_dossier(self, story_id: int, tab: Tab) -> None:
        """Load the story whole — premise, chain, memory — and open on
        `tab`, every tab's state fresh (messages starts on the tail)."""
        self.story_id = story_id
        self.premise = self.api.stories.get(story_id).premise
        try:
            self.msgs = self.api.stories.messages.list(story_id)
        except Exception:
            self.msgs = []
        self.view = self.api.stories.lore.get(story_id)
        self.turn_filtered = list(range(len(self.msgs)))
        self.scenes_f = list(range(len(self.view.scenes)))
        self.cast_f = list(range(len(self.view.characters)))
        self.tabs = {name: _TabState() for name in _TABS}
        self.tabs["messages"].cursor = max(0, len(self.turn_filtered) - 1)
        self.detail = None
        self.fields = []
        self._reload_settings()
        self.tab = tab
        state = self.tabs[tab]
        self.cursor, self.in_filter, self.query = state.cursor, False, ""
        self.notice = ""
        self.dossier_on = True

    def _reload(self) -> None:
        """The whole memory again, after a save — cheap for one story, and
        the store's own invalidation is never mirrored by hand. The
        visible sets are NOT recomputed: an edit that drops a row out of
        an open filter would otherwise take it away as it is saved."""
        self.view = self.api.stories.lore.get(self.story_id)
        self._rebuild_fields()

    def _tabstrip_text(self) -> StyleAndTextTuples:
        """The four tab names on one row — the dossier's whole header,
        the active one bold in the text color, the rest dim."""
        out: StyleAndTextTuples = [("", " ")]
        for name in _TABS:
            style = "class:tab.active" if name == self.tab else "class:tab"
            out.append((style, f" {_TAB_LABELS[name]} "))
            out.append(("", " "))
        return out

    def _switch_tab(self, step: int) -> None:
        self.notice = ""
        self.in_panel = False
        self._stash_tab()
        self.tab = _TABS[(_TABS.index(self.tab) + step) % len(_TABS)]
        self._restore_tab()

    def _stash_tab(self) -> None:
        # A detail does not survive the switch: what is kept is the LIST
        # as it stood — cursor on the row that was drilled into.
        self.tabs[self.tab] = _TabState(
            cursor=self._list_pos if self.detail is not None else self.cursor,
            in_filter=self.in_filter,
            query=self.query,
        )

    def _restore_tab(self) -> None:
        state = self.tabs[self.tab]
        self.in_filter, self.query = state.in_filter, state.query
        self.detail = None
        self.fields = []
        self.cursor = min(state.cursor, max(0, self._rows_count() - 1))

    def _split(self) -> tuple[int, int]:
        return _TURN_SPLIT if self.tab == "messages" else _LENS_SPLIT

    def _move_cursor(self, delta: int) -> None:
        self.notice = ""
        super()._move_cursor(delta)

    def _esc_label(self) -> str:
        """What Esc does at the dossier's top level — the picker's list
        waits underneath it; standalone there is only the prompt."""
        return "quit"

    def _field_rows(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if not self.fields:
            out.append(("class:muted", "  (nothing here yet)"))
            return out
        # The char view's journal rows carry their scene's number as its
        # own column, enumerated like the scenes list; rows without one
        # (description, history) keep the column blank.
        nos = [f.scene_no for f in self.fields if f.scene_no is not None]
        no_w = max((len(str(n)) for n in nos), default=0)
        label_w = min(24, max(len(f.label) for f in self.fields))
        avail = max(10, self._max_row_content_width() - 4 - no_w - 2 - label_w - 3)
        for i, f in enumerate(self.fields):
            head = truncate(flatten(f.text[: 4 * avail]), avail) or "(empty)"
            no = f"{f.scene_no:>{no_w}}  " if f.scene_no is not None else " " * (no_w + 2)
            row = f"{no if no_w else ''}{truncate(f.label, label_w):<{label_w}} · {head}"
            self._emit_row(out, i == self.cursor, row, dim=not f.editable)
        return out

    def _field_preview(self, width: int) -> StyleAndTextTuples:
        if not self.fields:
            return [("class:preview.muted", "nothing to preview")]
        out: StyleAndTextTuples = []
        f = self.fields[self.cursor]
        if f.kind == "profile" and f.text:
            # The profile reads as TOML: keys and macros in the command
            # color — highlighted after the wrap, so the widths stay
            # honest (escapes take no columns the wrap could count).
            wrapped = "\n".join(wrap_text(f.text, width))
            for line in highlight_toml(wrapped).split("\n"):
                out.extend(ansi_fragments(line, "class:preview.body"))
                out.append(("class:preview.body", "\n"))
            return out
        for line in wrap_text(f.text or "(empty)", width):
            out.append(("class:preview.body", line + "\n"))
        return out

    @staticmethod
    def _display_rows(text: str, width: int) -> list[tuple[int, int, bool]]:
        """(start, length, line_end) of each wrapped DISPLAY row — the same
        character wrapping the Window renders, so motion by row lands where
        the eye expects."""
        rows: list[tuple[int, int, bool]] = []
        pos = 0
        for line in text.split("\n"):
            start = 0
            while True:
                length = min(width, len(line) - start)
                line_end = start + length >= len(line)
                rows.append((pos + start, length, line_end))
                if line_end:
                    break
                start += length
            pos += len(line) + 1
        return rows

    def _leave_dossier(self) -> None:
        """Esc at the dossier's top level. Standalone the dossier is the
        whole screen, so it quits; the picker overrides this to reveal
        its list again."""
        get_app().exit()

    def _extra_idle(self) -> Filter:
        """What else must be quiet for the standard keys to fire — the
        picker's delete confirm; nothing standalone."""
        return Condition(lambda: True)

    def _extra_keys(self, kb: KeyBindings, idle: Filter) -> None:
        """The picker's own keys (delete and its confirm), added after the
        standard ones so its backspace outranks their no-op."""

    def _extra_floats(self) -> list[AnyContainer]:
        """The picker's own dialogs."""
        return []

    # ---------- filled above: by the tools tab, or by the assembled `Dossier` ----------

    # The tabs' hooks the skeleton calls: the settings are the tools tab's
    # to read; the rest routes by tab, and is the assembled `Dossier`'s.

    def _reload_settings(self) -> None:
        raise NotImplementedError

    def _build_application(self) -> Application[None]:
        raise NotImplementedError

    def _rebuild_fields(self) -> None:
        raise NotImplementedError

    def _rows_count(self) -> int:
        raise NotImplementedError

    def _refilter(self) -> None:
        raise NotImplementedError

    def _start_edit(self) -> None:
        raise NotImplementedError


def made_by(provider: str | None, model: str | None, width: int) -> StyleAndTextTuples:
    """Who made a turn, a scene or a character, closing its preview: after
    a blank row, dimmed and right-aligned. Nothing without a model — a
    row no pass made, or one made before anything recorded it."""
    if not model:
        return []
    label = f"{provider}/{model}" if provider else model
    return [("class:preview.body", "\n"), ("class:preview.muted", label.rjust(width) + "\n")]
