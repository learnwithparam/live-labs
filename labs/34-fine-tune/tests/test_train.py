"""What goes up to Kaggle, what comes back, and the adapter conversion that lets MLX load it."""

import json

import numpy

from corvane.adapter import mlx_config, mlx_weights
from corvane.labels import HELDOUT
from corvane.train import Recipe, dataset_version, stage_dataset, train_report


def write_splits(folder, train_text="train\n"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "train.jsonl").write_text(train_text)
    (folder / "valid.jsonl").write_text("valid\n")


def test_the_staged_dataset_holds_the_splits_and_recipe_but_never_the_test(tmp_path):
    write_splits(tmp_path / "splits")
    (tmp_path / "splits" / HELDOUT.name).write_text("test\n")
    staged = stage_dataset(Recipe(), tmp_path / "splits", tmp_path / "staged")
    assert sorted(path.name for path in staged.iterdir()) == ["recipe.json", "train.jsonl", "valid.jsonl"]
    recipe = json.loads((staged / "recipe.json").read_text())
    assert recipe["revision"] and recipe["base_model"] == "Qwen/Qwen3-0.6B"
    assert recipe["dataset_version"] == dataset_version(tmp_path / "splits")


def test_the_dataset_version_changes_when_a_label_does(tmp_path):
    write_splits(tmp_path / "before")
    write_splits(tmp_path / "after", train_text="train with one review decision\n")
    assert dataset_version(tmp_path / "before") != dataset_version(tmp_path / "after")


def test_a_run_stays_small_enough_for_the_free_gpu_quota():
    recipe = Recipe()
    assert recipe.epochs == 1 and recipe.max_steps <= 450 and recipe.rank == 8
    assert set(recipe.targets) == {"q_proj", "k_proj", "v_proj", "o_proj"}


def test_peft_matrices_are_transposed_and_renamed_for_mlx():
    rank, inputs, outputs = 8, 16, 12
    peft = {"base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": numpy.ones((rank, inputs)),
            "base_model.model.model.layers.27.self_attn.q_proj.lora_B.weight": numpy.ones((outputs, rank))}
    weights, keys, layers = mlx_weights(peft)
    assert weights["model.layers.0.self_attn.q_proj.lora_a"].shape == (inputs, rank)
    assert weights["model.layers.27.self_attn.q_proj.lora_b"].shape == (rank, outputs)
    assert keys == {"self_attn.q_proj"} and layers == 28


def test_the_mlx_scale_is_alpha_over_rank_on_every_layer():
    config = mlx_config({"r": 8, "lora_alpha": 32}, {"self_attn.v_proj", "self_attn.q_proj"}, 28)
    assert config["num_layers"] == 28
    assert config["lora_parameters"] == {"rank": 8, "scale": 4.0, "dropout": 0.0,
                                         "keys": ["self_attn.q_proj", "self_attn.v_proj"]}


def test_the_report_carries_the_kernel_and_its_gpu_minutes(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"gpu": "Tesla T4", "gpu_minutes": 6.2, "steps": 215}))
    report = train_report(tmp_path, "someone/corvane-lora-1")
    assert report["kernel"] == "someone/corvane-lora-1" and report["gpu_minutes"] == 6.2


def test_the_same_data_and_recipe_reuse_the_adapter_and_a_new_label_trains_again(tmp_path, monkeypatch):
    import subprocess

    from corvane import train as trainer

    monkeypatch.chdir(tmp_path)
    splits = tmp_path / "out" / "data"  # where the export writes, relative to the lab folder
    write_splits(splits)
    (tmp_path / "out" / "adapter").mkdir(parents=True)
    (tmp_path / "out" / "adapter" / "adapters.safetensors").write_bytes(b"weights")
    report = {"recipe": trainer.run_settings(Recipe(), splits), "dataset_version": "abc",
              "kernel": "someone/corvane-lora-1", "gpu": "Tesla T4", "gpu_minutes": 2.9}
    (tmp_path / "out" / "train.json").write_text(json.dumps(report))
    # Reusing must not reach Kaggle: any attempt to run the script fails this test.
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pushed")))
    said = []
    assert trainer.train(Recipe(), echo=said.append, force=False) == report
    assert "Reusing it" in said[0] and "2.9 GPU minutes" in said[0]
    write_splits(splits, train_text="train with one review decision\n")
    assert trainer.already_trained(Recipe(), splits) is None
