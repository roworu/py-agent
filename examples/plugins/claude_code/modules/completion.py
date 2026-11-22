"""Input completion: a menu of slash commands when typing /, on top of py-agent's @ file menu, drawn like claude code.

Also the "> " prompt in front of the input, and "?" on an empty input listing the shortcuts."""

from rich.segment import Segment
from textual.strip import Strip

from . import render

SHORTCUTS = [("! for bash mode", "double tap esc to clear input"), ("/ for commands", "shift+tab to cycle modes"),
             ("@ for file paths", "ctrl+o for transcript"), ("# to memorize", "shift+enter for newline")]


def make(base, commands):
    """Subclass the app's input class with commands in the menu."""

    class ClaudeInput(base):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # the line number gutter makes room for the "> " prompt, clicks and wrapping account for it
            self.show_line_numbers = True

        @property
        def gutter_width(self) -> int:
            return 2

        def render_line(self, y: int) -> Strip:
            strip = super().render_line(y)
            prompt = Strip([Segment("> " if y + self.scroll_offset.y == 0 else "  ", self.rich_style)], 2)
            # the placeholder is drawn without the gutter, so it's moved over instead
            if not self.text:
                return Strip.join([prompt, strip]).crop(0, self.content_size.width)
            # the first segment is the line number, which can be wider than the gutter it claims
            return Strip.join([prompt, Strip(list(strip)[1:])])

        def menu_items(self) -> list[tuple[str, str]]:
            text = self.text
            # "command", "help" or "file", for accept
            self.kind = "help" if text == "?" else "command" if text.startswith("/") and not set(" \n") & set(text) else "file"
            if self.kind == "help":
                return SHORTCUTS
            if self.kind == "command":
                return [(n, commands.DESCRIPTIONS.get(n, ("", ""))[1]) for n in commands.NAMES if n.startswith(text)]
            return super().menu_items()

        def draw_menu(self) -> None:
            menu = self.app.query_one("#menu")
            menu.display = bool(self.items)
            if self.kind == "help":
                menu.update(render.shortcuts(self.items))
            else:
                menu.update(render.menu(self.items, self.selected) if self.items else "")

        def accept(self, submit: bool) -> None:
            if self.kind == "help":
                # the list is only to read, enter or tab closes it with the "?"
                self.clear()
                return
            if self.kind != "command":
                super().accept(submit)
                return
            name = self.items[self.selected][0]
            self.items = []
            if submit:
                self.post_message(self.Submitted(name))
            else:
                self.text = name + " "
                self.move_cursor(self.document.end)
            self.draw_menu()

    return ClaudeInput
