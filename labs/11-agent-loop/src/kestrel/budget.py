"""The agent's budget: how many model calls it may make and how many dollars it may spend on one chat.

A loop with no budget is a loop that can spin all night on a confused customer. Each engine charges
every model call here, and checks before the next one. Over budget, the chat goes to a person.
"""

from dataclasses import dataclass, field

from kestrel.config import PRICES

MAX_STEPS = 8  # model calls per chat; our hardest chat needs about 5
MAX_COST = 0.01  # dollars per chat, at list price


@dataclass
class Budget:
    model: str
    max_steps: int = MAX_STEPS
    max_cost: float = MAX_COST
    steps: int = 0
    cost: float = 0.0
    tokens: list[int] = field(default_factory=lambda: [0, 0])

    def charge(self, prompt_tokens: int, completion_tokens: int) -> None:
        # One price table for every engine, so the loop and ADK are costed the same way.
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        self.steps += 1
        self.tokens[0] += prompt_tokens
        self.tokens[1] += completion_tokens
        self.cost += (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000

    def over(self, steps: bool = True) -> str | None:
        """Why another model call is not allowed, or None if it is. ADK counts steps itself, so it asks about cost only."""
        if steps and self.steps >= self.max_steps:
            return f"step budget: {self.steps} model calls"
        if self.cost >= self.max_cost:
            return f"cost budget: ${self.cost:.4f}"
        return None

    def state(self) -> dict:
        return {"steps": self.steps, "max_steps": self.max_steps, "cost": round(self.cost, 6), "max_cost": self.max_cost,
                "tokens_in": self.tokens[0], "tokens_out": self.tokens[1]}
