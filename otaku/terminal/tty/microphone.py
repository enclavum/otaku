"""The microphone: what the reader says, held in memory from the moment a
recording starts and handed back as one WAV when it stops — dictation's,
at the prompt (`prompt.dictation`) — and, while it runs, how loud each
band of pitch is now (`take_bands`), what an equalizer draws. Through
PortAudio (`sounddevice`), imported at the first recording: a machine
without PortAudio loses dictation and nothing else."""

import cmath
import io
import math
import threading
import wave
from collections.abc import Sequence
from typing import Any

# What speech recognition is trained on; an engine resamples anything else.
_RATE = 16_000
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # bytes: 16-bit samples
_FULL_SCALE = 32_768

# The spectrum: read over the last `_SPAN` samples (32 ms at 16 kHz, a
# power of two for the FFT), Hann-windowed, in bands of equal importance to
# understanding speech — ANSI S3.5-1997's critical-band table (the Speech
# Intelligibility Index), split in equal shares from 200 Hz, above mains
# hum, to 7.7 kHz. A band's level is its loudest bin in decibels below a
# full-scale tone, read between the band's own floor — the room under it
# reads as nothing — and its ceiling, which the sounds of speech reach as
# often as they are used: both measured on the author's microphone and
# room, from English and Russian words read aloud. The page's twin, nine
# bands: `js/kit/microphone.js`.
_SPAN = 512
_HANN = tuple(0.5 - 0.5 * math.cos(2 * math.pi * i / (_SPAN - 1)) for i in range(_SPAN))
_REFERENCE = _FULL_SCALE * _SPAN / 4  # a full-scale tone's bin through the window
_BANDS = (  # (low Hz, high Hz, floor dB, ceiling dB)
    (200, 426, -72.4, -40.0),
    (426, 588, -75.2, -41.0),
    (588, 779, -76.2, -44.2),
    (779, 996, -80.4, -51.5),
    (996, 1249, -84.5, -60.3),
    (1249, 1553, -86.9, -63.3),
    (1553, 1920, -91.0, -65.7),
    (1920, 2369, -97.3, -69.5),
    (2369, 2944, -99.4, -73.0),
    (2944, 3676, -99.6, -69.9),
    (3676, 4818, -100.8, -71.1),
    (4818, 7700, -101.6, -73.2),
)
BAND_COUNT = len(_BANDS)


class MicrophoneError(Exception):
    """The microphone could not be opened; str(e) is the sentence."""


class Recording:
    """One recording, running from the moment it is made until `stop`.
    PortAudio fills it on a thread of its own."""

    def __init__(self) -> None:
        try:
            import sounddevice  # type: ignore[import-untyped]
        except (ImportError, OSError) as e:
            # Linux ships no PortAudio inside the package: the import
            # fails until the system's is installed.
            raise MicrophoneError("Dictation needs PortAudio, which is not installed.") from e
        self._chunks: list[bytes] = []
        self._tail = b""  # the last `_SPAN` samples heard, for the spectrum
        self._lock = threading.Lock()
        self._rate = _RATE
        try:
            self._stream = self._open(sounddevice)
        except sounddevice.PortAudioError as e:
            raise MicrophoneError(f"The microphone could not be opened: {e}") from e
        self._stream.start()

    def stop(self) -> bytes:
        """The recording as a WAV file's bytes, the microphone released."""
        self.discard()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(_CHANNELS)
            out.setsampwidth(_SAMPLE_WIDTH)
            out.setframerate(self._rate)
            with self._lock:
                out.writeframes(b"".join(self._chunks))
        return buffer.getvalue()

    def take_bands(self) -> list[float]:
        """How loud each of the `BAND_COUNT` bands of pitch is now, low to
        high, from 0 — silence, or nothing coming in — to 1."""
        with self._lock:
            tail = self._tail
        samples = memoryview(tail).cast("h")
        if len(samples) < _SPAN:
            return [0.0] * BAND_COUNT
        magnitudes = _magnitudes([s * w for s, w in zip(samples, _HANN, strict=True)])
        per_bin = self._rate / _SPAN
        levels = []
        for low, high, floor, ceiling in _BANDS:
            first = int(low / per_bin)
            loudest = max(magnitudes[first : max(first + 1, int(high / per_bin))])
            if loudest <= 0:
                levels.append(0.0)
                continue
            decibels = 20 * math.log10(loudest / _REFERENCE)
            levels.append(max(0.0, min(1.0, (decibels - floor) / (ceiling - floor))))
        return levels

    def discard(self) -> None:
        """The microphone released, what was said kept for `stop`; again
        is nothing."""
        if not self._stream.closed:
            self._stream.stop()
            self._stream.close()

    def _open(self, sounddevice: Any) -> Any:
        """An input stream on the default device at the rate speech is
        read at — or, on a device that will not resample, at its own."""
        try:
            return self._stream_at(sounddevice, _RATE)
        except sounddevice.PortAudioError:
            device = sounddevice.query_devices(kind="input")
            self._rate = int(device["default_samplerate"])
            return self._stream_at(sounddevice, self._rate)

    def _stream_at(self, sounddevice: Any, rate: int) -> Any:
        return sounddevice.RawInputStream(
            samplerate=rate, channels=_CHANNELS, dtype="int16", callback=self._take
        )

    def _take(self, data: Any, _frames: int, _time: Any, _status: Any) -> None:
        chunk = bytes(data)
        with self._lock:
            self._chunks.append(chunk)
            self._tail = (self._tail + chunk)[-_SPAN * _SAMPLE_WIDTH :]


def _magnitudes(samples: Sequence[float]) -> list[float]:
    """How strong each frequency is in `samples` — a power of two of
    them — from none up to half the rate: a radix-2 FFT, in place."""
    n = len(samples)
    bits = n.bit_length() - 1
    data = [complex(samples[int(f"{i:0{bits}b}"[::-1], 2)]) for i in range(n)]
    size = 2
    while size <= n:
        half = size // 2
        step = cmath.exp(-2j * cmath.pi / size)
        for start in range(0, n, size):
            twiddle = 1 + 0j
            for k in range(start, start + half):
                odd = twiddle * data[k + half]
                data[k + half] = data[k] - odd
                data[k] += odd
                twiddle *= step
        size *= 2
    return [abs(value) for value in data[: n // 2]]
