"""UI and behavior: look and feel of claude code; the pieces are in modules/, see README.md."""

from textual.binding import Binding

from .modules import approval, completion, context, guard, modes, picker, slash, todo
from .modules.context import CompactMixin
from .modules.look import LookMixin
from .modules.modes import ModesMixin
from .modules.render import ACCENT
from .modules.shortcuts import ShortcutsMixin

PROMPT = "Be concise and direct. Prefer editing existing files to creating new ones. Don't add comments unless asked."


def setup(api) -> None:
    """Patch the prompt, extend the agent, add tools and commands, and swap in a claude code style Chat."""
    build = api.prompt.build
    api.prompt.build = lambda cwd, override=None: build(cwd, override) + "\n\n" + PROMPT
    modes.install(api)
    # approval after modes, which keeps the mode it reads; guard last, so a doomed edit is refused before anyone is asked
    approval.install(api)
    guard.install(api)
    todo.install(api)
    context.install(api)
    slash.install(api)

    # textual only merges CSS and BINDINGS from widget classes, so they live here and not on the mixins
    class ClaudeCodeChat(ShortcutsMixin, ModesMixin, CompactMixin, LookMixin, api.Chat):
        Input = completion.make(api.Chat.Input, api.commands)
        Picker = picker.make(api.Chat.Picker)
        CSS = api.Chat.CSS + f"""
        .user {{ color: $text-muted; background: $boost; text-style: none; padding: 0 1; }}
        .bash {{ color: #fd5db1; }}
        .answer {{ height: auto; }}
        .dot {{ width: 2; color: $text; }}
        .answer > Markdown {{ width: 1fr; margin: 0; padding: 0; }}
        .thought {{ color: $text-muted; text-style: italic; }}
        .interrupted {{ color: $error; }}
        Picker {{ height: auto; }}
        #spinner {{ height: 1; padding: 0 1; color: {ACCENT}; }}
        #todos, #menu {{ height: auto; padding: 0 2; }}
        PromptInput, PromptInput:focus {{ border: round gray; }}
        """
        BINDINGS = [Binding("shift+tab", "cycle_mode", "cycle mode", priority=True),
                    Binding("ctrl+o", "transcript", "transcript mode")]

    ClaudeCodeChat.api = api
    api.Chat = ClaudeCodeChat
