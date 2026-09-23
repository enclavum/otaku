"""The in-app edit of one prompt, its pure half: `with_prompt` is the
file's text with the key's value replaced whatever it holds, appended
where the file lacks the key, dropped when emptied; every other key
rides along untouched, and a key spelled inside another value is not the
key. Text in, text out — the promise is what the file parses back to,
so the cases compare values, never the spelling."""

import tomllib

from otaku.formatting import toml_string
from otaku.settings.prompts import with_prompt

QUESTIONS = "tool_questions_prompt"


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
