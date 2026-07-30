"""`otaku logs`: the day-rotated request and system logs, printed."""

from scenarios.support.harness import App, run_otaku


class TestRequests:
    def test_todays_requests_print_what_was_sent(self, app: App) -> None:
        app.play("I enter the hall.")
        result = run_otaku(app.paths.root, "logs", "requests")
        assert result.returncode == 0
        assert "I enter the hall." in result.stdout
        assert "[chat]" in result.stdout  # every entry is tagged with its purpose

    def test_bare_logs_lists_the_subcommands(self, app: App) -> None:
        # No default: bare `otaku logs` shows what there is to show.
        result = run_otaku(app.paths.root, "logs")
        listing = result.stdout + result.stderr
        for name in ("requests", "system", "error"):
            assert name in listing

    def test_list_names_the_days(self, app: App) -> None:
        app.play("I enter the hall.")
        result = run_otaku(app.paths.root, "logs", "requests", "--list")
        assert result.returncode == 0
        assert " B" in result.stdout  # a day and its size

    def test_a_day_without_logs_is_refused(self, app: App) -> None:
        result = run_otaku(app.paths.root, "logs", "requests", "2001-01-01")
        assert result.returncode == 1
        assert "no request log for 2001-01-01" in result.stderr

    def test_a_malformed_day_is_refused(self, app: App) -> None:
        result = run_otaku(app.paths.root, "logs", "requests", "yesterday")
        assert result.returncode == 2
        assert "DAY must be" in result.stderr


class TestErrors:
    def test_a_contained_crash_prints_and_a_day_without_refuses(
        self, app: App, capsys, monkeypatch
    ) -> None:
        def boom(session: object, store: object, args: object) -> None:
            raise RuntimeError("boom")

        from otaku.chat import commands

        monkeypatch.setitem(commands.COMMANDS, "/help", (boom, None))
        app.play("/help")
        result = run_otaku(app.paths.root, "logs", "error")
        assert result.returncode == 0
        assert "RuntimeError: boom" in result.stdout
        refused = run_otaku(app.paths.root, "logs", "error", "2001-01-01")
        assert refused.returncode == 1
        assert "no error log for 2001-01-01" in refused.stderr


class TestSystem:
    def test_the_workers_account_prints(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        result = run_otaku(app.paths.root, "logs", "system")
        assert result.returncode == 0
        # Long operations log uniformly: a started line, and a finished
        # line carrying the elapsed time.
        assert "extraction started (story" in result.stdout
        assert "extraction finished (story" in result.stdout
        assert "scene close started (story" in result.stdout
        assert "scene close finished (story" in result.stdout

    def test_a_day_without_logs_is_refused(self, app: App) -> None:
        result = run_otaku(app.paths.root, "logs", "system", "2001-01-01")
        assert result.returncode == 1
        assert "no system log" in result.stderr
