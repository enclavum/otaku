"""The extraction's pure pieces: scene planning and the numbered scene.

`pack` cuts item sizes into contiguous [start, end) spans covering every
item, each holding at least `min_messages` items and `min_chars` of size
— as many spans as any partition allows. Cut i of k sits at the allowed
position whose cumulative size is nearest to i/k of the total, the earlier
on a tie, where allowed means the span before it qualifies and the rest
can still make the k-i spans left. Items that cannot make two qualifying
spans are one span all the same.

`plan_scenes` closes the scenes of one extraction pass over a story's
unextracted tail. The tail is cut before every scene start into parts,
each packed on its own. An ended part closes whole once its last row is
settled, and one that is not stops every later part; the part in play
packs its settled rows and holds the newest span open; a heading alone
closes nothing; `force` ignores the settle margin and holds nothing open.

`numbered_chat`'s contract: the analysis model sees `[n]` numbering, an attributed
line's speaker, composed template, and an `((OOC: …))` enclosure on every
out-of-character row — added when the row has no stored template to show
one — and never an `otk-` block.
"""

import random
from collections.abc import Sequence
from itertools import combinations, pairwise

import pytest

from otaku.agents.extraction import _parse_json, numbered_chat, pack, plan_scenes
from otaku.store.schema import Attachment, Message

# The extraction pass's minimums over rows of one size: twenty rows make a scene.
_MIN_CHARS = 6000
_MIN_MESSAGES = 20
_ROW = 300

_Span = tuple[int, int]
_Tail = tuple[list[int], list[int], int, int, int, bool]


class TestPack:
    def test_no_items_make_no_spans(self) -> None:
        assert pack([], min_chars=10, min_messages=2) == []

    def test_the_spans_run_in_order_over_every_item(self) -> None:
        for sizes, min_chars, min_messages in _item_cases():
            if not sizes:
                continue
            spans = pack(sizes, min_chars=min_chars, min_messages=min_messages)
            assert spans[0][0] == 0, sizes
            assert spans[-1][1] == len(sizes), sizes
            assert all(start < end for start, end in spans), sizes
            assert all(left[1] == right[0] for left, right in pairwise(spans)), sizes

    def test_several_spans_each_meet_both_minimums(self) -> None:
        for sizes, min_chars, min_messages in _item_cases():
            spans = pack(sizes, min_chars=min_chars, min_messages=min_messages)
            if len(spans) > 1:
                for start, end in spans:
                    assert _meets(sizes[start:end], min_chars, min_messages), (sizes, spans)

    def test_there_are_as_many_spans_as_any_partition_allows(self) -> None:
        """Checked against every partition of small inputs; under two, one span."""
        for sizes, min_chars, min_messages in _item_cases():
            if not sizes:
                continue
            most = _most_spans(sizes, min_chars, min_messages)
            spans = pack(sizes, min_chars=min_chars, min_messages=min_messages)
            assert len(spans) == max(most, 1), (sizes, min_chars, min_messages, spans)

    def test_the_size_minimum_can_allow_fewer_spans_than_the_count(self) -> None:
        """Items enough for five spans, size enough for two."""
        assert pack([1] * 50, min_chars=20, min_messages=10) == [(0, 25), (25, 50)]

    def test_the_count_minimum_can_allow_fewer_spans_than_the_size(self) -> None:
        """Size enough for ten spans, items enough for two."""
        assert pack([100] * 10, min_chars=100, min_messages=4) == [(0, 5), (5, 10)]

    def test_one_item_spans_when_min_messages_is_one(self) -> None:
        assert pack([5, 7, 5], min_chars=5, min_messages=1) == [(0, 1), (1, 2), (2, 3)]

    def test_a_large_item_can_stand_as_a_span_alone(self) -> None:
        sizes = [100] * 10 + [10000] + [100] * 10
        assert pack(sizes, min_chars=1000, min_messages=1) == [(0, 10), (10, 11), (11, 21)]

    def test_a_large_item_lends_no_size_past_its_span(self) -> None:
        """The large item qualifies alone, but the small ones after it cannot."""
        assert pack([5000, 10, 10, 10], min_chars=1000, min_messages=1) == [(0, 4)]

    def test_one_item_is_one_span_whether_or_not_it_meets_the_minimums(self) -> None:
        assert pack([5], min_chars=10, min_messages=2) == [(0, 1)]
        assert pack([50], min_chars=10, min_messages=1) == [(0, 1)]

    def test_items_under_the_count_minimum_are_one_span(self) -> None:
        assert pack([1000, 1000], min_chars=10, min_messages=3) == [(0, 2)]

    def test_items_under_the_size_minimum_are_one_span(self) -> None:
        assert pack([1] * 10, min_chars=100, min_messages=2) == [(0, 10)]

    def test_items_short_of_two_spans_are_one_span(self) -> None:
        assert pack(_rows(39), min_chars=_MIN_CHARS, min_messages=_MIN_MESSAGES) == [(0, 39)]
        assert pack([1000] * 5, min_chars=10, min_messages=3) == [(0, 5)]

    def test_empty_items_never_meet_a_size_minimum(self) -> None:
        assert pack([0] * 6, min_chars=1, min_messages=2) == [(0, 6)]

    def test_an_empty_item_still_counts_as_an_item(self) -> None:
        assert pack([100, 0, 100, 0], min_chars=100, min_messages=2) == [(0, 2), (2, 4)]

    def test_fifty_nine_rows_split_in_the_middle(self) -> None:
        spans = pack(_rows(59), min_chars=_MIN_CHARS, min_messages=_MIN_MESSAGES)
        assert spans == [(0, 29), (29, 59)]

    def test_sixty_rows_split_in_three(self) -> None:
        spans = pack(_rows(60), min_chars=_MIN_CHARS, min_messages=_MIN_MESSAGES)
        assert spans == [(0, 20), (20, 40), (40, 60)]

    def test_a_cut_takes_the_allowed_position_nearest_its_share(self) -> None:
        """Cuts at 1, 2 and 3 are all allowed; 2 sits on half the size."""
        assert pack([10, 1, 1, 10], min_chars=10, min_messages=1) == [(0, 2), (2, 4)]

    def test_cuts_follow_size_not_count(self) -> None:
        assert pack([100, 100] + [10] * 8, min_chars=1, min_messages=4) == [(0, 4), (4, 10)]
        sizes = [1] * 4 + [3] * 8 + [1] * 8
        assert pack(sizes, min_chars=1, min_messages=7) == [(0, 9), (9, 20)]

    def test_each_cut_aims_at_its_share_of_the_whole(self) -> None:
        """The second cut aims at 2/3 of all the size (10.7), not half of what is left (10.5)."""
        assert pack([5, 5, 1, 5], min_chars=5, min_messages=1) == [(0, 1), (1, 3), (3, 4)]

    def test_a_cut_goes_only_as_near_its_share_as_the_rest_allows(self) -> None:
        """Every share lies inside the large item; each cut stops where the rest still makes
        the spans left."""
        sizes = [10] * 5 + [1000] + [10] * 5
        spans = pack(sizes, min_chars=10, min_messages=2)
        assert spans == [(0, 3), (3, 5), (5, 7), (7, 9), (9, 11)]

    def test_of_two_equally_near_cuts_the_earlier_wins(self) -> None:
        assert pack([1] * 5, min_chars=1, min_messages=2) == [(0, 2), (2, 5)]

    def test_every_cut_is_the_allowed_position_nearest_its_share(self) -> None:
        for sizes, min_chars, min_messages in _item_cases():
            spans = pack(sizes, min_chars=min_chars, min_messages=min_messages)
            expected = _expected_pack(sizes, min_chars, min_messages)
            assert spans == expected, (sizes, min_chars, min_messages)

    def test_any_sequence_packs(self) -> None:
        spans = pack(tuple(_rows(60)), min_chars=_MIN_CHARS, min_messages=_MIN_MESSAGES)
        assert spans == [(0, 20), (20, 40), (40, 60)]


class TestPlanScenes:
    def test_an_empty_tail_closes_nothing(self) -> None:
        assert _plan(0) == []
        assert _plan(0, force=True) == []

    def test_the_newest_span_is_held_open(self) -> None:
        """45 rows pack as 22 + 23; the 23 stay open."""
        assert _plan(45) == [(0, 22)]

    def test_the_part_in_play_closes_all_but_its_newest_span(self) -> None:
        assert _plan(60) == [(0, 20), (20, 40)]

    def test_the_part_in_play_closes_nothing_short_of_two_spans(self) -> None:
        assert _plan(39) == []
        assert _plan(40) == [(0, 20)]

    def test_only_the_settled_rows_of_the_part_in_play_are_packed(self) -> None:
        assert _plan(60, settled=45) == [(0, 22)]
        assert _plan(60, settled=40) == [(0, 20)]
        assert _plan(60, settled=39) == []

    def test_nothing_settled_closes_nothing(self) -> None:
        assert _plan(60, settled=0) == []
        assert _plan(48, [40], settled=0) == []

    def test_an_ended_part_closes_whole(self) -> None:
        """The 8 rows in play after it are held."""
        assert _plan(48, [40]) == [(0, 20), (20, 40)]

    def test_an_ended_part_closes_once_its_last_row_is_settled(self) -> None:
        assert _plan(48, [40], settled=40) == [(0, 20), (20, 40)]
        assert _plan(48, [40], settled=39) == []

    def test_an_ended_part_not_settled_whole_closes_nothing(self) -> None:
        assert _plan(48, [40], settled=30) == []

    def test_an_ended_part_not_settled_whole_stops_every_later_part(self) -> None:
        assert _plan(90, [40, 80], settled=60) == [(0, 20), (20, 40)]

    def test_an_ended_part_is_packed_as_pack_cuts_it(self) -> None:
        assert _plan(69, [59]) == [(0, 29), (29, 59)]

    def test_a_short_ended_part_closes_as_one_short_scene(self) -> None:
        assert _plan(33, [31]) == [(0, 31)]

    def test_each_part_is_packed_on_its_own(self) -> None:
        assert _plan(100, [50]) == [(0, 25), (25, 50), (50, 75)]
        assert _plan(70, [30]) == [(0, 30), (30, 50)]

    def test_a_heading_alone_closes_nothing(self) -> None:
        """The short chapter before it is one short scene; the 14 rows in play are held."""
        assert _plan(25, [10, 11]) == [(0, 10)]

    def test_a_heading_alone_at_the_end_ends_the_part_before_it(self) -> None:
        """The heading at 40 is the part in play, so the 40 rows before it have ended."""
        assert _plan(41, [40]) == [(0, 20), (20, 40)]

    def test_a_heading_with_one_row_after_it_is_a_short_scene(self) -> None:
        assert _plan(30, [10, 12]) == [(0, 10), (10, 12)]

    def test_a_start_at_zero_needs_no_cut(self) -> None:
        assert _plan(45, [0]) == [(0, 22)]
        assert _plan(48, [0, 40]) == [(0, 20), (20, 40)]

    def test_a_start_at_zero_changes_nothing_over_many_tails(self) -> None:
        for tail in _tails():
            sizes, starts = tail[0], tail[1]
            if _first_part_is_one_row(len(sizes), starts):
                continue  # whether that row is a heading alone, the contract leaves open
            without = [start for start in starts if start != 0]
            assert _scenes(tail, starts=[*without, 0]) == _scenes(tail, starts=without), tail

    def test_starts_may_come_unsorted_and_repeated(self) -> None:
        assert _plan(25, [11, 10, 11, 10]) == [(0, 10)]
        assert _plan(90, [80, 40, 80], force=True) == [
            (0, 20),
            (20, 40),
            (40, 60),
            (60, 80),
            (80, 90),
        ]

    def test_unsorted_and_repeated_starts_change_nothing_over_many_tails(self) -> None:
        for tail in _tails():
            starts = tail[1]
            given = _scenes(tail, starts=starts + starts[::-1])
            assert given == _scenes(tail, starts=sorted(set(starts))), tail

    def test_starts_past_the_tail_are_ignored(self) -> None:
        """48 rows have indexes 0 to 47: a start at 48 is no row of the tail."""
        assert _plan(48, [48, 60]) == _plan(48) == [(0, 24)]

    def test_negative_starts_are_ignored(self) -> None:
        assert _plan(48, [-1, -8]) == _plan(48) == [(0, 24)]

    def test_starts_outside_the_tail_change_nothing_over_many_tails(self) -> None:
        for tail in _tails():
            sizes, starts = tail[0], tail[1]
            inside = [start for start in starts if 0 <= start < len(sizes)]
            assert _scenes(tail) == _scenes(tail, starts=inside), tail

    def test_force_closes_everything(self) -> None:
        assert _plan(48, [40], settled=30, force=True) == [(0, 20), (20, 40), (40, 48)]

    def test_force_holds_no_span_open(self) -> None:
        assert _plan(45, force=True) == [(0, 22), (22, 45)]

    def test_force_closes_parts_under_the_minimums(self) -> None:
        assert _plan(10, settled=0, force=True) == [(0, 10)]
        assert _plan(33, [31], settled=0, force=True) == [(0, 31), (31, 33)]

    def test_force_ignores_the_settle_margin(self) -> None:
        for settled in (0, 30, 40, 48):
            assert _plan(48, [40], settled=settled, force=True) == [(0, 20), (20, 40), (40, 48)]

    def test_force_ignores_the_settle_margin_over_many_tails(self) -> None:
        for tail in _tails():
            closed = _scenes(tail, settled=0, force=True)
            for settled in range(1, len(tail[0]) + 1):
                assert _scenes(tail, settled=settled, force=True) == closed, (tail, settled)

    def test_scenes_run_in_order_and_none_crosses_a_start(self) -> None:
        for tail in _tails():
            sizes, starts = tail[0], tail[1]
            scenes = _scenes(tail)
            case = (tail, scenes)
            assert all(0 <= start < end <= len(sizes) for start, end in scenes), case
            assert all(left[1] <= right[0] for left, right in pairwise(scenes)), case
            assert not any(start < s < end for start, end in scenes for s in starts), case

    def test_every_rule_holds_over_many_tails(self) -> None:
        """Against the contract written out by brute force, bar the cases it leaves open."""
        for tail in _tails():
            expected = _expected_plan(*tail)
            if expected is not None:
                assert _scenes(tail) == expected, tail


class TestNumberedChat:
    def test_numbers_each_message(self) -> None:
        text = numbered_chat(
            [Message(role="user", body="First."), Message(role="assistant", body="Second.")]
        )
        assert text == "[1] First.\n[2] Second."

    def test_an_attributed_line_carries_its_speaker(self) -> None:
        text = numbered_chat([Message(role="user", body="I wait.", speaker="Ryn")])
        assert text == "[1] Ryn: I wait."

    def test_framing_is_composed_onto_the_body(self) -> None:
        message = Message(role="user", body="I wait.", template="((OOC: as Ryn.))\n{body}")
        assert numbered_chat([message]) == "[1] ((OOC: as Ryn.))\nI wait."

    def test_a_bare_ooc_row_gains_the_enclosure(self) -> None:
        message = Message(role="assistant", body="Good plan.", kind="ooc", template=None)
        assert numbered_chat([message]) == "[1] ((OOC: Good plan.))"

    def test_an_attributed_framed_row_keeps_its_framing(self) -> None:
        # The speaker decorates the COMPOSED line. Prefixing the body first
        # would hide its leading slash from the composer, sending the row
        # down the legacy path with `{name}` left unfilled.
        message = Message(
            role="user",
            body="/me Elara: I bow.",
            speaker="Elara",
            template="((OOC: as {name}.))\n{body}",
        )
        assert numbered_chat([message]) == "[1] Elara: ((OOC: as Elara.))\nI bow."

    def test_an_ooc_row_with_framing_shows_it_as_stored(self) -> None:
        message = Message(role="user", body="Plan?", kind="ooc", template="((OOC: {body}))")
        assert numbered_chat([message]) == "[1] ((OOC: Plan?))"

    def test_a_block_is_left_out_of_the_numbered_chat(self) -> None:
        # A block is the model's own aside, not the scene: the analysis
        # model never sees it, tags and all, and the numbering stays whole.
        reply = Message(role="assistant", body="She nods.\n```otk-notes\nthe letter\n```")
        text = numbered_chat([Message(role="user", body="I wait."), reply])
        assert text == "[1] I wait.\n[2] She nods."

    def test_a_row_that_is_only_a_block_keeps_its_number(self) -> None:
        # The speaker labels come back BY NUMBER: a row may empty, never vanish.
        span = [
            Message(role="assistant", body="```otk-notes\nlater\n```", speaker="Keeper"),
            Message(role="user", body="Go."),
        ]
        assert numbered_chat(span) == "[1] \n[2] Go."

    def test_markup_outside_the_namespace_stays(self) -> None:
        reply = Message(role="assistant", body="She <i>never</i> nods.<plan>x</plan>")
        assert numbered_chat([reply]) == "[1] She <i>never</i> nods.<plan>x</plan>"

    def test_a_picture_is_marked_at_its_line_numbered_through_the_scene(self) -> None:
        # One mark per picture, in the order the request attaches them —
        # so the model can name what it saw by number.
        span = [
            Message(role="user", body="Look.", attachments=(picture("a"),)),
            Message(role="assistant", body="A door."),
            Message(role="user", body="And these.", attachments=(picture("b"), picture("c"))),
        ]
        assert numbered_chat(span).splitlines() == [
            "[1] (picture 1) Look.",
            "[2] A door.",
            "[3] (picture 2) (picture 3) And these.",
        ]

    def test_the_mark_is_a_decoration_the_speaker_still_heads(self) -> None:
        line = Message(role="user", body="Look.", speaker="Ryn", attachments=(picture("a"),))
        assert numbered_chat([line]) == "[1] Ryn: (picture 1) Look."

    def test_a_picture_with_no_words_is_the_mark_alone(self) -> None:
        line = Message(role="user", body="", attachments=(picture("a"),))
        assert numbered_chat([line]) == "[1] (picture 1)"


class TestParseJson:
    """The reply parser's tolerances: fences and prose around the object,
    and JSON syntax typed with typographic double quotes — a small model
    mirrors the story's own punctuation — straightened only on retry, so
    a parsable reply keeps every curly quote inside its values."""

    def test_reads_the_object_out_of_fences_and_prose(self) -> None:
        assert _parse_json('Sure!\n```json\n{"scene": 1}\n```') == {"scene": 1}

    def test_typographic_quotes_as_syntax_parse_on_retry(self) -> None:
        assert _parse_json("{“title”: “Quayside”}") == {"title": "Quayside"}

    def test_a_parsable_reply_keeps_curly_quotes_in_values(self) -> None:
        assert _parse_json('{"line": "She said “hi” — twice"}') == {"line": "She said “hi” — twice"}

    def test_a_reply_without_an_object_refuses(self) -> None:
        with pytest.raises(ValueError):
            _parse_json("no json here")

    def test_a_broken_object_still_refuses(self) -> None:
        with pytest.raises(ValueError):
            _parse_json('{"scene": }')


def picture(name: str) -> Attachment:
    """A picture on a turn, as the column records one."""
    return Attachment(file=f"pic-{name}.jpg", width=10, height=10, size=100)


# --- the worked examples' rows ---


def _rows(count: int) -> list[int]:
    return [_ROW] * count


def _plan(
    count: int,
    starts: Sequence[int] = (),
    *,
    settled: int | None = None,
    force: bool = False,
) -> list[_Span]:
    """`plan_scenes` over `count` rows of one size, every row settled unless told otherwise."""
    return plan_scenes(
        _rows(count),
        starts,
        settled=count if settled is None else settled,
        min_chars=_MIN_CHARS,
        min_messages=_MIN_MESSAGES,
        force=force,
    )


# --- the contract, written out by brute force ---


def _meets(sizes: Sequence[int], min_chars: int, min_messages: int) -> bool:
    return len(sizes) >= min_messages and sum(sizes) >= min_chars


def _most_spans(sizes: Sequence[int], min_chars: int, min_messages: int) -> int:
    """The most spans of any partition whose every span meets both minimums, 0 when none
    does — every partition tried, most spans first."""
    for cut_count in range(len(sizes) - 1, -1, -1):
        for cuts in combinations(range(1, len(sizes)), cut_count):
            bounds = (0, *cuts, len(sizes))
            if all(_meets(sizes[a:b], min_chars, min_messages) for a, b in pairwise(bounds)):
                return cut_count + 1
    return 0


def _expected_pack(sizes: Sequence[int], min_chars: int, min_messages: int) -> list[_Span]:
    """The contract's spans: the most there can be, each cut the allowed one nearest its share."""
    if not sizes:
        return []
    count = _most_spans(sizes, min_chars, min_messages)
    if count < 2:
        return [(0, len(sizes))]
    rest = [_most_spans(sizes[cut:], min_chars, min_messages) for cut in range(len(sizes))]
    cuts = [0]
    for i in range(1, count):
        allowed = [
            cut
            for cut in range(cuts[-1] + 1, len(sizes))
            if _meets(sizes[cuts[-1] : cut], min_chars, min_messages) and rest[cut] >= count - i
        ]
        cuts.append(_nearest(sizes, allowed, i, count))
    return list(pairwise([*cuts, len(sizes)]))


def _nearest(sizes: Sequence[int], allowed: list[int], i: int, count: int) -> int:
    """The allowed cut whose cumulative size is nearest i/count of the total, the earlier on a
    tie (`min` keeps the first of equals); compared times `count` to stay in integers."""
    total = sum(sizes)
    return min(allowed, key=lambda cut: abs(sum(sizes[:cut]) * count - i * total))


def _expected_plan(
    sizes: Sequence[int],
    starts: Sequence[int],
    settled: int,
    min_chars: int,
    min_messages: int,
    force: bool,
) -> list[_Span] | None:
    """The contract's scenes, or None where it leaves them open: a one-row first part that has
    ended or is forced (a heading alone, or a short scene?), and a heading alone under force
    ("closes everything", or "closes no scene"?)."""
    count = len(sizes)
    bounds = [0, *sorted({start for start in starts if 0 < start < count}), count]
    scenes: list[_Span] = []
    for start, end in pairwise(bounds):
        in_play = end == count
        alone = end - start == 1
        if alone and (force or (start == 0 and not in_play)):
            return None
        if force:
            scenes += _shifted(_expected_pack(sizes[start:end], min_chars, min_messages), start)
        elif not in_play:
            if settled < end:
                break
            if not alone:
                scenes += _shifted(_expected_pack(sizes[start:end], min_chars, min_messages), start)
        else:
            ripe = sizes[start : max(start, min(settled, end))]
            scenes += _shifted(_expected_pack(ripe, min_chars, min_messages), start)[:-1]
    return scenes


def _shifted(spans: list[_Span], by: int) -> list[_Span]:
    return [(start + by, end + by) for start, end in spans]


# --- small random inputs ---


def _item_cases() -> list[tuple[list[int], int, int]]:
    """Up to ten sizes — some empty, some large — with minimums that leave one span to a few."""
    rng = random.Random(17)
    cases = []
    for _ in range(400):
        sizes = [
            rng.choice((0, rng.randint(1, 9), rng.randint(10, 30)))
            for _ in range(rng.randint(0, 10))
        ]
        cases.append((sizes, rng.randint(1, 30), rng.randint(1, 3)))
    return cases


def _tails() -> list[_Tail]:
    """Tails of up to twelve rows with a few starts — mostly inside the tail, some repeated,
    some outside — a settle margin anywhere, and force on about a third of them."""
    rng = random.Random(29)
    tails = []
    for _ in range(400):
        count = rng.randint(0, 12)
        sizes = [rng.choice((0, rng.randint(1, 9), rng.randint(10, 30))) for _ in range(count)]
        starts = [
            rng.randint(-2, count + 2) if rng.random() < 0.2 else rng.randint(0, max(count - 1, 0))
            for _ in range(rng.randint(0, 3))
        ]
        settled = rng.randint(0, count)
        tails.append(
            (sizes, starts, settled, rng.randint(1, 20), rng.randint(1, 2), rng.random() < 0.3)
        )
    return tails


def _scenes(
    tail: _Tail,
    *,
    starts: Sequence[int] | None = None,
    settled: int | None = None,
    force: bool | None = None,
) -> list[_Span]:
    """`plan_scenes` over a random tail, its starts, settle margin or force replaced if given."""
    sizes, tail_starts, tail_settled, min_chars, min_messages, forced = tail
    return plan_scenes(
        sizes,
        tail_starts if starts is None else starts,
        settled=tail_settled if settled is None else settled,
        min_chars=min_chars,
        min_messages=min_messages,
        force=forced if force is None else force,
    )


def _first_part_is_one_row(count: int, starts: Sequence[int]) -> bool:
    inside = [start for start in starts if 0 < start < count]
    return count > 0 and min(inside, default=count) == 1
