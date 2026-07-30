"""`otaku logs`: the day-rotated request and system logs, printed."""

from scenarios.support.harness import App, run_otaku


class TestRequests:
    def test_todays_requests_print_what_was_sent(self, app: App) -> None:
        app.play("I enter the hall.")
        result = run_otaku(app.paths.root, "logs", "requests")
        assert result.returncode == 0
        assert "I enter the hall." in result.stdout
        assert "[chat]" in result.stdout  # every entry is tagged with its purpose

    def test_bare_logs_defaults_to_requests(self, app: App) -> None:
        app.play("I enter the hall.")
        assert "I enter the hall." in run_otaku(app.paths.root, "logs").stdout

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


class TestSystem:
    def test_the_workers_account_prints(self, app: App) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        result = run_otaku(app.paths.root, "logs", "system")
        assert result.returncode == 0
        assert result.stdout.strip()

    def test_a_day_without_logs_is_refused(self, app: App) -> None:
        result = run_otaku(app.paths.root, "logs", "system", "2001-01-01")
        assert result.returncode == 1
        assert "no system log" in result.stderr
