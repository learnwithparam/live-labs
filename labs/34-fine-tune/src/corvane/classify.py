"""Three classifiers behind one shape: the large model over an API, and the small model on this laptop,
with or without the trained adapter.

Each returns one prediction per item: the parsed category (None when the answer was not exactly one
category), the raw answer, a confidence when the model exposes one, and what the item cost.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from corvane.config import PRICES, SMALL_MODEL_HOURLY
from corvane.labels import CATEGORIES, POLICY, SHORT_INSTRUCTIONS, parse_label, user_message

EXAMPLES_PER_CATEGORY = 2
MAX_ANSWER_TOKENS = 8
TOKENS_PER_BATCH = 3000


@dataclass
class Prediction:
    id: str
    label: str | None
    raw: str
    confidence: float | None
    cost: float

    def as_dict(self) -> dict:
        return asdict(self)


def few_shot_messages(item: dict, examples: list[dict]) -> list[dict]:
    """The prompt the large model and the untuned small model both get: the policy and worked examples.

    Examples go in as earlier chat turns, the format a chat model was trained to continue.
    """
    messages = [{"role": "system", "content": SHORT_INSTRUCTIONS + "\n\n" + POLICY}]
    for example in examples:
        messages += [{"role": "user", "content": user_message(example)},
                     {"role": "assistant", "content": example["label"]}]
    return messages + [{"role": "user", "content": user_message(item)}]


def short_messages(item: dict) -> list[dict]:
    """The tuned model's prompt: the same instructions it was trained with, and no examples to pay for."""
    return [{"role": "system", "content": SHORT_INSTRUCTIONS}, {"role": "user", "content": user_message(item)}]


def pick_examples(train: list[dict]) -> list[dict]:
    """The first reviewed items of each category in the training split, so the prompt never sees a test item."""
    picked = []
    for category in CATEGORIES:
        picked += sorted((item for item in train if item["label"] == category), key=lambda item: item["id"])[
            :EXAMPLES_PER_CATEGORY]
    return picked


def usage_cost(response, model: str) -> float:
    """What the provider billed for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    if billed is not None:
        return float(billed)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    return ((usage.prompt_tokens - cached) * price_in + cached * price_cached
            + usage.completion_tokens * price_out) / 1e6


class LargeModel:
    """The model Corvane pays for per token today, called with the few-shot prompt."""

    def __init__(self, client, model: str, examples: list[dict], workers: int = 8):
        self.client, self.model, self.examples, self.workers = client, model, examples, workers
        self.name = model

    def one(self, item: dict) -> Prediction:
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=few_shot_messages(item, self.examples),
                extra_body={"usage": {"include": True}})
        except Exception as error:
            # A failed call is not a label. The item goes to a reviewer like any other unclear answer.
            return Prediction(item["id"], None, f"error: {type(error).__name__}", None, 0.0)
        raw = response.choices[0].message.content or ""
        return Prediction(item["id"], parse_label(raw), raw.strip(), None, usage_cost(response, self.model))

    def classify(self, items: list[dict]) -> list[Prediction]:
        # API calls wait on the network, so a few run at once. Order is kept by `map`.
        with ThreadPoolExecutor(self.workers) as pool:
            predictions = list(pool.map(self.one, items))
        # One failed call is one item for a reviewer. Every call failing is a wrong key or model name, and
        # scoring it would print 0% as if the model were that bad, so the run stops with the error instead.
        if predictions and all(prediction.raw.startswith("error: ") for prediction in predictions):
            raise SystemExit(f"The large model answered none of the {len(items)} items ({predictions[0].raw}). "
                             "Check LLM_API_KEY and LLM_MODEL in .env.")
        return predictions


class SmallModel:
    """Qwen3 0.6B in MLX on this laptop. With an adapter it is the tuned model; without, the untuned one.

    Confidence is the probability the model gave its own answer, token by token. A tuned model that is
    unsure spreads that probability across categories, which is the signal to ask a reviewer.
    """

    def __init__(self, model_path: str, adapter_path: str | None = None, examples: list[dict] | None = None,
                 hourly: float = SMALL_MODEL_HOURLY):
        from mlx_lm import load

        self.model, self.tokenizer = load(model_path, adapter_path=adapter_path)
        self.examples, self.hourly = examples, hourly
        self.name = "Qwen3 0.6B tuned" if adapter_path else "Qwen3 0.6B untuned"
        self.seconds = 0.0

    def prompt(self, item: dict) -> str:
        messages = few_shot_messages(item, self.examples) if self.examples else short_messages(item)
        # Qwen3 thinks out loud by default. A category needs no thinking, and thinking tokens cost time.
        return self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                                  enable_thinking=False)

    def classify(self, items: list[dict]) -> list[Prediction]:
        """Greedy answers for every item, decoded in batches, with each answer's probability.

        Batching is what makes a small model cheap: the GPU answers dozens of short prompts in about
        the time one takes. The prompt token budget per batch keeps the KV cache near 300 MB.
        """
        import math

        from mlx_lm.generate import BatchGenerator

        prompts = [self.tokenizer.encode(self.prompt(item), add_special_tokens=False) for item in items]
        per_batch = max(1, min(64, TOKENS_PER_BATCH // max(len(prompt) for prompt in prompts)))
        started = time.perf_counter()
        generator = BatchGenerator(self.model, stop_tokens=[[token] for token in self.tokenizer.eos_token_ids],
                                   prefill_batch_size=per_batch, completion_batch_size=per_batch)
        uids = generator.insert(prompts, [MAX_ANSWER_TOKENS] * len(prompts))
        answers = {uid: [] for uid in uids}
        log_probability = dict.fromkeys(uids, 0.0)
        while responses := generator.next_generated():
            for response in responses:
                if response.finish_reason != "stop":
                    answers[response.uid].append(response.token)
                    log_probability[response.uid] += response.logprobs[response.token].item()
        generator.close()
        self.seconds = time.perf_counter() - started
        # Measured throughput on this laptop, priced at what renting the same machine costs per second.
        per_item = self.seconds / len(items) * self.hourly / 3600
        predictions = []
        for item, uid in zip(items, uids):
            raw = self.tokenizer.decode(answers[uid]).strip()
            predictions.append(Prediction(item["id"], parse_label(raw), raw,
                                          round(math.exp(log_probability[uid]), 4), per_item))
        return predictions
