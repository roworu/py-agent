"""UI: context size from the server's token usage in the status line; models with contextWindow set also show how full it is."""


def setup(api) -> None:
    """Swap in a Chat subclass that adds token usage to the status line."""

    class ContextChat(api.Chat):
        def update_status(self) -> None:
            super().update_status()
            if not (usage := self.agent.usage):
                return
            # the last prompt plus its answer is what the next request starts from
            tokens = usage["prompt_tokens"] + usage["completion_tokens"]
            limit = self.agent.model.context_window
            shown = f"{tokens:,} tokens" + (f" ({tokens / limit:.0%})" if limit else "")
            status = self.query_one("#status")
            status.update(f"{status.content} · {shown}")

    api.Chat = ContextChat
