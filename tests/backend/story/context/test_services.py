"""The one rule both frontends lay the preview out by (`separate`): a
header — the summary's, the pinned messages' — opens whatever follows
it; a run reads as one — of messages, of the summary's history and scene
summaries, of the pinned messages one header heads; every other part
stands apart."""

from itertools import product

from otaku.backend.story.context.services import separate
from otaku.context.prompt import Provenance

HEADERS = (Provenance.HEADER, Provenance.PINNED_HEADER)
RUNS = {
    (Provenance.MESSAGE, Provenance.MESSAGE),
    (Provenance.HISTORY, Provenance.SCENE),
    (Provenance.SCENE, Provenance.SCENE),
    (Provenance.PINNED_MESSAGE, Provenance.PINNED_MESSAGE),
}


class TestSeparate:
    def test_a_header_opens_whatever_follows_it(self) -> None:
        for header, after in product(HEADERS, Provenance):
            assert not separate(header, after), (header, after)

    def test_a_run_reads_as_one(self) -> None:
        for previous, current in RUNS:
            assert not separate(previous, current), (previous, current)

    def test_every_other_part_stands_apart(self) -> None:
        for previous, current in product(Provenance, Provenance):
            if previous in HEADERS or (previous, current) in RUNS:
                continue
            assert separate(previous, current), (previous, current)
