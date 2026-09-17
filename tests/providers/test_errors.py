"""The error family: one base a caller catches, four kinds by where the
failure came from, and `str(e)` the sentence a frontend may show."""

import pytest

from otaku.providers import (
    DeclinedError,
    ProviderError,
    StatusError,
    UnauthorizedError,
    UnreachableError,
)


class TestTheFamily:
    @pytest.mark.parametrize("kind", [UnreachableError, UnauthorizedError, DeclinedError])
    def test_every_kind_is_a_provider_error(self, kind: type[ProviderError]) -> None:
        with pytest.raises(ProviderError):
            raise kind("Could not reach x.")

    def test_the_sentence_is_the_message(self) -> None:
        assert str(UnreachableError("Could not reach x.")) == "Could not reach x."

    def test_a_status_error_carries_the_status_and_is_one_of_the_family(self) -> None:
        refused = StatusError("Refused by x with HTTP 503.", 503)
        assert refused.status == 503
        assert str(refused) == "Refused by x with HTTP 503."
        assert isinstance(refused, ProviderError)
