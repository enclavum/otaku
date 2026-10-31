"""The /set vocabulary: its constants, which the frontends read through
`api.settings` — pinned to the file vocabulary they order, because the
two live in different layers and only this keeps them one thing — and
its pure edges, how stop strings are typed and read back and how a value
is shown. And the model in use's one choice of wire (`ModelFacts`): the
template a story's request is rendered in, over the text wire alone."""

import pytest

from otaku.backend.app.models.services import (
    EFFORT_LEVELS,
    PARAMETERS,
    THINK_MENU,
    ModelFacts,
    parameter_text,
    parse_stops,
)
from otaku.providers.openai.completion import PROTOCOL_PARAMS
from otaku.providers.openai.templates import PLAIN, TEXT_TEMPLATES, TextTemplate
from otaku.providers.registry import ALL_CLIENTS
from otaku.settings.models import THINK_UNSET
from otaku.store.schema import Layout


class TestThinkVocabulary:
    def test_the_menu_is_the_wires_ladder_behind_the_way_out(self) -> None:
        # A level the wire honours that the menu lacked could be reported
        # by /info and never set; there is one ladder, the wire's.
        assert (THINK_UNSET, *EFFORT_LEVELS) == THINK_MENU
        assert THINK_UNSET not in EFFORT_LEVELS

    def test_default_leads_the_menu(self) -> None:
        # The way out comes first, as the docstring promises.
        assert THINK_MENU[0] == THINK_UNSET


class TestParamBounds:
    def test_a_bound_is_a_numbers_and_its_floor_is_under_its_ceiling(self) -> None:
        # A bound on the stop string could never be checked; a floor
        # above its ceiling could never be met.
        for name, parameter in PARAMETERS.items():
            if parameter.low is None:
                continue
            assert parameter.kind in (int, float), name
            assert parameter.high is None or parameter.low <= parameter.high, name

    def test_the_unbounded_are_the_seed_and_the_stop(self) -> None:
        unbounded = {name for name, parameter in PARAMETERS.items() if parameter.low is None}
        assert unbounded == {"seed", "stop"}


class TestParamsVocabulary:
    def test_every_provider_declares_what_it_reads_in_the_set_vocabulary(self) -> None:
        # A declaration outside the vocabulary names a parameter nobody
        # can set; one short of the protocol's own denies what every
        # OpenAI endpoint reads — but for the penalties, of which an
        # engine may have one sampler (KoboldCpp's presence term).
        for kind, client_class in ALL_CLIENTS.items():
            declared = client_class.completion_class.supported_params
            assert declared <= set(PARAMETERS), kind
            assert PROTOCOL_PARAMS - {"presence_penalty", "frequency_penalty"} <= declared, kind


class TestParseStops:
    def test_a_bare_text_is_one_stop_as_it_is(self) -> None:
        assert parse_stops("END") == ["END"]
        assert parse_stops("  The End  ") == ["The End"]

    def test_quoted_stops_are_several_and_may_hold_a_newline(self) -> None:
        assert parse_stops('"\\nUser:" "END"') == ["\nUser:", "END"]
        assert parse_stops('"a b"   "c"') == ["a b", "c"]

    def test_an_open_quote_or_an_empty_stop_is_refused(self) -> None:
        for raw in ('"unclosed', '"" "x"', "", '"a" b'):
            with pytest.raises(ValueError):
                parse_stops(raw)

    def test_what_is_shown_reads_back_the_same(self) -> None:
        stops = ["\nUser:", "END", 'say "no"']
        assert parse_stops(parameter_text(stops)) == stops


class TestParameterText:
    def test_a_number_is_itself(self) -> None:
        assert parameter_text(0.7) == "0.7"
        assert parameter_text(40) == "40"

    def test_stops_are_json_strings_apart(self) -> None:
        assert parameter_text(["\nUser:", "END"]) == '"\\nUser:" "END"'


class TestWireTemplate:
    """The template the turn and its preview render a story's request in:
    the model's own, where the layout goes over the text wire; none where
    it goes as the chat."""

    def test_the_novel_on_a_model_that_completes_text_is_rendered_in_its_template(self) -> None:
        assert facts(PLAIN).wire_template(Layout.NOVEL) is PLAIN

    def test_every_other_layout_goes_as_the_chat(self) -> None:
        for layout in (Layout.CANVAS, Layout.MANUSCRIPT, Layout.CHAT):
            assert facts(PLAIN).wire_template(layout) is None, layout

    def test_the_novel_on_a_model_that_cannot_complete_text_goes_as_the_chat(self) -> None:
        assert facts(TEXT_TEMPLATES["gemma4"]).wire_template(Layout.NOVEL) is None
        assert facts(PLAIN, text_completion=False).wire_template(Layout.NOVEL) is None


def facts(template: TextTemplate | None, *, text_completion: bool = True) -> ModelFacts:
    """The model in use as far as the wire is concerned: whether its engine
    serves the text wire, and the template it renders in."""
    return ModelFacts(
        model=None,
        client=None,
        row=None,
        vision=False,
        text_completion=text_completion,
        text_template=template,
        on_cloud=False,
    )
