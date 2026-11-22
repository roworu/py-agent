"""UI: gruvbox theme and a highlighted status line."""


def setup(api) -> None:
    """Swap in a Chat subclass with its own theme and css."""

    class ThemedChat(api.Chat):
        CSS = api.Chat.CSS + """
        .user { color: $warning; }
        #status { background: $panel; color: $text; }
        """

        # textual runs on_mount of every class in the mro, so no super() call here
        def on_mount(self) -> None:
            self.theme = "gruvbox"

    api.Chat = ThemedChat
