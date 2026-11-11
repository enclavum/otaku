"""The dictation half of a client: a recording posted to the engine's
transcription endpoint — the OpenAI form, a file and a model's name, which
every engine here takes — and the text it answers with; and which
recordings the engine decodes, by extension (`formats`), class knowledge an
engine sets where it takes more than a WAV.
"""

from typing import ClassVar

from otaku.providers.errors import ProviderError
from otaku.providers.http import DICTATION_TIMEOUT, Http
from otaku.settings.providers import ProviderConfig


class OpenAIDictation:
    # ---------- class knowledge: what the engine's endpoint decodes ----------

    # The recordings it takes, by extension: a WAV everywhere — Ollama's
    # reads nothing else — more where an engine says so.
    formats: ClassVar[frozenset[str]] = frozenset({"wav"})

    def __init__(self, config: ProviderConfig, http: Http) -> None:
        self._config = config
        self._http = http

    def transcribe(
        self, model: str, audio: bytes, audio_format: str, timeout: float = DICTATION_TIMEOUT
    ) -> str:
        """What is said in `audio`, a recording in `audio_format` ("wav"),
        as `model` writes it down. Raises the error family, an answer
        without its text included."""
        http = self._http.within(timeout, "dictation")
        data = http.upload(
            f"{self._config.url}/audio/transcriptions",
            {"model": model},
            (f"dictation.{audio_format}", audio, f"audio/{audio_format}"),
        )
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            error = ProviderError(f"The answer from {self._config.name} carries no text.")
            http.record(error)
            raise error
        return text.strip()
