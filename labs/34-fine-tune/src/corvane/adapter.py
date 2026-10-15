"""Turn a PEFT LoRA adapter trained on a GPU into one MLX loads on this laptop.

Both libraries store the same two matrices per layer, A and B, but in different shapes and under
different names. PEFT keeps A as (rank, inputs) and B as (outputs, rank); MLX multiplies on the other
side, so it wants both transposed. The update is scaled by alpha over rank in both, written differently.
"""

import json
import re
from pathlib import Path

PEFT_NAME = re.compile(r"^base_model\.model\.(model\.layers\.(\d+)\.(.+))\.lora_([AB])\.weight$")


def mlx_weights(peft_weights: dict) -> tuple[dict, set[str], int]:
    """Rename and transpose each matrix. Returns the weights, the layer keys LoRA wraps and the layer count."""
    converted, keys, layers = {}, set(), 0
    for name, weight in peft_weights.items():
        match = PEFT_NAME.match(name)
        if not match:
            raise ValueError(f"{name} is not a LoRA weight on a decoder layer")
        module, layer, key, side = match.groups()
        converted[f"{module}.lora_{side.lower()}"] = weight.T
        keys.add(key)
        layers = max(layers, int(layer) + 1)
    return converted, keys, layers


def mlx_config(peft_config: dict, keys: set[str], layers: int) -> dict:
    """The adapter_config.json `mlx_lm.load` reads. `num_layers` counts from the top, so all of them."""
    return {"fine_tune_type": "lora", "num_layers": layers,
            "lora_parameters": {"rank": peft_config["r"], "scale": peft_config["lora_alpha"] / peft_config["r"],
                                "dropout": 0.0, "keys": sorted(keys)}}


def peft_to_mlx(peft_dir: Path, mlx_dir: Path) -> Path:
    import mlx.core as mx

    peft_config = json.loads((peft_dir / "adapter_config.json").read_text())
    # PEFT saves the matrices in fp32. MLX keeps them so and casts the update to the model's dtype.
    weights, keys, layers = mlx_weights(mx.load(str(peft_dir / "adapter_model.safetensors")))
    mlx_dir.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(mlx_dir / "adapters.safetensors"), weights)
    (mlx_dir / "adapter_config.json").write_text(json.dumps(mlx_config(peft_config, keys, layers), indent=1))
    return mlx_dir
