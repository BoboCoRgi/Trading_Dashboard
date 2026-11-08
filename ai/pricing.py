from decimal import Decimal

from core.money import D

# Approximate public list prices in USD per 1,000,000 tokens.
# Used only to estimate observability cost. Not an invoice.
_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "claude-sonnet-4-5": (D("3"), D("15")),
    "gpt-4.1": (D("2"), D("8")),
    "gpt-4.1-mini": (D("0.40"), D("1.60")),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> tuple[Decimal, str]:
    if model.startswith("gemini"):
        return D(0), "Gemini list price is not in this table; recorded as 0"
    input_price, output_price = _PER_MILLION.get(model, _PER_MILLION["claude-sonnet-4-5"])
    note = model if model in _PER_MILLION else f"unknown model {model}; used claude-sonnet-4-5 rates"
    cost = (D(input_tokens) * input_price + D(output_tokens) * output_price) / D(1_000_000)
    return cost, f"estimate using {note} list rates"
