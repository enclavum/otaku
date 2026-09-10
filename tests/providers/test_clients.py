"""What an engine's autoconfigure reads off this machine's processes.

`launched_port` finds the `--port` a running engine was launched with,
by the executable's bare name, in either flag spelling, and passes over
a llama-server that is Ollama's or LM Studio's own — theirs, never an
engine to configure. No such process, no flag, or no processes at all
is None, so a launch never depends on it.
"""

from otaku.providers.clients import launched_port


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
