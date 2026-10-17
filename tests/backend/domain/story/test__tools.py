"""The tools a model may use by writing a call in its reply.

A tool is declared by its class — its name, who acts on its call —
and registered under its name; `read_call` binds one call to the tool
its name says, in any case, and `get_tool_label` names a tool's turn
for a reader: its label and "call", or what the result says it is. `ToolQuestion` reads
its call as a question and the answers to pick from: the question is
everything before the first answer line; an answer line opens `1.`,
`1)`, `a.` or `a)`; none makes the question free-form; whatever follows
the answers that is not one is no answer; nine at most. `to_json` is
the call as a frontend draws it. A tool otaku RUNS answers its call
with `run(session)`, reading the session and never writing it: the
system info tool the local time, the sky and the model's name, the web
search tool the top results' snippets through the web search
integration, cut to a budget, or the sentence for why there are none —
and a failed search's turn is called so (`label_of`).
"""

import re
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from otaku.backend.domain.app import App
from otaku.backend.domain.story._tools import (
    TOOLS,
    Actor,
    Tool,
    ToolAssistantNote,
    ToolQuestion,
    ToolSystemInfo,
    ToolWebSearch,
    get_tool_label,
    read_call,
)
from otaku.integrations import (
    IntegrationConfig,
    IntegrationError,
    Integrations,
    SearchResult,
    WebSearch,
)
from otaku.settings.integrations import IntegrationSettings
from otaku.settings.state import ModelRef


class TestAsk:
    def test_the_question_and_its_numbered_answers(self) -> None:
        ask = ToolQuestion("\nStay at the inn, or go?\n1. She stays\n2. She goes\n")
        assert ask.question == "Stay at the inn, or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_question_without_answers_is_free_form(self) -> None:
        ask = ToolQuestion("What is her brother's name?")
        assert ask.question == "What is her brother's name?"
        assert ask.options == ()

    def test_answers_may_be_lettered_or_closed_with_a_bracket(self) -> None:
        ask = ToolQuestion("Stay or go?\na) She stays\nB. She goes\n3) She hides")
        assert ask.options == ("She stays", "She goes", "She hides")

    def test_the_question_may_run_over_lines(self) -> None:
        ask = ToolQuestion("The bridge is out.\nWhich way now?\n1. Upstream\n2. Back")
        assert ask.question == "The bridge is out.\nWhich way now?"

    def test_a_line_after_the_answers_is_no_answer(self) -> None:
        # A picked answer is SENT as the reader's line: a closing remark
        # must not ride it.
        ask = ToolQuestion("Stay or go?\n1. She stays\n2. She goes\nChoose wisely!")
        assert ask.options == ("She stays", "She goes")

    def test_blank_lines_are_passed_over(self) -> None:
        ask = ToolQuestion("Stay or go?\n\n1. She stays\n\n2. She goes")
        assert ask.question == "Stay or go?"
        assert ask.options == ("She stays", "She goes")

    def test_a_number_inside_a_sentence_opens_no_answer(self) -> None:
        ask = ToolQuestion("Is it chapter 1. or chapter 2?")
        assert ask.question == "Is it chapter 1. or chapter 2?"
        assert ask.options == ()

    def test_nine_answers_at_most(self) -> None:
        ask = ToolQuestion("Which?\n" + "\n".join(f"{n}. Door {n}" for n in range(1, 13)))
        assert ask.options == tuple(f"Door {n}" for n in range(1, 10))

    def test_the_call_is_kept_as_written(self) -> None:
        text = "\nStay or go?\n1. She stays\n"
        assert ToolQuestion(text).text == text

    def test_to_json_carries_the_tool_who_acts_on_it_and_what_it_read(self) -> None:
        assert ToolQuestion("Stay or go?\n1. Stay").to_json() == {
            "tool": "question",
            "label": "Questions",
            "actor": "user",
            "text": "Stay or go?\n1. Stay",
            "question": "Stay or go?",
            "options": ["Stay"],
        }
        assert ToolAssistantNote("the seal").to_json() == {
            "tool": "note",
            "label": "Notes",
            "actor": "nobody",
            "text": "the seal",
        }


class TestIsOption:
    """`ToolQuestion.is_option` — the rule the parse parts a question
    from its options by, for a reader of the call as it streams."""

    def test_an_option_is_a_head_then_the_answer(self) -> None:
        for line in ("1. Yes", "2) No", "a. Yes", "B) No", "12. Twelve", "  3. Indented"):
            assert ToolQuestion.is_option(line), line

    def test_a_head_alone_or_glued_to_its_text_is_not_one(self) -> None:
        for line in ("1.", "1.Yes", "1", "Yes", "", "A question?", "10x. No", "123. Too long"):
            assert not ToolQuestion.is_option(line), line

    def test_agrees_with_the_parse(self) -> None:
        text = "Go in?\n1. Yes\n2. No\nA remark.\n3. Wait"
        lines = text.splitlines()
        question = "\n".join(line for line in lines if not ToolQuestion.is_option(line))
        # the parse keeps the lines before the first option as the question
        assert ToolQuestion(text).question == "Go in?"
        assert question.startswith("Go in?")
        assert [line for line in lines if ToolQuestion.is_option(line)] == [
            "1. Yes",
            "2. No",
            "3. Wait",
        ]


class TestReadCall:
    def test_a_call_is_read_as_the_tool_its_name_says(self) -> None:
        ask = read_call("question", "Stay or go?\n1. Stay")
        assert isinstance(ask, ToolQuestion) and ask.options == ("Stay",)
        assert isinstance(read_call("note", "the letter is forged"), ToolAssistantNote)
        assert isinstance(read_call("web-search", "tide tables"), ToolWebSearch)
        assert isinstance(read_call("system-info", ""), ToolSystemInfo)

    def test_the_name_is_read_in_any_case(self) -> None:
        assert isinstance(read_call("NOTE", "x"), ToolAssistantNote)

    def test_a_name_no_tool_owns_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            read_call("plan", "x")


class TestLabel:
    """`get_tool_label` — what a frontend calls the tool a tool turn's
    `provider` names."""

    def test_a_known_tool_is_called_by_its_label_and_call(self) -> None:
        assert get_tool_label("web-search", "Tides: High water.") == "Web search call"
        assert get_tool_label("system-info", "Time: …") == "System info call"
        assert get_tool_label("note", "") == "Notes call"

    def test_a_search_that_failed_is_called_so(self) -> None:
        failed = "Web search failed: the key is missing."
        assert get_tool_label("web-search", failed) == "Web search failed"
        assert ToolWebSearch.label_of(failed) == "Web search failed"
        # a result that merely mentions a failure is a result
        assert get_tool_label("web-search", "News: Web search failed again") == "Web search call"

    def test_a_tool_this_build_does_not_have_is_called_by_its_name_and_none_a_tool(self) -> None:
        assert get_tool_label("plan", "x") == "Plan call"
        assert get_tool_label(None, "x") == "Tool call"
        assert get_tool_label("", "x") == "Tool call"


class TestRun:
    """`Tool.run` — the result of a call of a tool otaku runs, the TOOL
    turn's text, read off the session."""

    def test_only_a_tool_otaku_runs_has_a_result(self) -> None:
        for tool in TOOLS.values():
            if tool.actor is Actor.SYSTEM:
                assert isinstance(tool("").run(_app(_founded())), str), tool.name
            else:
                with pytest.raises(NotImplementedError):
                    tool("x").run(_app(_founded()))

    def test_the_system_info_tool_answers_the_hour_the_sky_and_the_model(self) -> None:
        lines = ToolSystemInfo("").run(_app(_founded(), model="gemma-4")).splitlines()
        assert [line.split(": ", 1)[0] for line in lines] == ["Time", "Moon", "Sun", "Model"]
        # the local time and weekday, no zone: nothing that tells where the machine is
        assert re.fullmatch(r"Time: \d{4}-\d{2}-\d{2} \d{2}:\d{2} \([A-Z][a-z]+\)", lines[0])
        assert lines[3] == "Model: gemma-4"

    def test_the_search_asks_web_search_with_the_query_collapsed(self) -> None:
        engine = _Engine([SearchResult("Tides", "High water at six.")])
        result = ToolWebSearch("  tide\n tables  ").run(_app(_bound(engine)))
        assert engine.asked == [("tide tables", ToolWebSearch.MAX_RESULTS)]
        assert result == "Tides: High water at six."

    def test_the_results_are_title_snippet_paragraphs_without_the_empty_ones(self) -> None:
        engine = _Engine(
            [
                SearchResult("Tides", " High water at six. "),
                SearchResult("Nothing", "   "),
                SearchResult("Moon", "Full tonight."),
            ]
        )
        result = ToolWebSearch("tides").run(_app(_bound(engine)))
        assert result == "Tides: High water at six.\n\nMoon: Full tonight."

    def test_the_snippets_are_cut_to_the_character_budget(self) -> None:
        budget = ToolWebSearch.CHAR_BUDGET
        engine = _Engine([SearchResult("A", "x" * (budget - 10)), SearchResult("B", "y" * 100)])
        result = ToolWebSearch("q").run(_app(_bound(engine)))
        assert result == "A: " + "x" * (budget - 10) + "\n\nB: " + "y" * 10
        # nothing past the budget, however many results come back
        engine = _Engine([SearchResult("A", "x" * budget), SearchResult("B", "y")])
        assert ToolWebSearch("q").run(_app(_bound(engine))) == "A: " + "x" * budget

    def test_no_results_no_query_and_no_key_each_answer_a_sentence(self) -> None:
        assert ToolWebSearch("q").run(_app(_bound(_Engine([])))) == "No results found."
        assert ToolWebSearch("   ").run(_app(_bound(_Engine([])))) == "No query was given."
        # the integration with no key, which asks nothing
        missing = "Web search failed: the key is missing."
        assert ToolWebSearch("q").run(_app(_founded())) == missing

    def test_a_failing_engine_answers_the_sentence_the_model_reads(self) -> None:
        engine = _Engine(IntegrationError("the service answered 500"))
        result = ToolWebSearch("q").run(_app(_bound(engine)))
        # no engine named: which service the key is for is the reader's business
        assert result == "Web search failed: the service answered 500."


class TestRegistry:
    def test_every_tool_is_registered_under_its_own_name(self) -> None:
        # Holds a forgotten registration AND a name two tools claim: the
        # registry keeps one class per name, so one of the two would miss.
        for tool in every_tool(Tool):
            assert TOOLS.get(tool.name) is tool, tool.__name__

    def test_a_name_is_lowercase_words_joined_by_single_hyphens(self) -> None:
        for name in TOOLS:
            assert re.fullmatch(r"[a-z]+(-[a-z]+)*", name), name

    def test_a_call_has_its_actor_and_an_aside_has_none(self) -> None:
        assert ToolQuestion.actor is Actor.USER
        assert ToolAssistantNote.actor is Actor.NOBODY
        assert ToolSystemInfo.actor is Actor.SYSTEM
        assert ToolWebSearch.actor is Actor.SYSTEM

    def test_the_four_tools_and_their_names(self) -> None:
        assert set(TOOLS) == {"question", "note", "system-info", "web-search"}


class _Engine(WebSearch):
    """Web search answering canned results, or raising — and recording
    what it was asked."""

    def __init__(self, answer: list[SearchResult] | IntegrationError) -> None:
        super().__init__(IntegrationConfig(WebSearch.id, "", "k"))
        self._answer = answer
        self.asked: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.asked.append((query, max_results))
        if isinstance(self._answer, IntegrationError):
            raise self._answer
        return self._answer


def _founded() -> Integrations:
    """Every integration bound as an empty file founds it: web search with no key."""
    return Integrations.bind(IntegrationSettings())


def _bound(engine: WebSearch) -> Integrations:
    founded = _founded()
    return replace(founded, by_id={**founded.by_id, WebSearch.id: engine})


def _app(integrations: Integrations, *, model: str = "m") -> App:
    """A stand-in app holding what a tool reads off one: the
    integrations as bound and the model in use."""
    return cast(
        App,
        SimpleNamespace(_integrations=integrations, _model=lambda: ModelRef("p", model)),
    )


def every_tool(base: type[Tool]) -> list[type[Tool]]:
    """Every class below `base`, however deep."""
    found: list[type[Tool]] = []
    for sub in base.__subclasses__():
        found += [sub, *every_tool(sub)]
    return found
