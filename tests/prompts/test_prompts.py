"""The templates: each built-in filed under its key with the type first
(`file_of`), read as its file holds it but for the one final newline an
editor leaves (`read_template`), and every one a field of `Prompts`; and
the in-app edit of one prompt, its pure half: `with_prompt` is the file's
text with the key's value replaced whatever it holds, appended where the
file lacks the key, dropped when emptied; every other key rides along
untouched, and a key spelled inside another value is not the key. Text
in, text out — the promise is what the file parses back to, so the cases
compare values, never the spelling."""

import tomllib
from dataclasses import fields
from importlib.resources.abc import Traversable
from typing import cast

from otaku.formatting import toml_string
from otaku.prompts import DEFAULTS, Prompts, file_of, read_template, with_prompt

QUESTIONS = "tool_questions_prompt"


class TestFileOf:
    def test_the_type_comes_first(self) -> None:
        assert file_of("card_framing") == "framing_card"
        assert file_of("extract_prompt") == "prompt_extract"
        assert file_of("recap_header") == "header_recap"

    def test_a_name_of_several_words_keeps_them(self) -> None:
        assert file_of("tool_questions_prompt") == "prompt_tool_questions"


class TestReadTemplate:
    def test_the_final_newline_an_editor_leaves_is_not_the_template_s(self) -> None:
        assert read_template(_file("Say it.\n")) == "Say it."
        assert read_template(_file("Say it.")) == "Say it."

    def test_a_newline_of_the_template_s_own_stays(self) -> None:
        assert read_template(_file("Say it.\n\n")) == "Say it.\n"
        assert read_template(_file("\nSay it.\n")) == "\nSay it."


class TestPrompts:
    def test_each_field_reads_its_own_template(self) -> None:
        # the key a field's text is read by is the field's own name
        assert [f.metadata["key"] for f in fields(Prompts)] == [f.name for f in fields(Prompts)]

    def test_a_built_in_holds_every_placeholder_its_template_requires(self) -> None:
        lacking = [
            (f.name, placeholder)
            for f in fields(Prompts)
            for placeholder in f.metadata["required"]
            if "{" + placeholder + "}" not in DEFAULTS[f.name]
        ]
        assert lacking == []

    def test_the_defaults_are_the_fields_in_their_order(self) -> None:
        assert list(DEFAULTS) == [f.name for f in fields(Prompts)]


class TestWithPrompt:
    def test_the_value_is_replaced_whatever_the_file_holds(self) -> None:
        text = stub("extract_prompt", "You are a harsh critic.\nRead the scene.")
        edited = with_prompt(text, "extract_prompt", "NEW\nTEXT")
        assert tomllib.loads(edited)["extract_prompt"] == "NEW\nTEXT"
        assert edited.count("extract_prompt = ") == 1

    def test_a_one_line_value_is_replaced_too(self) -> None:
        edited = with_prompt(stub(QUESTIONS, "one line"), QUESTIONS, "It's new")
        assert tomllib.loads(edited)[QUESTIONS] == "It's new"

    def test_a_key_the_file_lacks_is_appended(self) -> None:
        text = stub("me_framing", "((OOC: I am {name}.))")
        edited = with_prompt(text, QUESTIONS, "Ask.")
        assert tomllib.loads(edited) == {"me_framing": "((OOC: I am {name}.))", QUESTIONS: "Ask."}

    def test_a_file_that_is_empty_takes_the_key(self) -> None:
        assert tomllib.loads(with_prompt("", QUESTIONS, "Ask.")) == {QUESTIONS: "Ask."}

    def test_an_empty_value_drops_the_key(self) -> None:
        text = stub("me_framing", "x") + stub(QUESTIONS, "Ask.\nTwice.")
        assert tomllib.loads(with_prompt(text, QUESTIONS, "")) == {"me_framing": "x"}

    def test_other_keys_ride_along_untouched(self) -> None:
        text = stub("me_framing", "x") + stub(QUESTIONS, "Ask.") + stub("you_framing", "y")
        assert tomllib.loads(with_prompt(text, QUESTIONS, "Z")) == {
            "me_framing": "x",
            QUESTIONS: "Z",
            "you_framing": "y",
        }

    def test_a_key_spelled_inside_another_value_is_not_the_key(self) -> None:
        inside = f"first line\n{QUESTIONS} = decoy\nlast line"
        text = stub("extract_prompt", inside) + stub(QUESTIONS, "Ask.")
        assert tomllib.loads(with_prompt(text, QUESTIONS, "Z")) == {
            "extract_prompt": inside,
            QUESTIONS: "Z",
        }

    def test_the_edit_is_idempotent(self) -> None:
        once = with_prompt(stub(QUESTIONS, "Ask."), QUESTIONS, "Z")
        assert with_prompt(once, QUESTIONS, "Z") == once


def stub(key: str, value: str) -> str:
    """A prompts.toml block the way `write_stub` writes one."""
    return f"{key} = {toml_string(value)}\n\n"


class _File:
    """A template file as `read_template` reads one."""

    def __init__(self, text: str) -> None:
        self.text = text

    def read_text(self, encoding: str) -> str:
        return self.text


def _file(text: str) -> Traversable:
    return cast(Traversable, _File(text))
