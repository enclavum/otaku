"""How a frame is read: the deltas of each wire, the usage report, and a
frame that is trouble instead of content."""

from otaku.providers.openai.frames import chat_delta, completion_delta, read_usage, trouble


class TestChatDelta:
    def test_reads_content_and_reasoning_under_either_spelling(self) -> None:
        event = {"choices": [{"delta": {"content": "hi", "reasoning_content": "hm"}}]}
        assert chat_delta(event) == ("hm", "hi")
        event = {"choices": [{"delta": {"content": "hi", "reasoning": "hm"}}]}
        assert chat_delta(event) == ("hm", "hi")

    def test_absent_parts_are_empty(self) -> None:
        assert chat_delta({"choices": [{"delta": {"content": "hi"}}]}) == ("", "hi")
        assert chat_delta({"choices": [{"delta": {}}]}) == ("", "")
        assert chat_delta({"choices": []}) == ("", "")
        assert chat_delta({}) == ("", "")

    def test_a_null_content_is_empty_not_the_word_none(self) -> None:
        assert chat_delta({"choices": [{"delta": {"content": None}}]}) == ("", "")


class TestCompletionDelta:
    def test_reads_the_continuation_as_text(self) -> None:
        assert completion_delta({"choices": [{"text": "abc"}]}) == ("", "abc")

    def test_reads_the_reasoning_a_catalog_reports_beside_it(self) -> None:
        assert completion_delta({"choices": [{"text": "abc", "reasoning": "hm"}]}) == ("hm", "abc")

    def test_absent_parts_are_empty(self) -> None:
        assert completion_delta({"choices": [{}]}) == ("", "")
        assert completion_delta({}) == ("", "")


class TestTrouble:
    def test_a_servers_error_object_breaks_the_reply_off_in_its_words(self) -> None:
        event = {"error": {"message": "server overloaded", "type": "server_error"}}
        assert trouble(event) == "The reply broke off: server overloaded"

    def test_a_servers_error_string_too(self) -> None:
        assert trouble({"error": "boom"}) == "The reply broke off: boom"

    def test_a_refusal_delta_is_the_model_declining(self) -> None:
        event = {"choices": [{"delta": {"refusal": "I cannot help with that."}}]}
        assert trouble(event) == "The model declined: I cannot help with that."

    def test_a_stop_by_error_breaks_the_reply_off(self) -> None:
        event = {"choices": [{"delta": {}, "finish_reason": "error"}]}
        assert trouble(event) == "The reply broke off: the generation stopped with an error"

    def test_a_stop_by_content_filter_is_the_model_declining(self) -> None:
        event = {"choices": [{"delta": {}, "finish_reason": "content_filter"}]}
        assert trouble(event) == "The model declined: the reply was filtered"

    def test_content_is_no_trouble(self) -> None:
        assert trouble({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}) == ""
        assert trouble({"choices": [{"delta": {}, "finish_reason": "stop"}]}) == ""
        assert trouble({"error": None}) == ""
        assert trouble({}) == ""


class TestReadUsage:
    def test_the_three_counts_off_the_report(self) -> None:
        usage = {
            "prompt_tokens": 70,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 64},
        }
        assert read_usage({"usage": usage}) == (70, 5, 64)

    def test_no_cache_figure_is_none_not_zero(self) -> None:
        assert read_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 5}}) == (7, 5, None)

    def test_anthropics_spelling_of_the_cache_read_is_the_fallback(self) -> None:
        usage = {"prompt_tokens": 70, "completion_tokens": 5, "cache_read_input_tokens": 64}
        assert read_usage({"usage": usage}) == (70, 5, 64)

    def test_a_partial_report_states_only_what_it_states(self) -> None:
        assert read_usage({"usage": {"completion_tokens": 5}}) == (None, 5, None)

    def test_no_report_is_none(self) -> None:
        assert read_usage({"choices": [{"delta": {"content": "hi"}}]}) is None
        assert read_usage({"usage": None}) is None
        assert read_usage({"usage": {}}) is None
