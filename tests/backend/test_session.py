"""The /set vocabulary constants the frontends read off
`backend.session` — pinned to the file vocabulary they order, because
the two live in different layers and only this keeps them one thing."""

from otaku.backend.session import KNOWN_PARAMS, THINK_MENU
from otaku.providers.openai.completion import PROTOCOL_PARAMS
from otaku.providers.registry import ALL_CLIENTS
from otaku.settings.state import THINK_DEFAULT, THINK_LEVELS


class TestThinkVocabulary:
    def test_the_menu_offers_exactly_the_file_s_vocabulary(self) -> None:
        # A menu offering a level the file refuses would Refuse on
        # click; one missing a level would make that level typed-only.
        assert set(THINK_MENU) == THINK_LEVELS | {THINK_DEFAULT}

    def test_default_leads_the_menu(self) -> None:
        # The way out comes first, as the docstring promises.
        assert THINK_MENU[0] == THINK_DEFAULT


class TestParamsVocabulary:
    def test_every_provider_declares_what_it_reads_in_the_set_vocabulary(self) -> None:
        # A declaration outside the vocabulary names a parameter nobody
        # can set; one short of the protocol's own denies what every
        # OpenAI endpoint reads.
        for kind, client_class in ALL_CLIENTS.items():
            declared = client_class.completion_class.supported_params
            assert PROTOCOL_PARAMS <= declared <= set(KNOWN_PARAMS), kind
