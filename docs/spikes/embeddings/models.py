"""The two models of the multilingual spike (#146), both run through fastembed's TextEmbedding.

fastembed 0.9.1 does not list BAAI/bge-m3 (`TextEmbedding.list_supported_models()`), so it is
added as a custom model from BAAI's own ONNX export (`onnx/` in the BAAI/bge-m3 repo, fp32,
2.27 GB of weights in `model.onnx_data`). The dense vector of bge-m3 is the [CLS] token's
hidden state, normalised (`1_Pooling/config.json` in the repo), which fastembed's CLS pooling
computes. MODEL=small|m3 picks one in the scripts next to this file.
"""

import os

from bleep.memory.embeddings import DEFAULT_LOCAL_MODEL, LOCAL_THREADS, FastEmbedEmbedder

M3 = "BAAI/bge-m3"
NAMES = {"small": DEFAULT_LOCAL_MODEL, "m3": M3}


def register_m3() -> None:
    # Imported here, as FastEmbedEmbedder does, so a script's "before" memory leaves fastembed out.
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    if any(m["model"] == M3 for m in TextEmbedding.list_supported_models()):
        return
    TextEmbedding.add_custom_model(
        model=M3,
        pooling=PoolingType.CLS,
        normalization=True,
        sources=ModelSource(hf=M3),
        dim=1024,
        model_file="onnx/model.onnx",
        additional_files=["onnx/model.onnx_data", "onnx/Constant_7_attr__value"],
        size_in_gb=2.27,
    )


def embedder(key: str | None = None, threads: int | None = None) -> FastEmbedEmbedder:
    """Bleep's local embedder for `small` or `m3` (MODEL in the environment by default), on
    Bleep's two threads unless THREADS says otherwise."""
    key = key or os.environ.get("MODEL", "small")
    if key == "m3":
        register_m3()
    if threads is None:
        threads = int(os.environ["THREADS"]) if os.environ.get("THREADS") else LOCAL_THREADS
    cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
    return FastEmbedEmbedder(NAMES[key], cache_dir=cache_dir, threads=threads)
