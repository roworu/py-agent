"""Picker: the base question widget drawn like claude code's; used for approvals, plans and rewind."""

from . import render


def make(base):
    """Subclass the app's picker class with claude code's look."""

    class ClaudePicker(base):
        def draw(self) -> None:
            self.update(render.picker(self.question, self.options, self.selected, self.body))

    return ClaudePicker
