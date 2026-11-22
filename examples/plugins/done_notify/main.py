"""UI: ring the bell and show a toast when an answer finishes."""


def setup(api) -> None:
    """Swap in a Chat subclass that notifies when it stops being busy."""

    class NotifyChat(api.Chat):
        async def set_busy(self, busy: bool) -> None:
            was_busy = self.busy
            await super().set_busy(busy)
            if was_busy and not busy:
                self.bell()
                self.notify("answer finished")

    api.Chat = NotifyChat
