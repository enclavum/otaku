"""Drive the providers package against one live provider and print
what happened — the observational half of notes/providers-testing.md,
where the smokes (scenarios/live) hold the promises. Whether a model
thinks at a level is the model's own appetite, so this prints it rather
than asserting it: per level, the fields that left, whether a thinking
echo came, the reply's start, the tokens, the seconds; a text
completion; the cat photo, and whether the answer says so.

usage: python scripts/live-matrix.py KIND MODEL [--url URL] [--levels a,b,c]
         [--vision-model M] [--completion-model M] [--no-completion]
         [--no-vision] [--max-tokens N] [--no-load] [--extra-model M:levels]

KIND is a section name (llamacpp, koboldcpp, ollama, omlx, lmstudio,
openrouter, nanogpt, generic); MODEL a model the provider lists, or
"first" for the first one listed. A cloud key is read from the engine's
environment variable. --extra-model plays another model's levels too.
"""

import argparse
import sys
import time
from pathlib import Path

from otaku.providers import (
    CLIENTS,
    DeclinedError,
    Image,
    ProviderConfig,
    ProviderError,
    Stats,
    Text,
    Thinking,
    probe,
)


class Log:
    def __init__(self):
        self.requests = []
        self.answers = []

    def record_request(self, provider, purpose, body):
        self.requests.append(body)
        return f"r{len(self.requests)}"

    def record_answer(self, provider, purpose, request_id, **kw):
        self.answers.append((request_id, kw["outcome"]))


# The photo every vision row is asked about (scenarios/fixtures/cat.jpg).
CAT = Path(__file__).resolve().parent.parent / "scenarios" / "fixtures" / "cat.jpg"
# A question a model may think about — the smoke's; a trivial one (17
# times 23) is answered without thinking by most models at any level.
PUZZLE = (
    "A farmer has 17 sheep. All but 9 run away. Then he buys twice as many "
    "as remain. How many sheep now? Give only the number."
)


class M:
    def __init__(self, role, body):
        self.role, self.body = role, body


def knobs(body):
    out = {}
    if "reasoning_effort" in body:
        out["effort"] = body["reasoning_effort"]
    if "chat_template_kwargs" in body:
        out["flag"] = body["chat_template_kwargs"].get("enable_thinking")
    return out or "none"


def run_turn(client, log, model, level, max_tokens):
    msgs = [
        M("system", "You are a careful assistant."),
        M("user", PUZZLE),
    ]
    before = len(log.requests)
    t0 = time.monotonic()
    thought, text, err = "", "", ""
    stats = None
    try:
        for c in client.complete_chat(
            model,
            msgs,
            {"max_tokens": max_tokens, "temperature": 0},
            think_level=level,
            watched=False,
        ):
            if isinstance(c, Thinking):
                thought += c.text
            elif isinstance(c, Text):
                text += c.text
            elif isinstance(c, Stats):
                stats = c
    except DeclinedError as e:
        err = f"DECLINED: {e}"
    except ProviderError as e:
        err = f"{type(e).__name__}: {e}"
    sent = [knobs(b) for b in log.requests[before:]]
    elapsed = time.monotonic() - t0
    tokens = f"{stats.prompt_tokens}/{stats.completion_tokens}" if stats else "-"
    cached = f" cached={stats.cached_tokens}" if stats and stats.cached_tokens else ""
    echo = f"yes({len(thought)})" if thought else "no"
    print(
        f"  think={level!s:7} sent={sent} thinking={echo:8}"
        f" text={text.strip()[:30]!r:34} tok={tokens}{cached} {elapsed:.1f}s {err}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind")
    ap.add_argument("model")
    ap.add_argument("--url")
    ap.add_argument("--levels", default="default,off,low,high,max")
    ap.add_argument("--vision-model")
    ap.add_argument("--completion-model")
    ap.add_argument("--no-completion", action="store_true")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--extra-model", action="append", default=[])
    a = ap.parse_args()

    cls = CLIENTS[a.kind]
    config = cls.autoconfigure()
    if a.url:
        config = ProviderConfig(name=config.name, url=a.url, api_key=config.api_key)
    log = Log()
    client = cls(config, request_sink=log, smooth=False)
    source = client.key_source.value if client.key_source else "none"
    print(f"== {a.kind} @ {config.url}  key={source}  locality={client.locality.value}")

    p = probe(config)
    key = p.key_source.value if p.key_source else "none"
    print(f"probe: {p.outcome.value} models={p.models_count} key={key} :: {p.message}")
    try:
        rows = client.models()
    except ProviderError as e:
        print("LISTING FAILED:", e)
        sys.exit(1)
    if a.model == "first":
        a.model = rows[0].name
    row = next((r for r in rows if r.name == a.model), None)
    print(f"listing: {len(rows)} rows; {a.model}: {row}")
    print(f"manages_models={client.manages_models}")
    print(f"balance: {client.balance(timeout=10.0)}")

    if not client.manages_models:
        try:
            client.load_model(a.model)
            print("load on a non-managing engine: no error?!")
        except ProviderError as e:
            print("load refused as expected:", e)
    elif not a.no_load:
        try:
            if row is not None and row.loaded:
                client.unload_model(a.model)
                print("unloaded:", not any(r.loaded and r.name == a.model for r in client.models()))
            client.load_model(a.model)
            print("loaded:", any(r.loaded and r.name == a.model for r in client.models()))
        except ProviderError as e:
            print("load/unload FAILED:", e)

    print(f"context: {client.get_context_size(a.model)}")
    row = client.model(a.model)
    print(f"capabilities: {row.capabilities if row is not None else None}")

    print("thinking levels:")
    for level in a.levels.split(","):
        run_turn(client, log, a.model, None if level == "default" else level, a.max_tokens)

    for spec in a.extra_model:
        model, _, levels = spec.partition(":")
        extra = client.model(model)
        print(f"extra model {model}: capabilities={extra.capabilities if extra else None}")
        for level in (levels or "default,off,high").split(","):
            run_turn(client, log, model, None if level == "default" else level, a.max_tokens)

    if not a.no_completion:
        model = a.completion_model or a.model
        for level in (None, "off", "high"):
            prompt = "The capital of France is" if level is None else f"Question: {PUZZLE}\nAnswer:"
            limit = 8 if level is None else max(a.max_tokens, 400)
            before = len(log.requests)
            t0 = time.monotonic()
            try:
                out = list(
                    client.complete_text(
                        model,
                        prompt,
                        {"max_tokens": limit, "temperature": 0},
                        think_level=level,
                        watched=False,
                    )
                )
                text = "".join(c.text for c in out if isinstance(c, Text))
                stats = out[-1] if out and isinstance(out[-1], Stats) else None
                tokens = f"{stats.prompt_tokens}/{stats.completion_tokens}" if stats else "-"
                sent = [knobs(b) for b in log.requests[before:]]
                elapsed = time.monotonic() - t0
                print(
                    f"completion ({model}) think={level!s:5} sent={sent}"
                    f" text={text.strip()[:40]!r} tok={tokens} {elapsed:.1f}s"
                )
            except ProviderError as e:
                print(f"completion ({model}) think={level!s:5} FAILED: {type(e).__name__}: {e}")

    if not a.no_vision:
        model = a.vision_model or a.model
        img = Image(CAT.read_bytes(), "image/jpeg")
        msgs = [M("system", "Answer with one word."), M("user", "What animal is this?")]
        t0 = time.monotonic()
        try:
            out = list(
                client.complete_chat(
                    model,
                    msgs,
                    {"max_tokens": 200, "temperature": 0},
                    think_level="off",
                    images=[img],
                    watched=False,
                )
            )
            text = "".join(c.text for c in out if isinstance(c, Text))
            seen = "cat" if "cat" in text.lower() or "kitten" in text.lower() else "NOT A CAT"
            print(f"vision ({model}): {seen} {text.strip()[:60]!r} {time.monotonic() - t0:.1f}s")
        except ProviderError as e:
            print(f"vision ({model}) FAILED: {type(e).__name__}: {e}")

    msgs = [
        M("system", "You are a careful assistant."),
        M("user", PUZZLE),
    ]
    print(
        f"counts: chat={client.count_chat_tokens(a.model, msgs)} "
        f"text={client.count_text_tokens(a.model, 'The capital of France is')} "
        f"(the engine counts: {client.counts_tokens})"
    )
    print("log outcomes:", [o for _, o in log.answers])


main()
