"""Free prose, dismantled into the same shape the other readers parse to.

`split_segments` owns the dismantling: paragraphs split into narration
beats and spoken lines (quoted or dash-led), oversized narration split at
sentence boundaries — the text reproduced verbatim, nothing rewritten.
`parse_freetext` wraps the segments as a story of narration turns; the
extraction pass builds the memory afterwards, exactly as for live play.
"""

import re

from otaku.transfer import ExportedMessage, StoryExport

# Prose splitting: quotes and dashes mark speech; the rest is narration.
_QUOTE_SPAN = re.compile(r'"[^"\n]*"|“[^”\n]*”|«[^»\n]*»|„[^“\n]*“')
_DASH_LINE = re.compile(r"^[—–-]\s+")  # noqa: RUF001 — en dash is deliberate
_MIN_SPEECH = 10  # shorter quoted spans are cited words inside narration
_MAX_TAG = 80  # longest unquoted span treated as an attribution tag
_MAX_SEGMENT = 600  # narration longer than this splits at sentence boundaries…
_TARGET_SEGMENT = 400  # …packed into pieces of roughly this size
_SAID_VERB = re.compile(
    r"\b(said|says|asked|replied|answered|muttered|whispered|murmured|snapped|"
    r"shouted|called|added|agreed|continued|offered|admitted|corrected)\b",
    re.IGNORECASE,
)


def parse_freetext(text: str) -> StoryExport | None:
    """`text` as a story of verbatim narration turns — or None when it
    holds nothing to import. Never rejects a format: any text is free
    text; the other parsers must be tried first."""
    segments = split_segments(text)
    if not segments:
        return None
    return StoryExport(
        messages=tuple(
            ExportedMessage(role="user", body=segment, kind="narration") for segment in segments
        )
    )


def split_segments(text: str) -> list[str]:
    """Dismantle prose into message-sized segments: paragraphs split into
    narration beats and spoken lines (quoted or dash-led), and oversized
    narration split at sentence boundaries. The text is reproduced
    verbatim; nothing is rewritten."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if block:
            out.extend(_split_block(block))
    return out


# ---------- prose splitting internals ----------


def _split_block(block: str) -> list[str]:
    """One blank-line-separated block → segments. A block whose lines use
    dash-led dialogue (— Reply, he said.) splits per line: each dash line
    is a turn, consecutive plain lines become narration runs."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) > 1 and any(_DASH_LINE.match(line) for line in lines):
        out: list[str] = []
        run: list[str] = []

        def flush() -> None:
            if run:
                for seg in _split_paragraph("\n".join(run)):
                    out.extend(_split_long_narration(seg))
                run.clear()

        for line in lines:
            if _DASH_LINE.match(line):
                flush()
                out.append(line)
            else:
                run.append(line)
        flush()
        return out
    return [piece for seg in _split_paragraph(block) for piece in _split_long_narration(seg)]


def _split_long_narration(seg: str) -> list[str]:
    """Split an oversized pure-narration segment at sentence boundaries
    into ~_TARGET_SEGMENT-char pieces (slice-based, so the text stays
    verbatim). Speech segments are never split — a turn stays whole."""
    if len(seg) <= _MAX_SEGMENT or _DASH_LINE.match(seg) or _QUOTE_SPAN.search(seg):
        return [seg]
    ends = [m.end() for m in re.finditer(r"[.!?…]+(?=\s)", seg)]
    if not ends:
        return [seg]
    out: list[str] = []
    start = 0
    prev = 0
    for end in [*ends, len(seg)]:
        if end - start > _TARGET_SEGMENT and prev > start:
            out.append(seg[start:prev].strip())
            start = prev
        prev = end
    if start < len(seg):
        out.append(seg[start:].strip())
    return [s for s in out if s]


def _split_paragraph(par: str) -> list[str]:
    """Split one paragraph at speech boundaries. Attribution tags stay with
    their quote (`"…," she said.` is one segment), adjacent quotes by the
    same breath merge, and narration beats become their own segments."""
    spans: list[tuple[bool, str]] = []
    pos = 0
    for m in _QUOTE_SPAN.finditer(par):
        if len(m.group(0)) < _MIN_SPEECH:
            continue  # a quoted word inside narration, not speech
        before = par[pos : m.start()].strip()
        if before:
            spans.append((False, before))
        spans.append((True, m.group(0)))
        pos = m.end()
    tail = par[pos:].strip()
    if tail:
        spans.append((False, tail))
    if not any(is_speech for is_speech, _ in spans):
        return [par]

    merged: list[tuple[bool, str]] = []
    for is_speech, text in spans:
        if merged:
            prev_speech, prev = merged[-1]
            is_tag = len(text) <= _MAX_TAG and (
                text[:1].islower() or text[:1] in ",;—–-" or _SAID_VERB.search(text)  # noqa: RUF001
            )
            if is_speech and prev_speech:
                merged[-1] = (True, f"{prev} {text}")
                continue
            if is_speech and not prev_speech and len(prev) <= _MAX_TAG and prev[-1] in ",:":
                merged[-1] = (True, f"{prev} {text}")
                continue
            if not is_speech and prev_speech and is_tag:
                sep = "" if text[:1] in ",;.!?…" else " "
                merged[-1] = (True, f"{prev}{sep}{text}")
                continue
        merged.append((is_speech, text))
    return [text for _, text in merged]
