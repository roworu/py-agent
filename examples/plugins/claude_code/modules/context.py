"""Context: auto-compact near the limit, the "context left" number and /context."""

import json

# for models without contextWindow in settings.json
DEFAULT_CONTEXT = 128_000
# share of the context window that triggers /compact
AUTO_COMPACT = 0.8


def limit(agent) -> int:
    return agent.model.context_window or DEFAULT_CONTEXT


def used(agent) -> int:
    """Tokens of the last request plus its answer, 0 before the first."""
    usage = agent.usage or {}
    return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


def install(api) -> None:
    """Add /context."""
    def report(agent, arg: str) -> tuple[str, None]:
        total = limit(agent)
        # ~4 chars per token; scaled to the server's real count once there is one
        parts = {"System prompt": len(agent.messages[0]["content"]) // 4,
                 "Tools": len(json.dumps(api.tools.SPECS)) // 4,
                 "Messages": len(json.dumps(agent.messages[1:])) // 4}
        scale = used(agent) / sum(parts.values()) if used(agent) else 1
        parts = {name: int(n * scale) for name, n in parts.items()}
        glyphs = {"System prompt": "▓", "Tools": "▒", "Messages": "█"}
        cells = "".join(glyphs[name] * round(n / total * 100) for name, n in parts.items())
        buffer = round((1 - AUTO_COMPACT) * 100)
        cells = (cells + "·" * 100)[:100 - buffer] + "░" * buffer
        rows = [" ".join(cells[i:i + 10]) for i in range(0, 100, 10)]
        sum_used = sum(parts.values())
        legend = [f"{agent.model.name} · {sum_used / 1000:.1f}k/{total / 1000:.0f}k tokens ({sum_used / total:.0%})", ""]
        legend += [f"{glyphs[name]} {name}: {n / 1000:.1f}k ({n / total:.1%})" for name, n in parts.items()]
        legend += [f"· Free: {max(0, total * AUTO_COMPACT - sum_used) / 1000:.1f}k", f"░ Auto-compact buffer: {buffer}%"]
        return "\n".join(f"{row}   {legend[i] if i < len(legend) else ''}".rstrip() for i, row in enumerate(rows)), None

    api.add_command("/context", report, "show how full the context window is")


class CompactMixin:
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.auto_compacting = False

    def context_left(self) -> float:
        """Share of the context left until auto-compact."""
        return max(0.0, 1 - used(self.agent) / (limit(self.agent) * AUTO_COMPACT))

    async def set_busy(self, busy: bool) -> None:
        await super().set_busy(busy)
        if busy:
            return
        # only once in a row, so a failed compact doesn't loop
        if not self.auto_compacting and self.context_left() == 0:
            self.auto_compacting = True
            await self.send("/compact")
        else:
            self.auto_compacting = False
