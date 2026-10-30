#!/usr/bin/env python
"""A fake llama.cpp for looking at the transcript by hand: `make test-model`.

It answers as llama.cpp does, on llama.cpp's default port, so the
`[llamacpp]` section a first run writes points here with nothing changed:
two models, as llama.cpp's router serves several, both loaded, each a 32K
window and seeing pictures — `otaku-test-chat`, an instruct model as most are, and
`otaku-test-text`, the base model the novel's text wire is for once one
hand line says so: `text_template = "plain"` under `[otaku-test-text]` in
configs/models.toml. Pick one in the model picker, switch on the story's
tools (questions, notes, system info, web search) on the Tools tab, and
play.

A number in a line chooses what the reply carries — the first one,
wherever it stands, or a list of them separated by commas alone, each
digit a choice: `13 I open the door`, `I open the door 1,3`. A line
without one gets a paragraph of prose, which in the canvas goes on in
the paragraph before it, as every reply that opens without a break does:

    1  thinking, streamed before the reply
    2  a note
    3  a question with options, ending the reply (answered, it is a past one)
    4  system info: otaku runs it and the reply goes on after its turn
    5  web search: the same (without a key, its turn is the failure sentence)
    6  a long reply: paragraphs, with dialogue
    7  a continuation: the sentence goes on, sent with a leading space
       the join gives up for its own
    8  an opening with a paragraph break
    0  everything: thinking, prose, a note, both tools, then a question

A line whose number is `-1` fails half the time: the engine answers 500
before a word arrives, and the reply stops on the failure; the other half,
and so a retry may, it gets a paragraph of prose.

On the text wire a line runs on in the paragraph before it, so the line
read there is what the story gained after the fake's own last reply. The
lore pass's prompts are told by their fixed openings and choose nothing:
their scenes are numbered.

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

# The first number in a line, or a list of them separated by commas alone.
_NUMBERS = re.compile(r"-?\d+(?:,\d+)*")
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
    "keep the ferryman out of sight for now and let the room speak first. What does the room "
    "hold? The lamp, cold since the morning; the table with the map still spread on it; the "
    "smell of tar and wet rope from the pier below. Nothing should move yet — the stillness is "
    "the point, and it makes the reader listen. The ferryman is close, though: a chair pushed "
    "back, a cup still warm. Give one of those, not both, so the reader has something to "
    "wonder about. End on a sound from the river side, a step or a creak, and leave it "
    "unexplained so the next line is theirs.",
    "Two things are pending: the map and the missing courier. Nudge toward the map — it is "
    "on the table already — and leave the courier for the next beat. The map matters more "
    "right now: the reader asked about the crossing twice, and the map is where the crossing "
    "is drawn. Someone has marked it since they last saw it, a cross in charcoal by the weir. "
    "That mark ties back to the courier without naming him, which keeps the thread alive "
    "without spending it. Keep the description short — three details at most — and let the "
    "reader decide whether to touch it. If they ask who made the mark, the ferryman can "
    "answer later, and badly; he should not be the one to explain it.",
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
    # Both see: the router lists each with the image modality, so a picture
    # may be attached wherever the wire carries one (the text wire never does).
    server.capabilities.update({CHAT_MODEL: ["vision"], TEXT_MODEL: ["vision"]})
    # The template probe (`/apply-template`) answered at once, by a template
    # that reads no thinking switch: the client learns "unknown" and keeps
    # it. Unanswered, each probe fell through to a reply, streamed at the
    # pace — seconds, on every listing.
    server.template_reads = set()
    server.chunk_size = 6
    server.chunk_delay = args.pace
    server.headers_delay = 2.0  # the prefill: nothing until the first token
    server.script = reply
    server.refuse = refuse
    server.refusal = "the test model failed, as -1 asks"
    print(
        f"A fake llama.cpp at {server.url}, serving {CHAT_MODEL} and {TEXT_MODEL}."
        " Ctrl+C stops it.",
        flush=True,
    )
    print(f'The novel: text_template = "plain" under [{TEXT_MODEL}] in configs/models.toml.')
    print("A number in a line: 1 think · 2 note · 3 question · 4 system info · 5 search")
    print(
        "  6 long · 7 continuation · 8 paragraph · 0 everything — combine them: 13 or 1,3 …"
        " · -1 a failure, half the time",
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
    numbers ask for, the reply going on after each tool's turn until the
    tools the line asked for have run; a paragraph for anything else —
    the lore prompts included, which get prose they cannot parse."""
    global _answered
    if "messages" not in body:
        # The text wire: one text, the newest line run on into it.
        composed = _compose(_choices(_text_line(str(body.get("prompt", "")))), rounds=0)
        _answered = composed if isinstance(composed, str) else composed[1]
        return composed
    messages: list[dict[str, Any]] = body["messages"]
    if _is_lore(content_text(messages[-1])):
        return _compose(set(), rounds=0)
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
    return _compose(_choices(line.rstrip().rsplit("\n\n", 1)[-1]), rounds=rounds)


def refuse(body: dict[str, Any]) -> int | None:
    """The status a request is refused with, or None to serve it: 500 for
    a line whose number is -1, half the time, before any word of a reply —
    so a retry may get through."""
    if "messages" in body:
        last = body["messages"][-1]
        line = content_text(last) if last.get("role") == "user" else ""
        if _is_lore(line):
            return None
        line = line.rstrip().rsplit("\n\n", 1)[-1]
    else:
        line = _text_line(str(body.get("prompt", "")))
    found = _NUMBERS.search(line)
    failing = found is not None and re.match(r"-1(?!\d)", found.group(0))
    return 500 if failing and random.random() < 0.5 else None


# The fake's last reply over the text wire: the newest line is what follows it.
_answered = ""


def _text_line(prompt: str) -> str:
    """The reader's newest line on the text wire, where it runs on in the
    paragraph before it: what the story gained after the fake's own last
    reply — the edges of a stored reply may differ, its inside does not —
    else the text's last paragraph."""
    said = _answered.strip()
    at = prompt.rfind(said) if said else -1
    if at >= 0:
        return prompt[at + len(said) :]
    return prompt.rstrip().rsplit("\n\n", 1)[-1]


def _choices(line: str) -> set[str]:
    """What `line` asks for: every digit of its first number, or of each
    number in a comma-separated list; none for a line without one, or
    for one whose number is negative (-1 fails, `refuse`)."""
    found = _NUMBERS.search(line)
    if found is None or found.group(0).startswith("-"):
        return set()
    return set(re.sub(r"\D", "", found.group(0)))


def _is_lore(text: str) -> bool:
    """Whether a request is the lore pass's, by its prompt's fixed opening
    — as the scenarios' scripted server tells them (`default_script`)."""
    return (
        "You are a story analyst" in text
        or text.startswith("Combine the scene summaries")
        or (text.startswith("Write ") and "'s history" in text)
    )


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
