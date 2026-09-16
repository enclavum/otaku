"""Thinking through every provider, live: `/set think none` plays a turn
with no thinking echo, a level plays or is refused as the provider allows,
and the request that left carries exactly the knobs the provider is
promised — read back from the state dir's own request log, the one
record of what left the machine. Marked `live`; each case skips itself
when its server is down or its key is not set, as the generic smokes do.

What is NOT asserted: that a level makes the model think. Whether it
does is the model's own choice per turn (Gemma 4 thinks on roughly half
of them), so the echo at a level would be a coin toss; the knobs on the
wire are the promise, and "none" is the one setting whose effect is
checked — no echo, on every provider that can stop at all.
"""

import json
import os
from pathlib import Path

import pytest

from otaku.backend.api import settings as api_settings
from otaku.providers.clients.omlx import OmlxClient
from otaku.settings.providers import ProviderConfig
from scenarios.support.live import case_key, case_model
from scenarios.support.live import live_app as build_app

pytestmark = pytest.mark.live

BOTH = frozenset({"reasoning_effort", "enable_thinking"})
EFFORT = frozenset({"reasoning_effort"})
FLAG = frozenset({"enable_thinking"})
TEMPLATE = frozenset({"enable_thinking", "template_reasoning_effort"})
# The engines that enforce a thinking budget of their own, each in its
# spelling: the flag and the word for the template, the budget for the
# sampler; KoboldCpp takes llama.cpp's budget as an alias.
LLAMACPP = TEMPLATE | {"thinking_budget_tokens"}
OMLX = TEMPLATE | {"thinking_budget"}
KOBOLDCPP = BOTH | {"thinking_budget_tokens"}
ALL = BOTH | LLAMACPP | OMLX  # the generic provider: permissive, every knob
# (provider, url, the env var of its key or "", the model named or "",
# the knobs the provider is promised — the completion half's
# `chat_reasoning_knobs`). The generic case is a llama-server reached
# through the protocol alone: the setup the "think none does nothing"
# report came from.
CASES = [
    ("llamacpp", "http://127.0.0.1:8080/v1", "", "", LLAMACPP),
    ("generic", "http://127.0.0.1:8080/v1", "", "", ALL),
    ("koboldcpp", "http://127.0.0.1:5001/v1", "", "", KOBOLDCPP),
    (
        "ollama",
        "http://127.0.0.1:11434/v1",
        "",
        os.environ.get("OTAKU_TEST_MODEL", "ollama/gemma3").partition("/")[2],
        EFFORT,
    ),
    (
        "omlx",
        os.environ.get("OTAKU_LIVE_OMLX_URL", OmlxClient.autoconfigure().url),
        "",
        os.environ.get("OTAKU_LIVE_OMLX_MODEL", ""),
        OMLX,
    ),
    (
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        "LMSTUDIO_API_KEY",
        os.environ.get("OTAKU_LIVE_LMSTUDIO_MODEL", ""),
        EFFORT,
    ),
    (
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        os.environ.get("OTAKU_LIVE_OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        EFFORT,
    ),
    (
        "nanogpt",
        "https://nano-gpt.com/api/v1",
        "NANOGPT_API_KEY",
        os.environ.get("OTAKU_LIVE_NANOGPT_MODEL", "gpt-4o-mini"),
        EFFORT,
    ),
]
_REQUIRED_KEYS = {"OPENROUTER_API_KEY", "NANOGPT_API_KEY"}
_PARAMS = [c[:4] for c in CASES]
_KNOBS = {c[0]: c[4] for c in CASES}
_IDS = [c[0] for c in CASES]


class TestThink:
    @pytest.mark.parametrize(("provider", "url", "key_var", "model"), _PARAMS, ids=_IDS)
    def test_off_plays_without_a_thinking_echo_and_says_so_on_the_wire(
        self, tmp_path: Path, server, capsys, provider: str, url: str, key_var: str, model: str
    ) -> None:  # type: ignore[no-untyped-def]
        app = _open(tmp_path, server, provider, url, key_var, model)
        try:
            off, _ = _levels(app)
            app.play(f"/set think {off}")
            assert app.session.think == off
            capsys.readouterr()
            app.play("Reply with one word: ready?")
            out = capsys.readouterr().out
            assert _replied(app)
            assert "(thinking)" not in out
            assert _carried(app, provider) == _expected(_KNOBS[provider], off)
        finally:
            app.close()

    @pytest.mark.parametrize(("provider", "url", "key_var", "model"), _PARAMS, ids=_IDS)
    def test_a_level_is_taken_and_carried_on_the_knobs_the_engine_reads(
        self, tmp_path: Path, server, provider: str, url: str, key_var: str, model: str
    ) -> None:  # type: ignore[no-untyped-def]
        app = _open(tmp_path, server, provider, url, key_var, model)
        try:
            _, on = _levels(app)
            app.play(f"/set think {on}")
            # Never refused for the provider's sake: a provider with no knob
            # takes the level too, and its request carries nothing.
            assert app.session.think == on
            app.play("Reply with one word: ready?")
            assert _replied(app)
            assert _carried(app, provider) == _expected(_KNOBS[provider], on)
        finally:
            app.close()


def _open(tmp_path: Path, server, provider: str, url: str, key_var: str, model: str):  # type: ignore[no-untyped-def]
    key = case_key(provider, key_var, _REQUIRED_KEYS)
    model = case_model(provider, url, key, model)
    return build_app(tmp_path, server, ProviderConfig(name=provider, url=url, api_key=key), model)


def _replied(app) -> bool:  # type: ignore[no-untyped-def]
    chain = app.store.stories.get_messages(app.session.story_id)
    return len(chain) >= 2 and chain[-1].role == "assistant" and bool(chain[-1].body.strip())


def _levels(app) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """(the off, the on) the model in use takes: the switch's two words
    where the provider says it switches, else the ladder's none and
    high — every rung where the provider cannot say. A model that takes
    no level (Ollama's Gemma 3, whose card names no thinking) has
    nothing to smoke: the case skips, and names a model that does."""
    words = api_settings.think_choices(app.session).levels
    if words == ("unset",):
        pytest.skip(f"{app.session.model} takes no thinking level (OTAKU_LIVE_<PROVIDER>_MODEL)")
    return ("off", "on") if "off" in words else ("none", "high")


def _expected(knobs: frozenset[str], level: str) -> dict[str, object]:
    """What the promised knobs spell for `level` on the wire: off as
    none on every knob, on as the flag alone, a rung by name."""
    off = level in ("none", "off")
    word = "none" if off else None if level == "on" else level
    out: dict[str, object] = {}
    if "reasoning_effort" in knobs and word:
        out["reasoning_effort"] = word
    template: dict[str, object] = {}
    if "enable_thinking" in knobs:
        template["enable_thinking"] = not off
    if "template_reasoning_effort" in knobs and word:
        template["reasoning_effort"] = word
    if template:
        out["chat_template_kwargs"] = template
    # A budget knob carries 0 for off and nothing for a rung or on.
    for budget in ("thinking_budget_tokens", "thinking_budget"):
        if budget in knobs and off:
            out[budget] = 0
    return out


def _carried(app, provider: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """The thinking fields on the LAST turn request that left for
    `provider`, from the state dir's request log. A 400 on the knob makes
    otaku send the turn again without it, so the turn's FIRST request is
    the one read: the knobs are what left, whatever the provider said."""
    bodies = []
    for path in sorted(app.paths.logs_dir.glob("requests-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            body = entry.get("body") or {}
            if entry.get("provider") == provider and "messages" in body:
                bodies.append(body)
    assert bodies, "no turn request was logged"
    turn = bodies[-1]["messages"][-1]["content"]
    first = next(b for b in bodies if b["messages"][-1]["content"] == turn)
    knobs = (
        "reasoning_effort",
        "chat_template_kwargs",
        "thinking_budget_tokens",
        "thinking_budget",
    )
    return {k: first[k] for k in knobs if k in first}
