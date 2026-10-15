"""What a request carries and what an answer is made of
(`web.api._request`), with no HTTP in sight.

`Ask` reads a request as a handler needs it: a field the request must
carry, a JSON boolean or integer — anything else a malformed request,
which the server answers 400 (a `KeyError`, `TypeError` or `ValueError`)
— and the query's flags and ids. `etag` is a story's head as the tag a
write names back; `plain` a representation as JSON writes it, and
`changed` a write's answer.
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest

from otaku.backend.api import Changed, InjectionPosition
from otaku.formatting import Money
from otaku.web.api._request import (
    Ask,
    changed,
    checked_integer,
    checked_strings,
    etag,
    plain,
)

MALFORMED = (KeyError, TypeError, ValueError)


class TestAsk:
    def test_a_path_id_is_a_number(self) -> None:
        assert Ask(params={"story": "12"}).id("story") == 12

    def test_a_field_left_out_reads_as_its_default(self) -> None:
        assert Ask(body={"title": "Rain"}).text("title") == "Rain"
        assert Ask().text("title") == ""
        assert Ask().text("title", "Untitled") == "Untitled"

    def test_a_needed_field_left_out_or_null_is_malformed(self) -> None:
        assert Ask(body={"text": "x"}).need("text") == "x"
        with pytest.raises(MALFORMED):
            Ask().need("text")
        # coerced, a null would store the literal "None"
        with pytest.raises(MALFORMED):
            Ask(body={"text": None}).need("text")

    def test_a_flag_is_a_json_boolean_and_nothing_else(self) -> None:
        assert Ask(body={"on": True}).flag("on") is True
        for value in ("true", 1, None):
            with pytest.raises(MALFORMED):
                Ask(body={"on": value}).flag("on")

    def test_an_integer_is_a_json_integer_and_never_a_boolean(self) -> None:
        assert Ask(body={"head": 7}).integer("head") == 7
        for value in (True, "7", None, 7.0):
            with pytest.raises(MALFORMED):
                Ask(body={"head": value}).integer("head")

    def test_strings_are_a_json_list_of_strings_or_none_given(self) -> None:
        assert Ask(body={"aliases": ["a", "b"]}).strings("aliases") == ("a", "b")
        assert Ask().strings("aliases") == ()
        for value in ("a", ["a", 1], None):
            with pytest.raises(MALFORMED):
                Ask(body={"aliases": value}).strings("aliases")

    def test_a_query_flag_is_true_or_false_or_its_default(self) -> None:
        assert Ask(query={"probe": "false"}).query_flag("probe", True) is False
        assert Ask(query={"probe": "true"}).query_flag("probe", False) is True
        assert Ask().query_flag("probe", True) is True
        with pytest.raises(MALFORMED):
            Ask(query={"probe": "no"}).query_flag("probe", True)

    def test_a_query_id_is_digits_or_absent(self) -> None:
        assert Ask(query={"story": "5"}).query_id("story") == 5
        assert Ask().query_id("story") is None
        with pytest.raises(MALFORMED):
            Ask(query={"story": "five"}).query_id("story")


class TestChecked:
    """A value the body holds anywhere — inside an object as well as at
    its top — checked for its shape the way `Ask` checks a field."""

    def test_an_integer_is_a_json_integer_and_never_a_boolean(self) -> None:
        assert checked_integer(3, "reminder_depth") == 3
        for value in (True, "3", None, 3.0):
            with pytest.raises(MALFORMED):
                checked_integer(value, "reminder_depth")

    def test_strings_are_a_json_list_of_strings(self) -> None:
        assert checked_strings(["x: 1"], "examples") == ("x: 1",)
        for value in ("x: 1", ["x", 1], None):
            with pytest.raises(MALFORMED):
                checked_strings(value, "examples")


class TestEtag:
    def test_the_tag_is_the_head_quoted_and_zero_for_none(self) -> None:
        assert etag(41) == '"41"'
        assert etag(None) == '"0"'


class TestPlain:
    def test_a_dataclass_is_an_object_of_its_fields_all_the_way_down(self) -> None:
        assert plain(_Row(name="Mara", tags=("a", "b"), shade=_Shade.DARK)) == {
            "name": "Mara",
            "tags": ["a", "b"],
            "shade": "dark",
        }

    def test_a_set_is_an_array_of_its_members_sorted(self) -> None:
        assert plain(frozenset({"note", "question"})) == ["note", "question"]

    def test_a_mapping_is_keyed_by_its_keys_plain_form(self) -> None:
        assert plain({_Shade.DARK: 1}) == {"dark": 1}

    def test_a_datetime_is_iso_8601(self) -> None:
        assert plain(datetime(2026, 10, 10, 12, 30)) == "2026-10-10T12:30:00"

    def test_a_position_says_its_value_and_its_name(self) -> None:
        assert plain(InjectionPosition()) == {"value": "system", "text": "system"}
        assert plain(InjectionPosition(1)) == {"value": 1, "text": "2nd last"}

    def test_money_keeps_its_figure_a_string(self) -> None:
        # a decimal must not become a float
        found = plain(Money(Decimal("12.50"), "USD"))
        assert (found["amount"], found["currency"]) == ("12.50", "USD")
        assert found["text"] == str(Money(Decimal("12.50"), "USD"))

    def test_a_value_json_cannot_say_is_refused(self) -> None:
        with pytest.raises(TypeError):
            plain(object())


class TestChanged:
    def test_a_write_answers_the_thing_as_it_stands_and_the_sentence(self) -> None:
        assert changed(Changed(_Row("Mara", (), _Shade.DARK), "Renamed.")) == {
            "value": {"name": "Mara", "tags": [], "shade": "dark"},
            "notice": "Renamed.",
        }

    def test_where_nothing_stands_the_value_is_null(self) -> None:
        assert changed(Changed(None, "Deleted.")) == {"value": None, "notice": "Deleted."}


class _Shade(enum.Enum):
    DARK = "dark"


@dataclass(frozen=True)
class _Row:
    name: str
    tags: tuple[str, ...]
    shade: _Shade
