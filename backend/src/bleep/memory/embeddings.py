"""Text to vectors for long-term memory: a local model by default, any OpenAI-compatible endpoint
as the option, and a hash-based fake for tests.

Every embedder returns float32 rows of unit length, so a dot product is the cosine similarity.
Its `name` is stored next to each vector; a different name on boot means the vectors are
re-embedded (`MemoryStore.reembed`). `embed` blocks (CPU work or an HTTP call), so the store runs
it in a worker thread. See docs/decisions/embeddings.md.
"""

import hashlib
import re
import threading
from typing import Any, Protocol

import httpx
import numpy as np
from pydantic import SecretStr

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
# onnxruntime takes every core by default and spins on them: about 100 ms of CPU for a 6 ms embed
# on a 14-core laptop. Two threads cost 22 ms of CPU for 11 ms (docs/decisions/embeddings.md).
LOCAL_THREADS = 2
HTTP_TIMEOUT_S = 30.0
_WORD = re.compile(r"[a-z0-9]+")


class EmbedderError(Exception):
    """The embedder could not turn the texts into vectors."""


class Embedder(Protocol):
    @property
    def name(self) -> str:
        """Which model the vectors come from; a change re-embeds the stored ones."""
        ...

    def embed(self, texts: list[str]) -> np.ndarray:
        """One L2-normalised float32 row per text."""
        ...


def normalise(vectors: np.ndarray) -> np.ndarray:
    """Rows scaled to unit length; an all-zero row stays zero."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


class FastEmbedEmbedder:
    """A `fastembed` ONNX model on the CPU, in-process. The model is loaded on the first `embed`,
    so building one (and the app) neither downloads nor loads anything."""

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        cache_dir: str | None = None,
        threads: int | None = LOCAL_THREADS,
    ) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.threads = threads
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"fastembed:{self.model}"

    def _load(self) -> Any:
        from fastembed import TextEmbedding  # slow to import; only when memory is first used

        return TextEmbedding(self.model, cache_dir=self.cache_dir, threads=self.threads)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        # One embed at a time: it takes about 11 ms, and the model and its tokenizer are then
        # never used from two worker threads at once.
        with self._lock:
            if self._model is None:
                self._model = self._load()
            return normalise(np.stack(list(self._model.embed(texts))))


class OpenAICompatEmbedder:
    """`POST {base_url}/embeddings`, as OpenAI, LM Studio, Ollama and others serve it."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: SecretStr | str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = SecretStr(api_key) if isinstance(api_key, str) else api_key
        self._transport = transport

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        headers = {}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        try:
            with httpx.Client(transport=self._transport, timeout=HTTP_TIMEOUT_S) as client:
                reply = client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": texts},
                    headers=headers,
                )
                reply.raise_for_status()
                rows = sorted(reply.json()["data"], key=lambda row: row["index"])
                vectors = np.array([row["embedding"] for row in rows], dtype=np.float32)
        except httpx.HTTPStatusError as error:  # the reply text can echo the request: not kept
            raise EmbedderError(
                f"embeddings endpoint answered {error.response.status_code}"
            ) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise EmbedderError(f"embeddings endpoint failed: {type(error).__name__}") from None
        if vectors.shape[0] != len(texts):
            raise EmbedderError(f"asked for {len(texts)} vectors, got {vectors.shape[0]}")
        return normalise(vectors)


class FakeEmbedder:
    """Bag of words hashed into `dims` buckets: texts that share words score higher. For tests;
    it records every batch it was asked for in `calls`."""

    def __init__(self, dims: int = 256, name: str = "fake") -> None:
        self.dims = dims
        self._name = name
        self.calls: list[list[str]] = []

    @property
    def name(self) -> str:
        return self._name

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        vectors = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in _WORD.findall(text.lower()):
                digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dims
                vectors[row, bucket] += 1.0 if digest[4] & 1 else -1.0
        return normalise(vectors)
