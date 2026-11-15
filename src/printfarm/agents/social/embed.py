"""Text embeddings for the social agent, computed on this machine (no outside service sees the post for this).

``FastEmbedder``: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 via fastembed (ONNX, CPU; 384
dimensions; Hebrew among its 50+ languages). The model is baked into the image at build time
(``FASTEMBED_CACHE_PATH``), so the agent works offline. ``HashEmbedder``: deterministic word hashing for tests and
for machines without the model (``SOCIAL_EMBEDDER=hash``); it matches shared words only.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from typing import Protocol

from printfarm.contracts.social import EMBED_DIM, EMBED_MODEL


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))       # inputs are unit vectors


def to_pg(v: list[float]) -> str:
    """pgvector text form, e.g. '[0.1,0.2]' (cast with ::vector)."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class HashEmbedder:
    name = "hash"
    _word = re.compile(r"\w+", re.UNICODE)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * EMBED_DIM
            for w in self._word.findall(t.lower()):
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=4).digest(), "big")
                v[h % EMBED_DIM] += 1.0
            out.append(unit(v))
        return out


class FastEmbedder:
    name = EMBED_MODEL

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # imported here: only the social image installs it

        self._model = TextEmbedding(EMBED_MODEL, cache_dir=os.getenv("FASTEMBED_CACHE_PATH"))
        self._lock = asyncio.Lock()           # one ONNX session; calls are short (~10 ms a post)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self._lock:
            vectors = await asyncio.to_thread(lambda: [list(map(float, v)) for v in self._model.embed(texts)])
        return [unit(v) for v in vectors]


def make_embedder() -> Embedder:
    if os.getenv("SOCIAL_EMBEDDER", "local") == "hash":
        return HashEmbedder()
    return FastEmbedder()
