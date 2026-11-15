"""Social Listening: constants and pure helpers shared by the social agent, the gateway and the tests.

ADR-0007: a post enters only when a group member captures it with the browser extension; the system drafts a
reply, a person approves it and **posts it themselves**. Nothing here posts, scrapes or contacts anyone.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"   # local, CPU, Hebrew included
EMBED_DIM = 384

CLASSIFY_MODEL = "claude-haiku-5-5"     # every post that passes the prefilter: cheap, fast
DRAFT_MODEL = "claude-opus-5-5"         # only real buying intent: the Hebrew reply a person will post

MAX_POST_CHARS = 5000
MAX_REPLY_CHARS = 1200
INTENTS = ("buy", "sell", "recommend", "other")
LABELS = ("buyer", "not_buyer")
CAPTURE_STATUSES = ("new", "scoring", "ignored", "scored", "lead", "duplicate", "waiting", "failed")
LEAD_STATUSES = ("drafting", "pending", "approved", "posted", "dismissed", "failed")


@dataclass(frozen=True)
class Price:
    """USD per million tokens (Claude API list prices, for the budget; the invoice is the authority)."""
    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = PRICES.get(model)
    if p is None:
        return 0.0
    return round((input_tokens * p.input + output_tokens * p.output + cache_read * p.cache_read
                  + cache_write * p.cache_write) / 1_000_000, 6)


_SPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+")


def normalise(text: str) -> str:
    """For duplicate detection: Unicode NFKC, no URLs, no Hebrew niqqud, collapsed whitespace, lower case."""
    t = unicodedata.normalize("NFKC", text)
    t = _URL.sub(" ", t)
    t = "".join(c for c in t if not ("֑" <= c <= "ׇ"))     # cantillation and vowel points
    return _SPACE.sub(" ", t).strip().lower()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode()).hexdigest()


def clean_post(text: str) -> str:
    """What the models see: control characters removed (keeping newlines), length capped."""
    t = "".join(c for c in text if c == "\n" or c == "\t" or unicodedata.category(c)[0] != "C" or c in "‏‎")
    return t.strip()[:MAX_POST_CHARS]


def prefilter_score(similar: list[tuple[str, float]], k: int = 5) -> float:
    """How much a post looks like the labelled buyer posts: from its nearest labelled examples
    ``[(label, cosine similarity)]``, the best buyer similarity, lowered when the nearest examples are mostly
    non-buyers. 0 when there are no buyer examples."""
    near = sorted(similar, key=lambda x: -x[1])[:k]
    buyers = [s for label, s in near if label == "buyer"]
    if not buyers:
        return 0.0
    best = max(buyers)
    others = [s for label, s in near if label != "buyer"]
    if others and max(others) > best:
        best -= (max(others) - best) / 2          # closer to a known non-buyer than to any buyer
    return round(max(0.0, min(1.0, best)), 4)
