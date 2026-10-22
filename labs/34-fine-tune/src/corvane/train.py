"""Train the LoRA adapter on a Kaggle GPU, then bring it back to evaluate on this laptop.

Training needs a GPU for a few minutes; scoring and serving do not. So the recipe and the reviewed
labels go up as a versioned, private Kaggle dataset, a kernel trains with Hugging Face PEFT, and the
adapter comes back to run in MLX. Nothing trains on the laptop.
"""

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from corvane.adapter import peft_to_mlx
from corvane.config import SMALL_MODEL, SMALL_MODEL_REVISION
from corvane.labels import SPLIT_DIR

ADAPTER_DIR = Path("out/adapter")
KAGGLE_DIR = Path("out/kaggle")
TRAIN_REPORT = Path("out/train.json")
TRAIN_SCRIPT = Path(__file__).parents[2] / "kaggle" / "train.sh"


@dataclass
class Recipe:
    """Every knob that changes the result, in one place, so a run can be repeated.

    LoRA rank 8 on the attention projections of every layer. One pass over the training split, capped
    by `max_steps`, keeps a run to a few GPU minutes. Batch 2 gives twice the updates of batch 4 in the
    same pass: at batch 4 the model still missed merchants it had seen only a few times. GPU arithmetic is
    not bit-for-bit repeatable, so the rate is set for a fresh run, not the best one: at 1e-3 a second run
    of this recipe fell to 88.7% on validation and its cascade was held; at 5e-4 four runs landed between
    89.6% and 93.0%. Answers are a
    few tokens, so 96 tokens fits every example, and short sequences are what make a step fast.
    """
    epochs: int = 1
    max_steps: int = 450
    batch_size: int = 2
    learning_rate: float = 5e-4
    rank: int = 8
    alpha: int = 32
    targets: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj")
    max_seq_length: int = 96
    seed: int = 34


def base_model_path() -> str:
    """Download the pinned commit of the base model once, into the Hugging Face cache."""
    from huggingface_hub import snapshot_download

    return snapshot_download(SMALL_MODEL, revision=SMALL_MODEL_REVISION)


def dataset_version(folder: Path = SPLIT_DIR) -> str:
    """A hash of exactly what the model trains on. The adapter card names it, so a result can be traced."""
    digest = hashlib.sha256()
    for name in ("train.jsonl", "valid.jsonl"):
        digest.update((folder / name).read_bytes())
    return digest.hexdigest()[:12]


def stage_dataset(recipe: Recipe, folder: Path = SPLIT_DIR, staged: Path = KAGGLE_DIR / "dataset") -> Path:
    """The files the kernel reads: the two splits and the recipe. The held-out test never goes up."""
    staged.mkdir(parents=True, exist_ok=True)
    for name in ("train.jsonl", "valid.jsonl"):
        (staged / name).write_bytes((folder / name).read_bytes())
    (staged / "recipe.json").write_text(json.dumps(run_settings(recipe, folder), indent=1))
    return staged


def run_settings(recipe: Recipe, folder: Path = SPLIT_DIR) -> dict:
    """The recipe, the pinned base model and the data's version: everything that decides the adapter."""
    return asdict(recipe) | {"targets": list(recipe.targets), "base_model": SMALL_MODEL,
                             "revision": SMALL_MODEL_REVISION, "dataset_version": dataset_version(folder)}


def already_trained(recipe: Recipe, folder: Path = SPLIT_DIR) -> dict | None:
    """The last run's report, when it was trained from these exact settings and its adapter is still here.

    Same data and same recipe give an adapter within a point or so of this one (GPU arithmetic is not
    bit-for-bit repeatable), so paying for the GPU again buys nothing. A new review decision changes
    the dataset version, and then it trains.
    """
    if not (ADAPTER_DIR / "adapters.safetensors").exists() or not TRAIN_REPORT.exists():
        return None
    report = json.loads(TRAIN_REPORT.read_text())
    return report if report.get("recipe") == run_settings(recipe, folder) else None


def train_report(run_dir: Path, kernel: str) -> dict:
    """What the kernel measured, plus where it ran, in the shape the console and the proof read."""
    metrics = json.loads((run_dir / "metrics.json").read_text())
    return metrics | {"kernel": kernel, "adapter": str(ADAPTER_DIR)}


def train(recipe: Recipe, echo=print, force: bool = False) -> dict:
    """Stage the data, run `kaggle/train.sh`, convert the adapter for MLX and write the report."""
    previous = None if force else already_trained(recipe)
    if previous:
        echo(f"Dataset {previous['dataset_version']} with this recipe is already trained: kernel {previous['kernel']} "
             f"on {previous['gpu']}, {previous['gpu_minutes']} GPU minutes. Reusing it; --force trains again.")
        return previous
    staged = stage_dataset(recipe)
    run_dir = KAGGLE_DIR / "run"
    process = subprocess.run(["bash", str(TRAIN_SCRIPT), str(staged), str(run_dir)])
    if process.returncode != 0:
        raise SystemExit("The Kaggle run did not complete. Its log is above; nothing was converted.")
    kernel = (run_dir / "kernel.txt").read_text().strip()
    peft_to_mlx(run_dir / "adapter", ADAPTER_DIR)
    report = train_report(run_dir, kernel)
    TRAIN_REPORT.write_text(json.dumps(report, indent=1))
    echo(f"Kernel {kernel} on {report['gpu']}: {report['steps']} steps in {report['train_seconds']:.0f} s, "
         f"{report['gpu_minutes']} GPU minutes, valid accuracy {report['valid_accuracy']:.1%}.")
    return report
