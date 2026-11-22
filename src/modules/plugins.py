"""Plugins: every folder in <config dir>/plugins/ with a main.py defining setup(api)."""

import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path

from . import agent, commands, config, files, llm, prompt, tools, tui


class Api:
    """Everything py-agent is made of; setup(api) runs before the agent and ui are created."""

    # bumped when the api changes; a plugin's main.py can require one with API_VERSION = n
    VERSION = 1

    def __init__(self, cfg: config.Config, cwd: Path) -> None:
        self.config, self.cwd = cfg, cwd
        # modules: patch their functions or tables to change behavior
        self.llm, self.prompt, self.tools, self.commands, self.files = llm, prompt, tools, commands, files
        # classes created at startup: replace with subclasses to extend
        self.Agent, self.Chat = agent.Agent, tui.Chat
        # the running instances, set after all plugins are set up
        self.agent: agent.Agent | None = None
        self.chat: tui.Chat | None = None

    def add_tool(self, function: Callable[..., str], description: str, props: dict, required: list[str]) -> None:
        """Register a tool called as function(cwd, stop, **args), returning text."""
        function, spec = tools._spec(function, description, props, required)
        tools.TOOLS[function.__name__] = (function, spec)
        # in place, since agent and prompt hold this list
        tools.SPECS[:] = [spec for _, spec in tools.TOOLS.values()]

    def settings(self, name: str) -> dict:
        """The plugin's section in settings.json ("plugins": {name: {...}}), empty if missing."""
        return self.config.plugin_settings.get(name, {})

    def add_command(self, name: str, handler: Callable[[agent.Agent, str], tuple[str, str | None]],
                    description: str = "", arguments: str = "") -> None:
        """Register a slash command; handler(agent, arg) returns (reply, ui action, see commands.run)."""
        commands.EXTRA[name] = handler
        commands.DESCRIPTIONS[name] = (arguments, description)
        if name not in commands.NAMES:
            commands.NAMES.append(name)


def load(api: Api) -> None:
    """Import each enabled plugin's main.py and call its setup(api)."""
    # parent package, so plugins can import their own files with "from . import x" too
    sys.modules.setdefault("py_agent_plugins", types.ModuleType("py_agent_plugins"))
    for path in api.config.plugins:
        name = f"py_agent_plugins.{path.parent.name}"
        # loaded as a package so the plugin can import its own files relatively
        spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        if (needed := getattr(module, "API_VERSION", 1)) > api.VERSION:
            sys.exit(f"plugin {path.parent.name} needs plugin api {needed}, this py-agent has {api.VERSION}: update py-agent")
        module.setup(api)

