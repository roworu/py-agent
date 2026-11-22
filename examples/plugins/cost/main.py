"""Behavior: /cost command adding up tokens and price of every request.

Prices in usd per million input and output tokens go in settings.json, by provider/id; unknown models count tokens only:
"plugins": {"cost": {"prices": {"openrouter/deepseek/deepseek-chat": [0.27, 1.10]}}}
"""


def setup(api) -> None:
    """Register /cost, priced from agent.total."""
    prices = api.settings("cost").get("prices", {})

    def cost(agent, arg: str) -> tuple[str, None]:
        tokens_in = tokens_out = usd = 0
        for name, total in agent.total.items():
            price_in, price_out = prices.get(name, (0, 0))
            tokens_in += total.get("prompt_tokens", 0)
            tokens_out += total.get("completion_tokens", 0)
            usd += (total.get("prompt_tokens", 0) * price_in + total.get("completion_tokens", 0) * price_out) / 1e6
        return f"{tokens_in:,} input · {tokens_out:,} output tokens · ${usd:.4f}", None

    api.add_command("/cost", cost, "show tokens and price spent since start")
