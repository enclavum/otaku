#!/usr/bin/env python
"""A fake llama.cpp for looking at the transcript by hand: `make test-model`.

It answers as llama.cpp does, on llama.cpp's default port, so the
`[llamacpp]` section a first run writes points here with nothing changed:
two models, as llama.cpp's router serves several, both loaded, each a 32K
window — `otaku-test-chat`, an instruct model as most are, and
`otaku-test-text`, the base model the novel's text wire is for once one
hand line says so: `text_template = "plain"` under `[otaku-test-text]` in
configs/models.toml. Pick one in the model picker, switch on the story's
tools (questions, notes, system info, web search) on the Tools tab, and
play.

A line that starts with digits chooses what the reply carries — they
combine, `13 I open the door` — and any other line gets a paragraph:

    1  thinking, streamed before the reply
    2  a note
    3  a question with options, ending the reply (answered, it is a past one)
    4  system info: otaku runs it and the reply goes on after its turn
    5  web search: the same (without a key, its turn is the failure sentence)
    6  a long reply: paragraphs, with dialogue
    7  an opening inline: a leading space
    8  an opening with a paragraph break
    9  an opening flush: after an unfinished line, otaku adds the space
    0  everything: thinking, prose, a note, both tools, then a question

A line that starts with `-1` fails instead: the engine answers 500 before
a word arrives, and the reply stops on the failure.

The digits stay in the story — the server cannot change what is stored.
Both models answer alike, on either wire. Ctrl+C stops it.
"""

import argparse
import random
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios.support.server import ModelServer, content_text

CHAT_MODEL = "otaku-test-chat"
TEXT_MODEL = "otaku-test-text"
WINDOW = 32768

_DIGITS = re.compile(r"\s*(\d+)(?:\s|$)")
_FAIL = re.compile(r"\s*-1(?:\s|$)")
_TOOL_CALL = re.compile(r"```otk-(system-info|web-search)\b")

PARAGRAPHS = (
    "The rain had not stopped since noon. It ran down the shutters in sheets and pooled in the "
    "ruts of the yard, and somewhere past the barn a gate kept knocking against its post.",
    '"You came back," the ferryman said, without turning. "I did not think you would." He coiled '
    "the wet rope over his arm, loop by loop, as if the counting mattered.",
    "Lanterns hung along the quay, each one swinging a little out of time with the others. The "
    "water under them was black and very still, and it smelled of tar and cold iron.",
    "She unfolded the map on the table and smoothed it flat with both hands. The river on it bent "
    "twice where the real one bent once, and someone had inked a small cross beside the weir.",
    '"Three nights," said the innkeeper. "Three nights and not a word from the hill road." She '
    "set the cup down harder than she meant to, and the tea slopped over the rim.",
    "Wind came off the marsh and carried the bells with it, faint and broken, from the chapel no "
    "one had kept for years. The dog lifted its head, listened, and lay down again.",
)
CONTINUATIONS = (
    "door gave under her hand, and the cold came in with the smell of the river.",
    "last of the light went out of the window, and the room settled into its shadows.",
    "rope creaked once and held, and the boat drifted a hand's width from the pier.",
    "answer came slowly, as if it had to be carried up from somewhere far below.",
)
THOUGHTS = (
    "The reader wants to go in. The door was left unlocked in the last scene, so it opens; "
    "keep the ferryman out of sight for now and let the room speak first.",
    "Two things are pending: the map and the missing courier. Nudge toward the map — it is "
    "on the table already — and leave the courier for the next beat.",
)
NOTES = (
    "Thread: the cross by the weir marks where the courier went into the water.",
    "The ferryman knows more than he says; keep his answers short.",
)
QUESTIONS = (
    ("What do you do?", ("Go in", "Wait by the door", "Call out first")),
    ("Which way?", ("The hill road", "The river path")),
)
SEARCHES = ("tide tables for a tidal river", "how an old ferry rope is coiled")


def main() -> None:
    parser = argparse.ArgumentParser(description="A fake llama.cpp for looking at the transcript.")
    parser.add_argument("--port", type=int, default=8080, help="llama.cpp's default: 8080")
    parser.add_argument(
        "--pace", type=float, default=0.03, help="seconds between streamed pieces (0.03)"
    )
    args = parser.parse_args()
    try:
        server = ModelServer(models=(CHAT_MODEL, TEXT_MODEL), port=args.port)
    except OSError as e:
        sys.exit(f"test_model: cannot listen on port {args.port} ({e.strerror or e})")
    # Several models are llama.cpp's router: listed with their state, and
    # both loaded, so each one's window is known from the start.
    server.router = True
    server.loaded = {CHAT_MODEL, TEXT_MODEL}
    server.window = WINDOW
    server.contexts.update({CHAT_MODEL: WINDOW, TEXT_MODEL: WINDOW})
    # The template probe (`/apply-template`) answered at once, by a template
    # that reads no thinking switch: the client learns "unknown" and keeps
    # it. Unanswered, each probe fell through to a reply, streamed at the
    # pace — seconds, on every listing.
    server.template_reads = set()
    server.chunk_size = 6
    server.chunk_delay = args.pace
    server.script = reply
    server.refuse = refuse
    server.refusal = "the test model failed, as -1 asks"
    print(
        f"A fake llama.cpp at {server.url}, serving {CHAT_MODEL} and {TEXT_MODEL}."
        " Ctrl+C stops it.",
        flush=True,
    )
    print(f'The novel: text_template = "plain" under [{TEXT_MODEL}] in configs/models.toml.')
    print("Lines starting with digits: 1 think · 2 note · 3 question · 4 system info · 5 search")
    print(
        "  6 long · 7 inline · 8 paragraph · 9 flush · 0 everything — combine them: 13 …"
        " · -1 a failure",
        flush=True,
    )
    restore = _quiet_interrupt()
    try:
        while True:
            time.sleep(3600)
            server.requests.clear()  # nothing reads them here; they only grow
    except KeyboardInterrupt:
        pass
    finally:
        restore()
        server.close()


def _quiet_interrupt() -> Callable[[], None]:
    """Ctrl+C ends the server without the terminal echoing `^C`: the
    terminal's echo of control keys is off while it serves; the callable
    returned puts it back. Nothing to do off a terminal, or where there
    is no termios."""
    try:
        import termios
    except ImportError:
        return lambda: None
    if not sys.stdin.isatty():
        return lambda: None
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    quiet = list(saved)
    quiet[3] &= ~termios.ECHOCTL  # the local modes
    termios.tcsetattr(fd, termios.TCSANOW, quiet)
    return lambda: termios.tcsetattr(fd, termios.TCSANOW, saved)


def reply(body: dict[str, Any]) -> str | tuple[str, str]:
    """What the fake answers one request with: the blocks the line's
    digits ask for, the reply going on after each tool's turn until the
    tools the line asked for have run; a paragraph for anything else —
    the lore prompts included, which get prose they cannot parse."""
    if "messages" not in body:
        # The text wire: one text, the latest line its last paragraph.
        tail = str(body.get("prompt", "")).rstrip().rsplit("\n\n", 1)[-1]
        return _compose(_digits(tail), rounds=0)
    messages: list[dict[str, Any]] = body["messages"]
    # Walk back over the tool rounds of this reply: a reader's-role turn
    # right after a reply that ended on a call is the tool's result.
    rounds, at = 0, len(messages) - 1
    while at >= 1 and messages[at].get("role") == "user":
        before = messages[at - 1]
        if before.get("role") != "assistant" or not _TOOL_CALL.search(content_text(before)):
            break
        rounds, at = rounds + 1, at - 2
    line = content_text(messages[at]) if messages[at].get("role") == "user" else ""
    # the line's own paragraph: an injection merged into its row stands before it
    return _compose(_digits(line.rstrip().rsplit("\n\n", 1)[-1]), rounds=rounds)


def refuse(body: dict[str, Any]) -> int | None:
    """The status a request is refused with, or None to serve it: 500 for
    a line that opens with -1, before any word of a reply."""
    if "messages" in body:
        last = body["messages"][-1]
        line = content_text(last) if last.get("role") == "user" else ""
    else:
        line = str(body.get("prompt", ""))
    return 500 if _FAIL.match(line.rstrip().rsplit("\n\n", 1)[-1]) else None


def _digits(line: str) -> set[str]:
    found = _DIGITS.match(line)
    return set(found.group(1)) if found else set()


def _compose(digits: set[str], *, rounds: int) -> str | tuple[str, str]:
    everything = "0" in digits
    calls = [
        call
        for digit, call in (("4", "```otk-system-info\n```"), ("5", _search_call()))
        if everything or digit in digits
    ]
    first = rounds == 0
    parts: list[str] = []
    if "7" in digits and first:
        parts.append(" " + random.choice(CONTINUATIONS))
    elif "9" in digits and first:
        parts.append(random.choice(CONTINUATIONS))
    elif "8" in digits and first:
        parts.append("\n\n" + random.choice(PARAGRAPHS))
    else:
        parts.append(random.choice(PARAGRAPHS))
    if "6" in digits and first:
        parts += random.sample(PARAGRAPHS, 3)
    if (everything or "2" in digits) and first:
        parts.append(f"```otk-note\n{random.choice(NOTES)}\n```")
    if rounds < len(calls):
        # A call otaku runs ends the reply; the next round goes on after it.
        parts.append(calls[rounds])
    elif everything or "3" in digits:
        question, options = random.choice(QUESTIONS)
        numbered = "\n".join(f"{n}. {option}" for n, option in enumerate(options, 1))
        parts.append(f"```otk-question\n{question}\n{numbered}\n```")
    text = parts[0] + "".join("\n\n" + part for part in parts[1:])
    if (everything or "1" in digits) and first:
        return random.choice(THOUGHTS), text
    return text


def _search_call() -> str:
    return f"```otk-web-search\n{random.choice(SEARCHES)}\n```"


if __name__ == "__main__":
    main()
