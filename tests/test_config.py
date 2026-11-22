"""config: default files, validation messages, model lookup and thinking levels."""

import json
import stat
from pathlib import Path

import pytest

from modules import config


PROVIDER = {"p": {"baseUrl": "http://x/v1", "models": [{"id": "m"}]}}


def load_error(write_settings, settings: dict | str, auth: dict | None = None) -> str:
    """Write settings, load them and return the exit message."""
    write_settings(settings, auth)
    with pytest.raises(SystemExit) as e:
        config.load()
    return str(e.value.code)


def test_first_run_writes_defaults(config_dir: Path) -> None:
    """Missing files are created from DEFAULT_FILES, private, and load into the default model."""
    cfg = config.load()
    for name in ("settings.json", "auth.json"):
        path = config_dir / name
        assert json.loads(path.read_text()) == config.DEFAULT_FILES[name]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert cfg.dir == config_dir
    assert [m.name for m in cfg.models] == ["llama.cpp/qwen3.6-35b", "llama.cpp/qwen3.8-27b"]
    assert cfg.default_model.name == "llama.cpp/qwen3.6-35b"
    assert cfg.default_model.api_key == "123"
    assert cfg.default_thinking == "off"
    assert cfg.prompt_file is None
    assert cfg.plugins == []


def test_default_reasoning_model_efforts() -> None:
    """thinkingLevelMap null entries drop those levels."""
    cfg = config.load()
    assert cfg.models[0].efforts == {}
    assert cfg.models[1].efforts == {"low": "low", "medium": "medium", "high": "high"}


def test_existing_files_are_not_overwritten(write_settings, config_dir: Path) -> None:
    """A user's settings survive load."""
    write_settings({"providers": PROVIDER}, {"p": "secret"})
    cfg = config.load()
    assert json.loads((config_dir / "settings.json").read_text()) == {"providers": PROVIDER}
    assert cfg.default_model == config.Model("p", "m", "http://x/v1", "secret", {})


def test_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without PY_AGENT_DIR, $XDG_CONFIG_HOME/py-agent is used."""
    monkeypatch.delenv("PY_AGENT_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config.load().dir == tmp_path / "xdg" / "py-agent"
    assert (tmp_path / "xdg" / "py-agent" / "settings.json").is_file()


def test_home_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without either variable, ~/.config/py-agent is used."""
    monkeypatch.delenv("PY_AGENT_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config._config_dir() == tmp_path / ".config" / "py-agent"


def test_py_agent_dir_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """~ in PY_AGENT_DIR is expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PY_AGENT_DIR", "~/pa")
    assert config._config_dir() == tmp_path / "pa"


@pytest.mark.parametrize("settings, expected", [
    ("{", "settings.json"),
    ("[]", "must be object, not array"),
    ({"providers": []}, "providers must be object, not array"),
    ({"providers": {"p": "x"}}, "providers.p must be object, not string"),
    ({"providers": {"p": {"models": [{"id": "m"}]}}}, "providers.p.baseUrl is missing"),
    ({"providers": {"p": {"baseUrl": 1, "models": []}}}, "baseUrl must be string, not number"),
    ({"providers": {"p": {"baseUrl": "u", "models": {}}}}, "providers.p.models must be array, not object"),
    ({"providers": {"p": {"baseUrl": "u", "models": ["m"]}}}, "models[0] must be object, not string"),
    ({"providers": {"p": {"baseUrl": "u", "models": [{}]}}}, "models[0].id is missing"),
    ({"providers": {"p": {"baseUrl": "u", "models": [{"id": 1}]}}}, "models[0].id must be string, not number"),
    ({"providers": {"p": {"baseUrl": "u", "models": [{"id": "m", "reasoning": True, "thinkingLevelMap": []}]}}},
     "models[0].thinkingLevelMap must be object"),
    ({"providers": {}}, "no models"),
    ({"providers": {"p": {"baseUrl": "u", "models": []}}}, "no models"),
    ({"providers": PROVIDER, "defaultModel": "z"}, "unknown defaultModel z"),
    ({"providers": PROVIDER, "defaultModel": 3}, "defaultModel must be string, not number"),
    ({"providers": PROVIDER, "defaultThinkingLevel": "huge"}, "unknown defaultThinkingLevel huge"),
    ({"providers": PROVIDER, "overrideSystemPrompt": "nope.md"}, "overrideSystemPrompt: no such file"),
    ({"providers": PROVIDER, "overrideSystemPrompt": 1}, "overrideSystemPrompt must be string"),
])
def test_bad_settings_exit_with_message(write_settings, settings, expected: str) -> None:
    """Every broken setting exits naming what is wrong."""
    assert expected in load_error(write_settings, settings)


def test_bad_auth(write_settings) -> None:
    """A non-string key in auth.json is reported with the provider name."""
    assert "auth.json: p must be string, not number" in load_error(write_settings, {"providers": PROVIDER}, {"p": 1})


def test_auth_not_object(write_settings, config_dir: Path) -> None:
    """auth.json must be an object."""
    write_settings({"providers": PROVIDER})
    (config_dir / "auth.json").write_text("[]")
    with pytest.raises(SystemExit) as e:
        config.load()
    assert "auth.json must be object" in str(e.value.code)


def test_missing_key_is_empty(write_settings) -> None:
    """A provider without an auth entry gets an empty key."""
    write_settings({"providers": PROVIDER}, {})
    assert config.load().default_model.api_key == ""


def test_default_model_and_thinking(write_settings) -> None:
    """defaultModel picks by id or provider/id; defaultThinkingLevel is kept."""
    write_settings({
        "providers": {
            "a": {"baseUrl": "u", "models": [{"id": "x"}, {"id": "y"}]},
            "b": {"baseUrl": "v", "models": [{"id": "y", "reasoning": True}]},
        },
        "defaultModel": "b/y",
        "defaultThinkingLevel": "high",
    })
    cfg = config.load()
    assert cfg.default_model.name == "b/y"
    assert cfg.default_thinking == "high"
    # bare id matches the first provider having it
    assert cfg.find_model("y").name == "a/y"
    assert cfg.find_model("a/x").base_url == "u"
    assert cfg.find_model("c/x") is None


def test_reasoning_efforts_map(write_settings) -> None:
    """Levels map to themselves, thinkingLevelMap renames them and null disables them."""
    write_settings({"providers": {"p": {"baseUrl": "u", "models": [
        {"id": "m", "reasoning": True, "thinkingLevelMap": {"max": "high", "minimal": None, "xhigh": None}},
        {"id": "n", "reasoning": False, "thinkingLevelMap": {"low": "x"}},
    ]}}})
    m, n = config.load().models
    assert m.efforts == {"low": "low", "medium": "medium", "high": "high", "max": "high"}
    assert n.efforts == {}


def test_override_system_prompt_relative_to_config_dir(write_settings, config_dir: Path) -> None:
    """overrideSystemPrompt is resolved against the config dir."""
    write_settings({"providers": PROVIDER, "overrideSystemPrompt": "mine.md"})
    (config_dir / "mine.md").write_text("custom")
    assert config.load().prompt_file == config_dir / "mine.md"


def test_override_system_prompt_absolute(write_settings, tmp_path: Path) -> None:
    """An absolute overrideSystemPrompt is used as is."""
    (tmp_path / "abs.md").write_text("custom")
    write_settings({"providers": PROVIDER, "overrideSystemPrompt": str(tmp_path / "abs.md")})
    assert config.load().prompt_file == tmp_path / "abs.md"


def test_plugins_found_sorted(write_settings, config_dir: Path) -> None:
    """Only plugin folders with main.py count, in name order."""
    write_settings({"providers": PROVIDER})
    for name in ("b", "a", "c"):
        (config_dir / "plugins" / name).mkdir(parents=True)
    (config_dir / "plugins" / "b" / "main.py").write_text("")
    (config_dir / "plugins" / "a" / "main.py").write_text("")
    assert config.load().plugins == [config_dir / "plugins" / "a" / "main.py", config_dir / "plugins" / "b" / "main.py"]


def test_model_supports() -> None:
    """off always works; other levels need an effort."""
    plain = config.Model("p", "m", "u", "", {})
    smart = config.Model("p", "s", "u", "", {"high": "high"})
    assert plain.supports("off") and not plain.supports("high")
    assert smart.supports("off") and smart.supports("high") and not smart.supports("low")
    assert smart.name == "p/s"


def test_config_history_path(config) -> None:
    """Input history lives in the config dir."""
    assert config.history == config.dir / "history"


def test_dataclasses_are_frozen(config) -> None:
    """Config and Model can't be changed in place."""
    with pytest.raises(AttributeError):
        config.default_thinking = "high"
    with pytest.raises(AttributeError):
        config.default_model.id = "x"
