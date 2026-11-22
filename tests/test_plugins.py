"""plugins: Api helpers, loading plugin folders, and the bundled example plugins."""

import shutil
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from fakes import FakeLLM, assistant, call
from modules import agent, commands, config, llm, plugins, prompt, tools, tui
from modules.config import Config


EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def with_plugins(cfg: Config, *mains: Path) -> plugins.Api:
    """An Api whose config lists the given plugin main.py files."""
    return plugins.Api(replace(cfg, plugins=list(mains)), cfg.dir)


def make_plugin(root: Path, name: str, code: str, **files: str) -> Path:
    """Write a plugin folder and return its main.py."""
    folder = root / "plugins" / name
    folder.mkdir(parents=True)
    for file, text in files.items():
        (folder / file).write_text(text)
    (folder / "main.py").write_text(code)
    return folder / "main.py"


def test_api_exposes_modules_and_classes(config: Config, workdir: Path) -> None:
    """Api holds the modules, classes and empty instance slots."""
    api = plugins.Api(config, workdir)
    assert (api.llm, api.prompt, api.tools, api.commands) == (llm, prompt, tools, commands)
    assert api.Agent is agent.Agent and api.Chat is tui.Chat
    assert api.agent is None and api.chat is None
    assert api.config is config and api.cwd == workdir


def test_add_tool(config: Config, workdir: Path) -> None:
    """add_tool registers the function, updates SPECS in place and the system prompt."""
    specs = tools.SPECS

    def shout(cwd: Path, stop: Event, text: str) -> str:
        return text.upper()

    plugins.Api(config, workdir).add_tool(shout, "Shout text.", {"text": {"type": "string"}}, ["text"])
    assert tools.SPECS is specs
    assert specs[-1]["function"]["name"] == "shout"
    assert specs[-1]["function"]["parameters"]["required"] == ["text"]
    assert tools.run("shout", '{"text": "hi"}', workdir, Event()) == "HI"
    assert "- shout: Shout text." in prompt.build(workdir)


def test_add_tool_replaces_same_name(config: Config, workdir: Path) -> None:
    """A tool with a built-in's name replaces it without duplicating the spec."""
    def bash(cwd: Path, stop: Event, command: str) -> str:
        return "nope"

    plugins.Api(config, workdir).add_tool(bash, "No bash.", {"command": {"type": "string"}}, ["command"])
    assert [s["function"]["name"] for s in tools.SPECS] == ["read", "write", "edit", "bash"]
    assert tools.run("bash", '{"command": "ls"}', workdir, Event()) == "nope"


def test_add_command(config: Config, workdir: Path) -> None:
    """add_command registers a handler and adds the name to autocomplete once."""
    api = plugins.Api(config, workdir)
    api.add_command("/hi", lambda a, arg: ("hi", None))
    api.add_command("/hi", lambda a, arg: ("hi2", None))
    api.add_command("/help", lambda a, arg: ("mine", None))
    assert commands.NAMES.count("/hi") == 1 and commands.NAMES.count("/help") == 1
    assert commands.run(None, "/hi") == ("hi2", None)


def test_load_runs_setup_in_order(config: Config, tmp_path: Path) -> None:
    """Plugins load in config order, as packages that can import their own files."""
    order = tmp_path / "order"
    a = make_plugin(tmp_path, "a", f"from .helper import X\ndef setup(api):\n    open({str(order)!r}, 'a').write(X)\n",
                    **{"helper.py": "X = 'a'"})
    b = make_plugin(tmp_path, "b", f"def setup(api):\n    open({str(order)!r}, 'a').write('b')\n"
                                   "    api.add_command('/b', lambda agent, arg: ('b', None))\n")
    plugins.load(with_plugins(config, a, b))
    assert order.read_text() == "ab"
    assert "py_agent_plugins.a" in sys.modules
    assert commands.run(None, "/b") == ("b", None)


def test_plugin_error_propagates(config: Config, tmp_path: Path) -> None:
    """A broken plugin stops startup with its own exception."""
    bad = make_plugin(tmp_path, "bad", "def setup(api):\n    raise RuntimeError('broken plugin')\n")
    with pytest.raises(RuntimeError, match="broken plugin"):
        plugins.load(with_plugins(config, bad))


def test_plugin_without_setup(config: Config, tmp_path: Path) -> None:
    """main.py without setup is an error, not silently ignored."""
    with pytest.raises(AttributeError):
        plugins.load(with_plugins(config, make_plugin(tmp_path, "empty", "")))


def test_plugin_found_by_config_load(write_settings, config_dir: Path, workdir: Path) -> None:
    """A folder in <config dir>/plugins is picked up by config.load and run by plugins.load."""
    write_settings({"providers": {"p": {"baseUrl": "u", "models": [{"id": "m"}]}}})
    make_plugin(config_dir, "hello", "def setup(api):\n    api.add_command('/hello', lambda agent, arg: ('hello!', None))\n")
    plugins.load(plugins.Api(config.load(), workdir))
    assert commands.run(None, "/hello") == ("hello!", None)


# example plugins: copied into the config dir and loaded like a user would

def load_example(cfg: Config, name: str) -> plugins.Api:
    """Copy an example plugin into the config dir, load it and create the agent."""
    shutil.copytree(EXAMPLES / name, cfg.dir / "plugins" / name)
    api = with_plugins(cfg, cfg.dir / "plugins" / name / "main.py")
    plugins.load(api)
    api.agent = api.Agent(api.config, api.cwd)
    return api


@pytest.mark.parametrize("name", sorted(p.name for p in EXAMPLES.iterdir()))
def test_every_example_loads(config: Config, name: str) -> None:
    """Each example plugin sets up without errors."""
    load_example(config, name)


class Silent:
    """Events that ignore everything."""

    def __getattr__(self, name):
        return lambda *a: None


def bash_result(api: plugins.Api, fake_llm: FakeLLM, **args) -> str:
    """What the model gets back from one bash call."""
    fake_llm.replies += [assistant(calls=[call("1", "bash", **args)]), assistant("done")]
    api.agent.run("go", Silent())
    return next(m["content"] for m in api.agent.messages if m["role"] == "tool")


@pytest.mark.parametrize("command", ["sudo rm x", "rm -rf /", "rm -rf ~", "git push --force", "git push origin -f"])
def test_bash_guard_blocks(config: Config, fake_llm: FakeLLM, command: str) -> None:
    """bash_guard refuses risky commands."""
    api = load_example(config, "bash_guard")
    assert bash_result(api, fake_llm, command=command).startswith("Error: blocked by bash_guard")


def test_bash_guard_allows(config: Config, fake_llm: FakeLLM) -> None:
    """bash_guard passes safe commands and extra args through."""
    api = load_example(config, "bash_guard")
    assert bash_result(api, fake_llm, command="echo ok", timeout=5) == "ok"


def test_cost_counts_usage(config: Config, fake_llm: FakeLLM) -> None:
    """cost adds up tokens of every request."""
    api = load_example(config, "cost")
    fake_llm.replies += [assistant("a", usage={"prompt_tokens": 1000, "completion_tokens": 10}),
                         assistant("b", usage={"prompt_tokens": 2000, "completion_tokens": 20})]

    api.agent.run("1", Silent())
    api.agent.run("2", Silent())
    assert commands.run(api.agent, "/cost") == ("3,000 input · 30 output tokens · $0.0000", None)


def test_stats(config: Config) -> None:
    """stats counts messages by role."""
    api = load_example(config, "stats")
    reply, action = commands.run(api.agent, "/stats")
    assert action is None and reply.startswith("1 system · ~")


def test_git_context(config: Config, git_workdir: Path) -> None:
    """git_context appends git status to the system prompt inside a repo only."""
    load_example(config, "git_context")
    (git_workdir / "new.txt").write_text("")
    assert "Git status:\n## " in prompt.build(git_workdir) and "?? new.txt" in prompt.build(git_workdir)
    assert "Git status" not in prompt.build(config.dir)
