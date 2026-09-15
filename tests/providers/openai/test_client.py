"""The client: who the engine is, the url rule for a local one, and what
it can do composed as one object."""

from otaku.providers import Locality, ProviderCapabilities, ProviderConfig
from otaku.providers.openai.client import OpenAIClient
from otaku.providers.openai.completion import PROTOCOL_PARAMS


class _Engine(OpenAIClient):
    id = "engine"
    label = "Engine"
    locality = Locality.LOCAL


class _Catalog(OpenAIClient):
    id = "catalog"
    label = "Catalog"
    locality = Locality.REMOTE


class _Somewhere(OpenAIClient):
    id = "somewhere"
    label = "Somewhere"


class TestIdentity:
    def test_the_base_cannot_say_where_it_runs_and_reads_no_variable(self) -> None:
        assert _Somewhere.locality is Locality.UNKNOWN
        assert _Somewhere.env_key == ""

    def test_the_default_section_names_the_engine_and_no_url(self) -> None:
        assert _Engine.autoconfigure() == ProviderConfig(name="engine", url="")

    def test_the_locality_words_are_the_wires(self) -> None:
        assert {member.value for member in Locality} == {"local", "remote", "unknown"}


class TestTheUrlRule:
    def test_a_local_engines_url_names_its_server_and_the_protocol_is_at_v1(self) -> None:
        for typed in ("http://h:1", "http://h:1/", "http://h:1/v1", "http://h:1/v1/"):
            assert _Engine(ProviderConfig(name="engine", url=typed)).config.url == "http://h:1/v1"

    def test_a_catalogs_url_and_an_unknown_one_stay_as_typed(self) -> None:
        assert (
            _Catalog(ProviderConfig(name="catalog", url="http://h:1/api")).config.url
            == "http://h:1/api"
        )
        assert (
            _Somewhere(ProviderConfig(name="somewhere", url="http://h:1")).config.url
            == "http://h:1"
        )

    def test_an_empty_url_stays_empty(self) -> None:
        assert _Engine(ProviderConfig(name="engine", url="")).config.url == ""


class TestCapabilities:
    def test_the_base_composes_what_its_halves_say(self) -> None:
        client = _Engine(ProviderConfig(name="engine", url="http://h:1/v1"))
        assert client.capabilities == ProviderCapabilities(
            tokenizer=False,
            prompt_cache=False,
            model_management=False,
            supported_params=PROTOCOL_PARAMS,
        )

    def test_the_object_is_a_value_composed_on_each_read(self) -> None:
        client = _Engine(ProviderConfig(name="engine", url="http://h:1/v1"))
        assert client.capabilities == client.capabilities
        assert client.capabilities is not client.capabilities

    def test_the_base_has_no_account(self) -> None:
        assert _Engine(ProviderConfig(name="engine", url="http://h:1/v1")).balance() is None
