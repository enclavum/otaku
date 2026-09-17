"""What an engine's autoconfigure reads off this machine's processes.

`launched_port` finds the `--port` a running engine was launched with,
by the executable's bare name however the command spells it, in either
flag spelling, and passes over a llama-server that is Ollama's or LM
Studio's own — theirs, never an engine to configure. No such process,
no flag, or no processes at all is None, so a launch never depends on
it. `_parse_ollama_host` reads OLLAMA_HOST the way Ollama itself does.
"""

from otaku.providers.clients import launched_port
from otaku.providers.clients.ollama import _parse_ollama_host


class TestLaunchedPort:
    def test_the_flag_is_read_in_either_spelling(self) -> None:
        assert (
            launched_port("llama-server", ["/opt/bin/llama-server -m x.gguf --port 8099"]) == 8099
        )
        assert launched_port("koboldcpp", ["koboldcpp --model x.gguf --port=5099"]) == 5099

    def test_the_bare_name_matches_the_executable_however_it_is_spelled(self) -> None:
        assert launched_port("llama-server", ['"C:\\Tools\\llama-server.exe" --port 8081']) == 8081
        assert launched_port("llama-server", ["/usr/local/bin/llama-server --port 8082"]) == 8082

    def test_a_bundled_child_is_never_an_engine(self) -> None:
        commands = [
            "/Applications/Ollama.app/Contents/Resources/llama-server --port 53243",
            "/Users/x/.lmstudio/extensions/backends/llama-server --port 41000",
            "/Applications/LM Studio.app/Contents/Resources/llama-server --port 41001",
        ]
        assert launched_port("llama-server", commands) is None

    def test_the_first_engine_of_its_name_answers(self) -> None:
        commands = [
            "/Applications/Ollama.app/Contents/Resources/llama-server --port 1",
            "/opt/bin/llama-server --port 8080",
            "/opt/bin/llama-server --port 8081",
        ]
        assert launched_port("llama-server", commands) == 8080

    def test_no_flag_no_process_no_processes_is_none(self) -> None:
        assert launched_port("llama-server", ["/opt/bin/llama-server -m x.gguf"]) is None
        assert launched_port("llama-server", ["/opt/bin/ollama serve --port 8080"]) is None
        assert launched_port("llama-server", []) is None

    def test_a_flag_inside_another_word_does_not_count(self) -> None:
        assert launched_port("llama-server", ["/opt/bin/llama-server --report 5"]) is None
        assert launched_port("llama-server", ["/opt/bin/llama-server --port-file p"]) is None

    def test_a_release_build_carries_its_platform_in_the_name(self) -> None:
        assert launched_port("koboldcpp", ["/Users/x/koboldcpp-mac-arm64 --port 5002"]) == 5002
        assert (
            launched_port("koboldcpp", ["./koboldcpp-linux-x64-nocuda m.gguf --port 5003"]) == 5003
        )
        assert launched_port("koboldcpp", ['"C:\\kcpp\\koboldcpp-nocuda.exe" --port 5004']) == 5004
        assert launched_port("koboldcpp", ["/opt/koboldcpp-tools/other --port 5005"]) is None

    def test_a_script_run_by_python_is_named_by_the_script(self) -> None:
        assert launched_port("koboldcpp", ["python koboldcpp.py --port 5006"]) == 5006
        assert (
            launched_port("koboldcpp", ["python3 /Users/x/kcpp/koboldcpp.py m.gguf --port 5007"])
            == 5007
        )

    def test_a_path_with_spaces_stays_whole(self) -> None:
        assert (
            launched_port("llama-server", ['"C:\\Program Files\\x\\llama-server.exe" --port 8083'])
            == 8083
        )
        assert (
            launched_port("llama-server", ["/Users/x/My Tools/llama-server -m x.gguf --port 8084"])
            == 8084
        )

    def test_a_positional_model_beside_the_name_is_fine(self) -> None:
        assert launched_port("koboldcpp", ["koboldcpp gemma.gguf --port 5008"]) == 5008

    def test_koboldcpps_positional_port_counts_when_no_flag_does(self) -> None:
        # KoboldCpp's own docs spell the port positionally, after the model.
        assert launched_port("koboldcpp", ["koboldcpp gemma.gguf 5014"]) == 5014
        assert launched_port("koboldcpp", ["/opt/koboldcpp-mac-arm64 m.gguf 5015 --admin"]) == 5015
        # The flag wins; a number behind a flag is that flag's, not a port.
        assert launched_port("koboldcpp", ["koboldcpp gemma.gguf 5014 --port 5009"]) == 5009
        assert launched_port("llama-server", ["/opt/bin/llama-server -c 4096 -m x.gguf"]) is None

    def test_the_lowest_port_wins_over_a_routers_children(self) -> None:
        # A router's children are llama-servers too, on ephemeral ports,
        # and `ps` lists them in no particular order.
        child = (
            "/opt/homebrew/bin/llama-server --host 127.0.0.1 --port 62266 --alias a --model a.gguf"
        )
        parent = "/opt/homebrew/bin/llama-server --models-dir /models --port 8091"
        assert launched_port("llama-server", [child, parent]) == 8091
        assert launched_port("llama-server", [parent, child]) == 8091


class TestOllamaHost:
    def test_empty_is_the_local_default(self) -> None:
        assert _parse_ollama_host("") == ("http", "localhost", 11434, "")

    def test_a_host_and_port_are_read_in_every_spelling(self) -> None:
        assert _parse_ollama_host("box:8080") == ("http", "box", 8080, "")
        assert _parse_ollama_host(":8080") == ("http", "localhost", 8080, "")
        assert _parse_ollama_host("box") == ("http", "box", 11434, "")
        # A bare number is a HOST to Ollama, not a port.
        assert _parse_ollama_host("8080") == ("http", "8080", 11434, "")

    def test_a_scheme_is_kept_and_sets_the_missing_port(self) -> None:
        assert _parse_ollama_host("http://box") == ("http", "box", 80, "")
        assert _parse_ollama_host("https://box") == ("https", "box", 443, "")
        assert _parse_ollama_host("https://box:8443") == ("https", "box", 8443, "")

    def test_a_path_is_kept_as_ollamas_client_keeps_it(self) -> None:
        assert _parse_ollama_host("http://box:8080/") == ("http", "box", 8080, "")
        assert _parse_ollama_host("https://box/ollama") == ("https", "box", 443, "/ollama")
        assert _parse_ollama_host("http://box:8080/a/b/") == ("http", "box", 8080, "/a/b")

    def test_ipv6_is_bracketed_and_quotes_are_shed(self) -> None:
        assert _parse_ollama_host("::1") == ("http", "[::1]", 11434, "")
        assert _parse_ollama_host("[::1]:8080") == ("http", "[::1]", 8080, "")
        assert _parse_ollama_host("http://[::1]") == ("http", "[::1]", 80, "")
        assert _parse_ollama_host('"box:8080"') == ("http", "box", 8080, "")

    def test_a_bad_port_falls_back(self) -> None:
        assert _parse_ollama_host("box:99999") == ("http", "box", 11434, "")
        assert _parse_ollama_host("box:eighty") == ("http", "box", 11434, "")
