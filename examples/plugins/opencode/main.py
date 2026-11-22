"""UI and behavior: look and feel of opencode; the pieces are in modules/, see README.md."""

from pathlib import Path

from textual.binding import Binding

from .modules import agents, prompt, slash, tools
from .modules.ui import OpenCodeMixin

# needs Agent.request_tools, api.snapshot and empty command replies
API_VERSION = 2
PROMPT = ("Be concise and direct; answer in a few lines unless asked for detail. Prefer editing existing files to "
          "creating new ones. Use the search tools (glob, grep, list) to find code instead of guessing, keep a todo "
          "list with todowrite for multi-step work, and hand self-contained research to the task tool.")


def setup(api) -> None:
    """Patch the prompt, add agents, tools and commands, and swap in an opencode style Chat."""
    build = api.prompt.build

    def build_prompt(cwd: Path, override: Path | None = None) -> str:
        # like opencode, a global AGENTS.md applies to every project
        text = build(cwd, override) + "\n\n" + PROMPT
        for path in (api.config.dir / "AGENTS.md", Path.home() / ".config" / "opencode" / "AGENTS.md"):
            if path.is_file():
                text += f"\n\nAdditional context: {path}\n\n{path.read_text(errors='replace')}"
        return text

    api.prompt.build = build_prompt
    specs = agents.install(api)
    tools.install(api, specs)
    slash.install(api)

    # textual only merges CSS and BINDINGS from widget classes, so they live here and not on the mixin
    class OpenCodeChat(OpenCodeMixin, api.Chat):
        Input = prompt.make_input(api.Chat.Input, api.commands, [s.name for s in specs if s.subagent])
        Picker = prompt.make_picker(api.Chat.Picker)
        # ctrl+p opens opencode's command list instead of textual's palette
        ENABLE_COMMAND_PALETTE = False
        CSS = api.Chat.CSS + """
        Screen { background: $background; }
        Screen.home { align: center middle; }
        #log { padding: 0 2; scrollbar-size-vertical: 1; scrollbar-color: $panel; scrollbar-color-hover: $panel;
               scrollbar-color-active: $panel; scrollbar-background: $background; scrollbar-background-hover: $background;
               scrollbar-background-active: $background; }
        .home #log { height: auto; width: 80; }
        #logo, #tips { display: none; height: auto; }
        .home #logo { display: block; width: 80; margin-bottom: 2; text-align: center; }
        .home #tips { display: block; width: 80; margin-top: 1; padding: 0 2; }
        .home PromptInput, .home #meta, .home #hints, .home #menu { width: 80; }
        #header { dock: top; height: 1; padding: 0 2; margin-bottom: 1; }
        .home #header, .with-sidebar #header { display: none; }
        #sidebar { dock: right; width: 42; height: 1fr; padding: 1 2; background: $surface; display: none; }
        .with-sidebar #sidebar { display: block; }
        .user { color: $foreground; background: $surface; padding: 1 2; text-style: none; border-left: thick $secondary; }
        #log > Markdown { padding: 0 1 0 3; margin-bottom: 1; }
        .reasoning { color: $foreground-muted; text-style: italic; border-left: thick $panel; margin-left: 3; padding: 0 1; }
        .hide-thinking .reasoning { display: none; }
        .tool, .footer, .info { padding: 0 3; height: auto; }
        .info { color: $foreground-muted; }
        .error { color: $error; background: $surface; border-left: thick $error; padding: 1 2; }
        PromptInput, PromptInput:focus { border: none; border-left: thick $secondary; background: $surface;
                                         padding: 1 2 0 2; height: auto; max-height: 12; }
        #meta { height: auto; border-left: thick $secondary; background: $surface; padding: 1 2; }
        #hints { height: 1; padding: 0 1; }
        #menu { background: $surface; padding: 0; height: auto; }
        Picker { height: auto; background: $surface; border-left: thick $warning; padding: 1 2; margin-bottom: 1; }
        #status { dock: bottom; height: 1; padding: 0 2; color: $foreground-muted; }
        """
        BINDINGS = [Binding("ctrl+x", "leader", "leader key", priority=True),
                    Binding("ctrl+p", "palette", "commands", priority=True),
                    Binding("ctrl+t", "cycle_thinking", "thinking level", priority=True),
                    Binding("ctrl+c", "clear_or_quit", "clear or quit", priority=True),
                    Binding("pageup", "scroll_log(-1)", "scroll up", priority=True),
                    Binding("pagedown", "scroll_log(1)", "scroll down", priority=True)]

    OpenCodeChat.api, OpenCodeChat.specs = api, specs
    api.Chat = OpenCodeChat
