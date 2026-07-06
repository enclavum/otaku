"""Tests for the TOML config loader and last_model helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaku import config


class TestDefaultConfig:
    def test_contains_all_sections(self) -> None:
        text = config.default_config()
        assert "[database]" in text
        assert "[encryption]" in text
        assert "[providers.ollama]" in text
        assert "[providers.lmstudio]" in text
        assert "[providers.omlx]" in text


class TestLoad:
    def test_writes_default_file_on_first_run(self) -> None:
        assert not config.CONFIG_PATH.exists()
        cfg = config.load()
        assert config.CONFIG_PATH.exists()
        assert set(cfg.providers) == {"ollama", "lmstudio", "omlx"}
        assert cfg.encryption.provider == "keychain"

    def test_first_run_bakes_in_detected_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Detection runs once, when the file is first written; the detected port
        # is persisted into the [providers.ollama] section and read back.
        monkeypatch.setenv("OLLAMA_HOST", ":9000")
        cfg = config.load()
        assert cfg.providers["ollama"].url == "http://localhost:9000/v1"
        assert "http://localhost:9000/v1" in config.CONFIG_PATH.read_text()

    def test_first_run_uses_default_ports_without_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        cfg = config.load()
        assert cfg.providers["ollama"].url == "http://localhost:11434/v1"
        assert cfg.providers["omlx"].url == "http://localhost:8000/v1"

    def test_existing_config_is_not_re_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Once config.toml exists, detection never runs again — the file wins
        # even if the environment now suggests a different port.
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        config.load()  # writes with default 11434
        monkeypatch.setenv("OLLAMA_HOST", ":9000")  # would detect 9000 if it re-ran
        cfg = config.load()  # file already exists → parse only
        assert cfg.providers["ollama"].url == "http://localhost:11434/v1"

    def test_provider_fields_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text(
            "[providers.foo]\n"
            'url = "http://host:1234/v1/"\n'
            'api_key = "sk-xyz"\n'
            "supports_thinking = true\n"
            'keep_alive = "1h"\n'
        )
        cfg = config.load(p)
        foo = cfg.providers["foo"]
        assert foo.name == "foo"
        assert foo.url == "http://host:1234/v1"  # trailing slash stripped
        assert foo.api_key == "sk-xyz"
        assert foo.supports_thinking is True
        assert foo.keep_alive == "1h"

    def test_provider_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers.bar]\nurl = "http://x/v1"\n')
        bar = config.load(p).providers["bar"]
        assert bar.api_key == ""
        assert bar.supports_thinking is False
        assert bar.keep_alive == "24h"
        assert bar.smoothen_streaming is False

    def test_smoothen_streaming_flag_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers.omlx]\nurl = "http://x/v1"\nsmoothen_streaming = true\n')
        assert config.load(p).providers["omlx"].smoothen_streaming is True

    def test_encryption_section_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text(
            '[providers.x]\nurl = "http://x/v1"\n'
            "[encryption]\n"
            'provider = "command"\n'
            'retrieve_command = "op read foo"\n'
        )
        enc = config.load(p).encryption
        assert enc.provider == "command"
        assert enc.retrieve_command == "op read foo"

    def test_encryption_defaults_when_absent(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers.x]\nurl = "http://x/v1"\n')
        enc = config.load(p).encryption
        assert enc.provider == "keychain"
        assert enc.retrieve_command is None


class TestLoadErrors:
    def test_no_providers_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[database]\nurl = "sqlite:///x.db"\n')
        with pytest.raises(ValueError, match="at least one"):
            config.load(p)

    def test_provider_not_a_table_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers]\nfoo = "not-a-table"\n')
        with pytest.raises(ValueError, match="must be a table"):
            config.load(p)

    def test_provider_missing_url_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers.foo]\napi_key = "x"\n')
        with pytest.raises(ValueError, match="missing required key 'url'"):
            config.load(p)

    def test_encryption_not_a_table_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        # top-level scalar `encryption` (declared before any section)
        p.write_text('encryption = "nope"\n[providers.x]\nurl = "http://x/v1"\n')
        with pytest.raises(ValueError, match="must be a table"):
            config.load(p)


class TestConfigDir:
    def test_env_var_overrides_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(config.CONFIG_DIR_ENV, str(tmp_path / "custom"))
        assert config._default_config_dir() == tmp_path / "custom"

    def test_env_var_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.CONFIG_DIR_ENV, "~/elsewhere")
        assert config._default_config_dir() == Path.home() / "elsewhere"

    def test_defaults_to_dot_otaku_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.CONFIG_DIR_ENV, raising=False)
        assert config._default_config_dir() == Path.home() / ".otaku"

    def test_empty_value_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.CONFIG_DIR_ENV, "")
        assert config._default_config_dir() == Path.home() / ".otaku"

    def test_custom_dir_is_fully_isolated(self, tmp_path: Path) -> None:
        """End-to-end in a child process (the path constants are computed at
        import): with OTAKU_CONFIG_DIR set, the default DB, the keystore, and
        the keychain item name must all land inside the custom dir, and
        nothing may be created under ~/.otaku."""
        home = tmp_path / "child_home"  # the fixture's "home" pre-creates .otaku
        home.mkdir()
        alt = tmp_path / "alt"
        env = os.environ.copy()
        env["HOME"] = str(home)
        env[config.CONFIG_DIR_ENV] = str(alt)
        env.pop(config.DATABASE_URL_ENV, None)
        script = (
            "import json\n"
            "from pathlib import Path\n"
            "from otaku import config\n"
            "from otaku.storage import crypto\n"
            "from otaku.storage.store import _db_path\n"
            "cfg = config.load()\n"
            "print(json.dumps({\n"
            "    'db': str(_db_path(cfg.database_url)),\n"
            "    'service': crypto._KC_SERVICE,\n"
            "    'keystore': str(crypto.KEYSTORE_PATH),\n"
            "    'home_otaku_exists': (Path.home() / '.otaku').exists(),\n"
            "}))\n"
        )
        r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["db"] == str(alt / "history.db")
        assert out["service"] == f"otaku:{alt}"
        assert out["keystore"] == str(alt / "keys.json")
        assert out["home_otaku_exists"] is False


class TestRememberRefusesCorruptFile:
    def test_corrupt_json_raises_and_preserves_file(self) -> None:
        config.MODEL_DEFAULTS_PATH.write_text("{not json")
        with pytest.raises(ValueError, match="unreadable"):
            config.remember_model_settings("m", config.Settings(think="low"))
        assert config.MODEL_DEFAULTS_PATH.read_text() == "{not json"

    def test_non_object_json_raises_and_preserves_file(self) -> None:
        config.MODEL_DEFAULTS_PATH.write_text("[1, 2]")
        with pytest.raises(ValueError, match="not a JSON object"):
            config.remember_model_settings("m", config.Settings(think="low"))
        assert config.MODEL_DEFAULTS_PATH.read_text() == "[1, 2]"

    def test_write_leaves_no_tmp_file(self) -> None:
        config.remember_model_settings("m", config.Settings(think="low"))
        leftovers = list(config.MODEL_DEFAULTS_PATH.parent.glob("*.tmp"))
        assert leftovers == []
        assert "m" in config._read_model_defaults()


class TestDatabaseUrl:
    def test_env_var_overrides_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.DATABASE_URL_ENV, "sqlite:///from-env.db")
        p = tmp_path / "c.toml"
        p.write_text(
            '[database]\nurl = "sqlite:///from-file.db"\n[providers.x]\nurl = "http://x/v1"\n'
        )
        assert config.load(p).database_url == "sqlite:///from-env.db"

    def test_file_url_used_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config.DATABASE_URL_ENV, raising=False)
        p = tmp_path / "c.toml"
        p.write_text(
            '[database]\nurl = "sqlite:///from-file.db"\n[providers.x]\nurl = "http://x/v1"\n'
        )
        assert config.load(p).database_url == "sqlite:///from-file.db"

    def test_default_url_when_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.DATABASE_URL_ENV, raising=False)
        from otaku.storage.store import Store

        p = tmp_path / "c.toml"
        p.write_text('[providers.x]\nurl = "http://x/v1"\n')
        assert config.load(p).database_url == Store.DEFAULT_URL


class TestDefaults:
    def test_parses_defaults_section(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text(
            '[providers.x]\nurl = "http://x/v1"\n'
            '[defaults]\nsystem = "Be brief."\nthink = "medium"\nverbose = true\nno_record = true\n'
            "[defaults.parameters]\ntemperature = 0.3\n"
        )
        cfg = config.load(p)
        assert cfg.defaults.system == "Be brief."
        assert cfg.defaults.think == "medium"
        assert cfg.verbose is True  # Config-level, like no_record — not part of Settings
        assert cfg.defaults.parameters == {"temperature": 0.3}
        assert cfg.no_record is True

    def test_absent_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('[providers.x]\nurl = "http://x/v1"\n')
        cfg = config.load(p)
        assert cfg.defaults == config.Settings()
        assert cfg.no_record is False
        assert cfg.verbose is False
        assert cfg.model_defaults == {}

    def test_defaults_not_a_table_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text('defaults = "nope"\n[providers.x]\nurl = "http://x/v1"\n')
        with pytest.raises(ValueError, match="must be a table"):
            config.load(p)

    def test_default_config_has_defaults_section(self) -> None:
        assert "[defaults]" in config.default_config()


class TestSettingsFor:
    @staticmethod
    def _cfg(defaults: config.Settings, model_defaults: dict) -> config.Config:
        return config.Config(
            database_url="x",
            providers={},
            encryption=config.Encryption(),
            defaults=defaults,
            model_defaults=model_defaults,
        )

    def test_no_override_returns_global(self) -> None:
        d = config.Settings(system="g", think="none")
        assert config.settings_for(self._cfg(d, {}), "m") is d

    def test_per_model_overrides_and_inherits(self) -> None:
        cfg = self._cfg(
            config.Settings(
                system="g", think="none", parameters={"temperature": 0.7, "top_p": 0.9}
            ),
            {"deepseek": config.Settings(think="high", parameters={"temperature": 0.5})},
        )
        s = config.settings_for(cfg, "deepseek")
        assert s.system == "g"  # inherited from global
        assert s.think == "high"  # overridden per-model
        assert s.parameters == {"temperature": 0.5, "top_p": 0.9}  # merged, per-model wins

    def test_unset_per_model_fields_inherit(self) -> None:
        cfg = self._cfg(
            config.Settings(system="g", think="medium"),
            {"m": config.Settings(parameters={"seed": 1})},
        )
        s = config.settings_for(cfg, "m")
        assert (s.system, s.think, s.parameters) == ("g", "medium", {"seed": 1})


class TestRememberModelSettings:
    def test_roundtrip(self) -> None:
        config.remember_model_settings(
            "m", config.Settings(system="s", think="high", parameters={"temperature": 0.5})
        )
        md = config._read_model_defaults()
        assert md["m"].system == "s"
        assert md["m"].think == "high"
        assert md["m"].parameters == {"temperature": 0.5}
        assert config.MODEL_DEFAULTS_PATH.exists()

    def test_preserves_other_models(self) -> None:
        config.remember_model_settings("a", config.Settings(think="high"))
        config.remember_model_settings("b", config.Settings(think="low"))
        assert set(config._read_model_defaults()) == {"a", "b"}

    def test_empty_settings_clears_entry(self) -> None:
        config.remember_model_settings("a", config.Settings(think="high"))
        config.remember_model_settings("a", config.Settings())
        assert "a" not in config._read_model_defaults()

    def test_malformed_json_is_ignored(self) -> None:
        config.MODEL_DEFAULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.MODEL_DEFAULTS_PATH.write_text("{ not valid json")
        assert config._read_model_defaults() == {}


class TestLastModel:
    def test_roundtrip(self) -> None:
        assert config.read_last_model() is None
        config.write_last_model("ollama/llama3")
        assert config.read_last_model() == "ollama/llama3"

    def test_empty_file_reads_as_none(self) -> None:
        config.LAST_MODEL_PATH.write_text("   \n")
        assert config.read_last_model() is None

    def test_write_strips_and_appends_newline(self) -> None:
        config.write_last_model("lmstudio/qwen")
        assert config.LAST_MODEL_PATH.read_text() == "lmstudio/qwen\n"
