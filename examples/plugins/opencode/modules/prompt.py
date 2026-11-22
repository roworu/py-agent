"""The prompt box and the permission picker.

The input adds slash commands and subagents to py-agent's @ file menu, cycles agents with tab and takes the key
after the ctrl+x leader."""

from . import render


def make_input(base, commands, subagents: list[str]):
    """Subclass the app's input class."""

    class OpenCodeInput(base):
        kind = "file"

        def menu_items(self) -> list[tuple[str, str]]:
            text = self.text
            self.kind = "command" if text.startswith("/") and not set(" \n") & set(text) else "file"
            if self.kind == "command":
                return [(n, commands.DESCRIPTIONS.get(n, ("", ""))[1]) for n in commands.NAMES if n.startswith(text)]
            if (query := self.mention()) is None:
                return []
            # @general hands the message to that subagent, like opencode
            return [(n, "agent") for n in subagents if n.startswith(query)] + super().menu_items()

        def draw_menu(self) -> None:
            menu = self.app.query_one("#menu")
            menu.display = bool(self.items)
            menu.update(render.menu(self.items, self.selected, self.app.c) if self.items else "")

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

        async def _on_key(self, event) -> None:
            app = self.app
            if app.leader_pending():
                event.stop()
                event.prevent_default()
                await app.run_leader(event.key)
            elif event.key in ("tab", "shift+tab") and not self.items:
                event.stop()
                event.prevent_default()
                app.cycle_agent(-1 if event.key == "shift+tab" else 1)
            elif event.key == "ctrl+d" and not self.text:
                event.stop()
                app.exit()
            else:
                await super()._on_key(event)

    return OpenCodeInput


def make_picker(base):
    """Subclass the app's picker class: options side by side, a for "allow always"."""

    class OpenCodePicker(base):
        BINDINGS = [("left", "move(-1)"), ("right", "move(1)"), ("a", "pick_always")]

        def draw(self) -> None:
            self.update(render.picker(self.question, self.options, self.selected, self.body, self.app.c))

        def action_pick_always(self) -> None:
            if len(self.options) == 3:
                self.action_pick(1)

    return OpenCodePicker
