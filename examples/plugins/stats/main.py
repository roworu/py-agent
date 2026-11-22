"""Behavior: /stats command showing conversation size."""

import json
from collections import Counter


def stats(agent, arg: str) -> tuple[str, None]:
    """Count messages by role and estimate tokens."""
    roles = Counter(m["role"] for m in agent.messages)
    # ~4 chars per token is a rough average for english and code
    tokens = len(json.dumps(agent.messages)) // 4
    return f"{', '.join(f'{n} {r}' for r, n in roles.items())} · ~{tokens} tokens", None


def setup(api) -> None:
    """Register /stats."""
    api.add_command("/stats", stats)
