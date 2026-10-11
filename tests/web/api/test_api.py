"""The page's whole surface as tables (`web.api`): every row keyed by
method and path, merged from the twins' own rows — a path two of them
claim a fault at import, not a toss-up the import order decides — and
the paths written as the HTTP surface writes them: under `/api`,
kebab-case, a parameter `{name}`. The METHOD is the lane a request
takes, and the two writes answered with the reply's stream are POSTs."""

import re

import pytest

from otaku.web import api
from otaku.web.api import _merged

_SEGMENT = re.compile(r"[a-z]+(-[a-z]+)*|\{[a-z_]+\}")


class TestTables:
    def test_every_path_is_under_api_in_kebab_case(self) -> None:
        for _, template in _surface():
            assert template.startswith("/api/"), template
            segments = template.removeprefix("/api/").split("/")
            assert all(_SEGMENT.fullmatch(segment) for segment in segments), template

    def test_every_method_is_a_lane(self) -> None:
        # a GET reads in the gaps of a streaming reply; every other method
        # moves something and takes the app's thread in turn
        assert {method for method, _ in _surface()} <= {"GET", "POST", "PATCH", "DELETE"}

    def test_the_streams_are_the_line_played_and_the_fresh_take(self) -> None:
        assert set(api.STREAMS) == {
            ("POST", "/api/stories/{story}/messages"),
            ("POST", "/api/stories/{story}/regens"),
        }

    def test_no_path_is_answered_by_both_tables(self) -> None:
        # The server matches one sorted list built from both, so a path in
        # both would let the compile order decide which answers it.
        assert set(api.ROUTES) & set(api.STREAMS) == set()


class TestMerged:
    def test_the_rows_of_every_table_are_one_table(self) -> None:
        merged = _merged({("GET", "/api/a"): 1}, {("GET", "/api/b"): 2})
        assert merged == {("GET", "/api/a"): 1, ("GET", "/api/b"): 2}

    def test_a_path_two_tables_claim_is_a_fault(self) -> None:
        with pytest.raises(AssertionError):
            _merged({("GET", "/api/a"): 1}, {("GET", "/api/a"): 2})

    def test_one_path_under_two_methods_is_two_rows(self) -> None:
        merged = _merged({("GET", "/api/a"): 1}, {("PATCH", "/api/a"): 2})
        assert len(merged) == 2


def _surface() -> dict[tuple[str, str], object]:
    """Every path the page may ask for, whichever table answers it."""
    return {**api.ROUTES, **api.STREAMS}
