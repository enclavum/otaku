"""Text templates: a family's turn markers rendered around a story for
the text wire, the family read off an engine's word or a model's name,
and the one word chosen among the three."""

import pytest

from otaku.providers.openai.templates import (
    PLAIN,
    TEXT_TEMPLATES,
    TextTemplate,
    family_of_chat_template,
    family_of_config_type,
    family_of_name,
    resolve,
)


class TestRender:
    def test_the_story_is_the_models_open_turn_after_the_premise(self) -> None:
        rendered = TEXT_TEMPLATES["gemma4"].render("Be brief.", "Once upon")
        assert rendered == "<bos><|turn>system\nBe brief.<turn|>\n<|turn>model\nOnce upon"

    def test_a_family_without_a_system_role_puts_the_premise_in_a_user_turn(self) -> None:
        rendered = TEXT_TEMPLATES["gemma"].render("Be brief.", "Once")
        assert rendered == (
            "<bos><start_of_turn>user\nBe brief.<end_of_turn>\n<start_of_turn>model\nOnce"
        )

    def test_an_empty_premise_leaves_no_turn_before_the_story(self) -> None:
        assert TEXT_TEMPLATES["chatml"].render("  \n", "Once") == "<|im_start|>assistant\nOnce"

    def test_bos_is_spelled_only_where_the_family_says(self) -> None:
        assert TEXT_TEMPLATES["llama3"].render("", "Once").startswith("<|begin_of_text|>")
        assert TEXT_TEMPLATES["chatml"].render("", "Once").startswith("<|im_start|>")

    def test_plain_is_the_bare_text(self) -> None:
        assert PLAIN.render("Be brief.", "Once") == "Be brief.\n\nOnce"
        assert PLAIN.render("", "Once") == "Once"


class TestStops:
    def test_the_markers_that_end_or_open_a_turn_end_a_continuation(self) -> None:
        assert TEXT_TEMPLATES["gemma4"].stops == ("<turn|>", "<|turn>user", "<|turn>system")
        assert TEXT_TEMPLATES["chatml"].stops == (
            "<|im_end|>",
            "<|im_start|>user",
            "<|im_start|>system",
        )

    def test_a_family_without_a_system_role_has_no_system_marker_to_stop_at(self) -> None:
        assert TEXT_TEMPLATES["mistral-tekken"].stops == ("</s>", "[INST]")

    def test_plain_stops_nowhere(self) -> None:
        assert PLAIN.stops == ()


class TestTheFamilyOff:
    def test_the_engines_config_type_where_it_names_one_family(self) -> None:
        assert family_of_config_type("gemma4") == "gemma4"
        assert family_of_config_type("gemma4_text") == "gemma4"
        assert family_of_config_type("gemma3") == "gemma"
        assert family_of_config_type("qwen3") == "chatml"

    def test_a_config_type_that_covers_more_than_one_family_says_nothing(self) -> None:
        for kind in ("mistral", "llama", "phi3", "", None, 4):
            assert family_of_config_type(kind) is None, kind

    def test_a_chat_templates_own_marker(self) -> None:
        assert family_of_chat_template("{{ bos }}<|turn>user\n{{ text }}") == "gemma4"
        assert family_of_chat_template("<start_of_turn>user\n") == "gemma"
        assert family_of_chat_template("<|im_start|>system") == "chatml"
        assert family_of_chat_template("<|start_header_id|>user<|end_header_id|>") == "llama3"
        assert family_of_chat_template("[INST] {{ text }} [/INST]") == "mistral-tekken"
        assert family_of_chat_template("{{ text }}") is None
        assert family_of_chat_template(None) is None

    def test_the_models_name_as_the_last_resort(self) -> None:
        assert family_of_name("google/gemma-4-e4b-it") == "gemma4"
        assert family_of_name("g4-story-9b") == "gemma4"
        assert family_of_name("Gemma-3-27b") == "gemma"
        assert family_of_name("mistral-nemo-12b") == "mistral-tekken"
        assert family_of_name("mn-gutenberg-12b") == "mistral-tekken"
        assert family_of_name("Qwen3-8B") == "chatml"
        assert family_of_name("meta-llama/llama-3.1-8b") == "llama3"

    def test_a_name_that_spells_no_family(self) -> None:
        for name in ("mistral-7b", "phi-4", "glm4", "damn-fine", ""):
            assert family_of_name(name) is None, name


class TestResolve:
    def test_the_users_word_wins_over_the_engines_and_the_names(self) -> None:
        found = resolve("plain", "gemma4", "gemma-4-e4b")
        assert found is not None
        assert (found.name, found.source) == ("plain", "override")

    def test_an_unknown_word_stands_for_none_and_the_engine_is_asked(self) -> None:
        found = resolve("gemma5", "gemma4", "whatever")
        assert found is not None
        assert (found.name, found.source) == ("gemma4", "engine")
        found = resolve(7, "gemma", "whatever")
        assert found is not None
        assert (found.name, found.source) == ("gemma", "engine")

    def test_the_name_is_the_last_resort(self) -> None:
        found = resolve(None, None, "mn-gutenberg-12b")
        assert found is not None
        assert (found.name, found.source) == ("mistral-tekken", "model")

    def test_none_of_the_three_knows(self) -> None:
        assert resolve(None, None, "mistral-7b") is None
        assert resolve("", "llama", "phi-4") is None

    def test_the_resolved_copy_is_the_catalogues_template_with_its_source(self) -> None:
        found = resolve(None, "chatml", "x")
        assert found is not None
        chatml = TEXT_TEMPLATES["chatml"]
        assert found == TextTemplate(
            chatml.name, chatml.user, chatml.model, chatml.system, chatml.bos, source="engine"
        )
        # the catalogue's own carries no source: it was reached by none
        assert chatml.source == ""


@pytest.mark.parametrize("name", list(TEXT_TEMPLATES))
def test_every_template_is_named_by_its_key(name: str) -> None:
    assert TEXT_TEMPLATES[name].name == name
