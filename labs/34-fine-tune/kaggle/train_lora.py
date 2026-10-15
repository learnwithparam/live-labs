"""LoRA on Qwen3 0.6B with Hugging Face PEFT, run as a Kaggle GPU kernel by `kaggle/train.sh`.

GPU hours are scarce, so this script fails fast: it checks the GPU, then runs a five-step smoke train
of the exact same code, and only then the real run. A wall-clock guard saves the adapter and stops
before the kernel's own limit, so a slow GPU still returns something to evaluate.

`CORVANE_LOCAL_CHECK=1` runs the same code on the CPU against ten examples, to catch a bug before it
costs any GPU time. It proves the code runs, not that the recipe is good.
"""

import glob
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

STARTED = time.time()
LOCAL_CHECK = os.environ.get("CORVANE_LOCAL_CHECK") == "1"
OUT = Path(os.environ.get("CORVANE_OUT", "/kaggle/working"))
# Kaggle stops a kernel at its own limit and keeps nothing. Stopping earlier keeps the adapter.
WALL_LIMIT_SECONDS = 18 * 60
PINNED = ["transformers==4.56.2", "peft==0.17.1"]


def check_gpu() -> str:
    """Refuse to train on a CPU by accident: it would burn the session and return nothing useful."""
    if LOCAL_CHECK:
        return "cpu (local check)"
    listing = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=True).stdout.strip()
    print(listing, flush=True)
    import torch

    assert torch.cuda.is_available(), "no GPU: refusing to train on CPU"
    return torch.cuda.get_device_name(0)


def find_data() -> Path:
    """Kaggle mounts the dataset under /kaggle/input, at a path that has changed before. Search for it."""
    if os.environ.get("CORVANE_DATA"):
        return Path(os.environ["CORVANE_DATA"])
    return Path(glob.glob("/kaggle/input/**/recipe.json", recursive=True)[0]).parent


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def encode(tokenizer, example: dict, max_length: int) -> tuple[list[int], list[int]]:
    """Prompt tokens, then the answer. Only the answer is scored, so the model learns to pick a category.

    The prompt is built exactly as `SmallModel.prompt` builds it on the laptop, thinking switched off.
    Train and serve on different templates and the adapter learns a format it will never see.
    """
    prompt = tokenizer.apply_chat_template(example["messages"][:-1], add_generation_prompt=True,
                                           tokenize=False, enable_thinking=False)
    prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
    answer_ids = tokenizer.encode(example["messages"][-1]["content"] + "<|im_end|>", add_special_tokens=False)
    token_ids = (prompt_ids + answer_ids)[:max_length]
    targets = ([-100] * len(prompt_ids) + answer_ids)[:max_length]
    return token_ids, targets


def batches(encoded: list, batch_size: int, pad_id: int, seed: int):
    """Shuffled batches, padded on the right; padding is never a target."""
    import torch

    order = list(range(len(encoded)))
    random.Random(seed).shuffle(order)
    for start in range(0, len(order), batch_size):
        rows = [encoded[index] for index in order[start:start + batch_size]]
        width = max(len(token_ids) for token_ids, _ in rows)
        token_ids = torch.tensor([ids + [pad_id] * (width - len(ids)) for ids, _ in rows])
        targets = torch.tensor([target + [-100] * (width - len(target)) for _, target in rows])
        mask = torch.tensor([[1] * len(ids) + [0] * (width - len(ids)) for ids, _ in rows])
        yield token_ids, targets, mask


def load_lora_model(recipe: dict, model_path: str, device: str):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM

    # Base weights in fp32 with fp16 autocast: fast on a T4 and stable, with no quantisation tricks.
    base = AutoModelForCausalLM.from_pretrained(model_path, dtype=torch.float32).to(device)
    config = LoraConfig(r=recipe["rank"], lora_alpha=recipe["alpha"], lora_dropout=0.0,
                        target_modules=recipe["targets"], task_type="CAUSAL_LM")
    return get_peft_model(base, config)


def train(recipe: dict, model_path: str, encoded: list, pad_id: int, device: str, steps: int,
          checkpoint_dir: Path | None = None) -> tuple:
    """One pass over the training split, capped at `steps`, with a warmup and a cosine decay."""
    import torch

    model = load_lora_model(recipe, model_path, device)
    model.train()
    optimizer = torch.optim.AdamW([weight for weight in model.parameters() if weight.requires_grad],
                                  lr=recipe["learning_rate"])
    warmup = max(1, steps // 10)
    schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: min(1.0, (step + 1) / warmup)
                                                 * 0.5 * (1 + math.cos(math.pi * min(step, steps) / steps)))
    on_gpu = device == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=on_gpu)
    losses, step, stopped_early = [], 0, False
    for token_ids, targets, mask in batches(encoded, recipe["batch_size"], pad_id, recipe["seed"]):
        with torch.autocast(device_type=device, dtype=torch.float16, enabled=on_gpu):
            loss = model(input_ids=token_ids.to(device), attention_mask=mask.to(device), labels=targets.to(device)).loss
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        optimizer.zero_grad(set_to_none=True)
        schedule.step()
        step += 1
        if step % 10 == 0 or step == steps:
            losses.append([step, round(loss.item(), 4)])
            print(f"step {step}/{steps} loss {loss.item():.4f}", flush=True)
        if checkpoint_dir and step == steps // 2:
            model.save_pretrained(checkpoint_dir / f"checkpoint-{step}")
        if step >= steps:
            break
        if time.time() - STARTED > WALL_LIMIT_SECONDS:
            print(f"wall-clock guard: stopping at step {step} to keep the adapter", flush=True)
            stopped_early = True
            break
    return model, losses, step, stopped_early


def valid_accuracy(model, tokenizer, valid: list, device: str) -> float:
    """Greedy answers on the valid split, a quick read before the laptop scores the held-out test."""
    import torch

    model.eval()
    tokenizer.padding_side = "left"
    correct = 0
    for start in range(0, len(valid), 32):
        chunk = valid[start:start + 32]
        prompts = [tokenizer.apply_chat_template(example["messages"][:-1], add_generation_prompt=True,
                                                 tokenize=False, enable_thinking=False) for example in chunk]
        inputs = tokenizer(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to(device)
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=8, do_sample=False)
        answers = tokenizer.batch_decode(generated[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        correct += sum(answer.strip() == example["messages"][-1]["content"] for answer, example in zip(answers, chunk))
    return round(correct / len(valid), 4)


def main() -> None:
    gpu = check_gpu()
    if not LOCAL_CHECK:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PINNED], check=True)
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    data = find_data()
    recipe = json.loads((data / "recipe.json").read_text())
    train_rows, valid_rows = read_jsonl(data / "train.jsonl"), read_jsonl(data / "valid.jsonl")
    if LOCAL_CHECK:
        train_rows, valid_rows = train_rows[:10], valid_rows[:10]
    device = "cpu" if LOCAL_CHECK else "cuda"
    torch.manual_seed(recipe["seed"])
    model_path = snapshot_download(recipe["base_model"], revision=recipe["revision"])
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    encoded = [encode(tokenizer, example, recipe["max_seq_length"]) for example in train_rows]
    one_epoch = math.ceil(len(encoded) / recipe["batch_size"])
    steps = 2 if LOCAL_CHECK else min(recipe["max_steps"], one_epoch)

    # The smoke run: the same code for five steps. A bug here costs a minute, not the whole run.
    train(recipe, model_path, encoded, tokenizer.pad_token_id, device, 1 if LOCAL_CHECK else 5)
    print("smoke train passed", flush=True)
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()

    train_started = time.time()
    model, losses, done, stopped_early = train(recipe, model_path, encoded, tokenizer.pad_token_id, device,
                                               steps, checkpoint_dir=OUT)
    train_seconds = time.time() - train_started
    model.save_pretrained(OUT / "adapter")
    metrics = {
        "gpu": gpu, "base_model": recipe["base_model"], "revision": recipe["revision"], "recipe": recipe,
        "dataset_version": recipe["dataset_version"], "train_examples": len(encoded), "steps": done,
        "stopped_early": stopped_early, "train_seconds": round(train_seconds, 1), "train_loss": losses,
        "peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if device == "cuda" else None,
        "trainable": sum(weight.numel() for weight in model.parameters() if weight.requires_grad),
        "valid_accuracy": valid_accuracy(model, tokenizer, valid_rows, device),
        "versions": {"torch": torch.__version__, "pinned": PINNED},
    }
    # GPU minutes are the session's whole wall time, installs and smoke included: that is what the quota counts.
    metrics["gpu_minutes"] = round((time.time() - STARTED) / 60, 1)
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1))
    print(json.dumps({key: metrics[key] for key in ("gpu", "steps", "train_seconds", "peak_memory_gb",
                                                    "valid_accuracy", "gpu_minutes")}), flush=True)


if __name__ == "__main__":
    main()
