"""Input completion: a menu of slash commands when typing /, on top of py-agent's @ file menu, drawn like claude code."""

from . import render


def make(base, commands):
    """Subclass the app's input class with commands in the menu."""

    class ClaudeInput(base):
        def menu_items(self) -> list[tuple[str, str]]:
            text = self.text
            # "command" or "file", for accept
            self.kind = "command" if text.startswith("/") and not set(" \n") & set(text) else "file"
            if self.kind == "command":
                return [(n, commands.DESCRIPTIONS.get(n, ("", ""))[1]) for n in commands.NAMES if n.startswith(text)]
            return super().menu_items()

        def draw_menu(self) -> None:
            menu = self.app.query_one("#menu")
            menu.display = bool(self.items)
            menu.update(render.menu(self.items, self.selected) if self.items else "")

        def accept(self, submit: bool) -> None:
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
