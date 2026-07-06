"""Tests for the slash-command tab completer."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from otaku.chat.commands import KNOWN_PARAMS
from otaku.chat.completer import build_completer


def complete(text: str) -> list[str]:
    completer = build_completer()
    doc = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


class TestTopLevel:
    def test_slash_lists_all_commands(self) -> None:
        out = complete("/")
        assert "/clear" in out
        assert "/set" in out
        assert "/history" in out

    def test_prefix_narrows(self) -> None:
        assert complete("/cl") == ["/clear"]

    def test_prefix_multiple_matches(self) -> None:
        # /help and /history both begin with /h
        assert set(complete("/h")) == {"/help", "/history"}

    def test_no_match(self) -> None:
        assert complete("/zzz") == []


class TestSetSubcommands:
    def test_lists_set_children_after_space(self) -> None:
        assert set(complete("/set ")) == {"system", "think", "verbose", "parameter"}

    def test_verbose_levels(self) -> None:
        assert set(complete("/set verbose ")) == {"on", "off"}

    def test_narrows_subcommand(self) -> None:
        assert complete("/set th") == ["think"]

    def test_think_levels(self) -> None:
        out = complete("/set think ")
        assert set(out) == {"on", "off", "none", "low", "medium", "high", "max", "default"}

    def test_parameter_names(self) -> None:
        assert set(complete("/set parameter ")) == set(KNOWN_PARAMS)

    def test_leaf_node_offers_nothing(self) -> None:
        # /set think on -> a leaf (None subtree); nothing further to complete
        assert complete("/set think on ") == []


class TestNonCommand:
    def test_bare_command_with_no_subtree(self) -> None:
        # /clear has no arguments (None subtree) → no completions past it
        assert complete("/clear ") == []

    def test_copy_completes_all(self) -> None:
        assert complete("/copy ") == ["all"]
