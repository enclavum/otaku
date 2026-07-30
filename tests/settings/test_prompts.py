"""The template renderer's contract: `render` fills the given
placeholders in one pass, every other brace stays literal, substituted
text is never rescanned, and rendering cannot fail."""

from otaku.settings.prompts import render


class TestRender:
    def test_fills_the_given_placeholders(self) -> None:
        assert render("Hi {name}, {word}.", name="Ana", word="welcome") == "Hi Ana, welcome."

    def test_other_braces_stay_literal(self) -> None:
        template = 'Reply as {"scene": {"title": "..."}} for {name}'
        assert render(template, name="x") == 'Reply as {"scene": {"title": "..."}} for x'

    def test_an_unknown_placeholder_is_just_text(self) -> None:
        assert render("{name} and {unknown}", name="x") == "x and {unknown}"

    def test_substituted_text_is_never_rescanned(self) -> None:
        assert render("{a} {b}", a="{b}", b="two") == "{b} two"

    def test_repeated_placeholders_all_fill(self) -> None:
        assert render("{n}-{n}", n="x") == "x-x"

    def test_no_substitutions_return_the_template(self) -> None:
        assert render("{anything} stays") == "{anything} stays"
