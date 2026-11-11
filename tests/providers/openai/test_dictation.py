"""The dictation half's class knowledge: the recordings each engine's
transcription endpoint decodes — a WAV everywhere, more where an engine
says so — and which half each client builds."""

import pytest

from otaku.providers import ALL_CLIENTS, OpenAIDictation
from otaku.providers.clients.llamacpp import LlamaCppDictation
from otaku.providers.clients.nanogpt import NanoGptDictation
from otaku.providers.clients.openrouter import OpenRouterDictation


class TestFormats:
    def test_the_base_takes_a_wav_alone(self) -> None:
        assert OpenAIDictation.formats == {"wav"}

    @pytest.mark.parametrize(
        ("half", "formats"),
        [
            (LlamaCppDictation, {"wav", "mp3", "flac"}),
            (OpenRouterDictation, {"wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"}),
            (NanoGptDictation, {"wav", "mp3", "ogg", "m4a", "aac"}),
        ],
    )
    def test_an_engine_that_takes_more_says_so(
        self, half: type[OpenAIDictation], formats: set[str]
    ) -> None:
        assert half.formats == formats

    @pytest.mark.parametrize("client", sorted(ALL_CLIENTS))
    def test_every_engine_takes_a_wav(self, client: str) -> None:
        assert "wav" in ALL_CLIENTS[client].dictation_class.formats


class TestHalves:
    @pytest.mark.parametrize(
        ("client", "half"),
        [
            ("llamacpp", LlamaCppDictation),
            ("openrouter", OpenRouterDictation),
            ("nanogpt", NanoGptDictation),
            ("ollama", OpenAIDictation),
            ("koboldcpp", OpenAIDictation),
            ("omlx", OpenAIDictation),
        ],
    )
    def test_each_client_builds_its_engines_half(
        self, client: str, half: type[OpenAIDictation]
    ) -> None:
        assert ALL_CLIENTS[client].dictation_class is half
