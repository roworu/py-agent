"""A centered dialog with a search box over a list, like opencode's model, session, theme and command dialogs.

Rows are (group, value, label, hint); dismissed with the picked value, or None on esc."""

from collections.abc import Callable

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class SelectDialog(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "close"), Binding("up", "move(-1)", priority=True),
                Binding("down", "move(1)", priority=True)]
    DEFAULT_CSS = """
    SelectDialog { align: center middle; background: $background 60%; }
    SelectDialog > Vertical { width: 70; max-width: 90%; height: auto; max-height: 80%; background: $surface; padding: 1 2; }
    SelectDialog #title { height: 1; margin-bottom: 1; }
    SelectDialog Input, SelectDialog Input:focus { border: none; background: $surface; padding: 0; height: 1; margin-bottom: 1; }
    SelectDialog OptionList { border: none; background: $surface; height: auto; max-height: 20; padding: 0; }
    SelectDialog OptionList > .option-list--option-highlighted { background: $primary; color: $background; text-style: none; }
    """

    def __init__(self, title: str, rows: list[tuple[str, str, str, str]], current: str | None = None,
                 on_highlight: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.title_text, self.rows, self.current, self.on_highlight = title, rows, current, on_highlight

    def compose(self):
        with Vertical():
            title = Text(self.title_text, "bold")
            title.append(" " * max(1, 62 - len(self.title_text)) + "esc", "dim")
            yield Static(title, id="title")
            yield Input(placeholder="Search")
            yield OptionList()

    def on_mount(self) -> None:
        self.fill("")
        self.query_one(Input).focus()

    def fill(self, query: str) -> None:
        """List the rows matching query under their group headers; the current value is marked and highlighted."""
        options, group, first = [], None, None
        for row_group, value, label, hint in self.rows:
            if query.lower() not in f"{label} {hint} {row_group} {value}".lower():
                continue
            if row_group and row_group != group:
                group = row_group
                options.append(Option(Text(group, f"bold {self.app.c['accent']}"), disabled=True))
            mark = "● " if value == self.current else "  "
            line = Text(mark + label)
            if hint:
                line.append("  " + hint, "dim")
            options.append(Option(line, id=value))
            if first is None or value == self.current:
                first = len(options) - 1
        menu = self.query_one(OptionList)
        menu.clear_options()
        menu.add_options(options)
        menu.highlighted = first

    def on_input_changed(self, event: Input.Changed) -> None:
        self.fill(event.value)

    def on_input_submitted(self) -> None:
        menu = self.query_one(OptionList)
        if menu.highlighted is not None:
            self.dismiss(menu.get_option_at_index(menu.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self.on_highlight and event.option.id:
            self.on_highlight(event.option.id)

    def action_move(self, step: int) -> None:
        menu = self.query_one(OptionList)
        menu.action_cursor_down() if step > 0 else menu.action_cursor_up()
