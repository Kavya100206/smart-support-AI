"""FAQ similarity search service.

Loads FAQ embeddings from PostgreSQL once at Django startup (called from
TicketsConfig.ready()) and stores them in a module-level variable. All
subsequent requests reuse that in-memory cache — no model, no DB hit,
no per-request embedding computation.

How embeddings get into the DB
-------------------------------
Run ``python manage.py seed_faqs`` on your LOCAL machine (where
sentence-transformers is installed). That command computes 384-dim
all-MiniLM-L6-v2 embeddings and stores them as JSON in FAQEntry.embedding.
The Docker container never needs sentence-transformers or PyTorch.

At runtime the container loads those pre-stored float lists from the DB,
converts them to numpy arrays, and does cosine similarity with a query
vector — but the query vector is also computed by seed_faqs, NOT at
request time.

Wait — how does the query get embedded at request time?
-------------------------------------------------------
It doesn't need to be. The agent asks the LLM which FAQ tool to call and
passes the customer query string as-is to get_faq_answer(). We embed the
query string here using only numpy, BUT we can't do that without a model.

The practical solution used here:
- The LLM reformulates the query into a concise sub-query.
- We do keyword / dot-product similarity against the pre-stored embeddings
  using the ``all-MiniLM-L6-v2`` model IF it is available locally.
- If the model is NOT available (inside Docker), we fall back to a simple
  TF-IDF-style word overlap score using only the stdlib.

This means Docker only needs numpy, not PyTorch. Similarity quality is
slightly lower in Docker but functional.

Public API:
    load_faq_embeddings()  — call once at startup; idempotent.
    get_faq_answer(query)  — returns {\"answer\": str | None, \"score\": float}.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

logger = logging.getLogger(__name__)

# ── Module-level embedding cache ───────────────────────────────────────────────
# Each entry: (FAQEntry instance, unit-norm embedding ndarray | None)
# Populated by load_faq_embeddings(); never mutated afterwards.
_FAQ_CACHE: list[tuple] | None = None

# Cosine similarity threshold (applies to both modes).
_FAQ_MATCH_THRESHOLD = 0.50

# sentence-transformers model — loaded lazily, only if available.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_encoder = None          # SentenceTransformer instance or None
_encoder_available = None  # True / False / None (= not checked yet)


def _get_encoder():
    """Return the SentenceTransformer model if available, else None.

    Checks once and caches the result so the import attempt only happens
    on the first call. Inside Docker this always returns None because
    sentence-transformers is not installed.
    """
    global _encoder, _encoder_available

    if _encoder_available is not None:
        return _encoder  # already checked

    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        logger.info("Loading sentence-transformers model %s …", _MODEL_NAME)
        _encoder = SentenceTransformer(_MODEL_NAME)
        _encoder_available = True
        logger.info("sentence-transformers model loaded (full embedding mode).")
    except ImportError:
        _encoder = None
        _encoder_available = False
        logger.info(
            "sentence-transformers not installed — using word-overlap fallback "
            "for FAQ similarity. Install sentence-transformers locally for full quality."
        )
    return _encoder


# ── Cosine similarity (numpy) ──────────────────────────────────────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two unit-norm vectors."""
    return float(np.dot(a, b))


# ── Word-overlap fallback (stdlib only, no PyTorch) ───────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _tfidf_score(query_tokens: list[str], faq_question: str) -> float:
    """Simple word-overlap score in [0, 1] using Jaccard + token frequency.

    Not as accurate as embedding similarity but requires only stdlib.
    Used inside Docker where sentence-transformers is not installed.
    """
    faq_tokens = _tokenize(faq_question)
    if not query_tokens or not faq_tokens:
        return 0.0

    q_set = set(query_tokens)
    f_set = set(faq_tokens)

    # Jaccard similarity on unique token sets.
    intersection = len(q_set & f_set)
    union = len(q_set | f_set)
    jaccard = intersection / union if union > 0 else 0.0

    # Boost by term frequency — shared tokens that appear more get higher weight.
    q_counts = Counter(query_tokens)
    f_counts = Counter(faq_tokens)
    shared = q_set & f_set
    freq_boost = sum(min(q_counts[t], f_counts[t]) for t in shared)
    freq_norm = freq_boost / (len(query_tokens) + len(faq_tokens))

    return min(jaccard * 0.6 + freq_norm * 0.4, 1.0)


# ── Public API ─────────────────────────────────────────────────────────────────

def load_faq_embeddings() -> None:
    """Load all FAQEntry rows from the DB and cache their embeddings.

    Safe to call multiple times — subsequent calls are no-ops if the cache
    is already populated. Designed to be called from TicketsConfig.ready().

    For entries that have a stored embedding (JSONField list of floats),
    the embedding is loaded and normalised. For entries without an embedding
    (e.g., before seed_faqs has been run), only the question text is cached
    so the word-overlap fallback can still work.

    If no FAQEntry rows exist yet, the cache is set to an empty list and a
    warning is logged.
    """
    global _FAQ_CACHE

    if _FAQ_CACHE is not None:
        return  # Already loaded — nothing to do.

    from tickets.models import FAQEntry  # noqa: PLC0415

    entries = list(FAQEntry.objects.all())

    if not entries:
        logger.warning(
            "FAQ embedding cache: no FAQEntry rows found. "
            "Run `python manage.py seed_faqs` to populate."
        )
        _FAQ_CACHE = []
        return

    cache: list[tuple] = []
    no_embedding_count = 0

    for entry in entries:
        vec = None
        if entry.embedding:
            try:
                arr = np.array(entry.embedding, dtype=np.float32)
                norm = np.linalg.norm(arr)
                vec = arr / norm if norm > 0 else arr
            except Exception:  # noqa: BLE001
                vec = None

        if vec is None:
            no_embedding_count += 1

        # Always cache (question text is used by the word-overlap fallback).
        cache.append((entry, vec))

    _FAQ_CACHE = cache

    if no_embedding_count:
        logger.warning(
            "FAQ cache: %d/%d entries have no stored embedding — word-overlap "
            "fallback will be used for those. Run `python manage.py seed_faqs` "
            "to compute and store embeddings.",
            no_embedding_count,
            len(entries),
        )
    else:
        logger.info("FAQ embedding cache loaded: %d entries (all have embeddings).", len(cache))


def get_faq_answer(query: str) -> dict:
    """Find the most similar FAQ answer for *query*.

    Strategy (in priority order):
    1. If sentence-transformers is available: embed the query, compute cosine
       similarity against stored FAQ embeddings.
    2. If sentence-transformers is NOT available (Docker): use word-overlap
       fallback (Jaccard + token frequency) against FAQ question text. For
       entries WITH stored embeddings, we still use the stored embedding vector
       against a zero-query-vector (score = 0) and fall back to text match.

    Returns:
        {
            \"answer\": str,    # best-matching FAQ answer, or None if no match
            \"score\":  float,  # similarity score (0.0–1.0)
        }

    The function never raises — on any error it returns answer=None, score=0.0.
    """
    if not query or not query.strip():
        return {"answer": None, "score": 0.0}

    if _FAQ_CACHE is None:
        logger.warning("get_faq_answer called before load_faq_embeddings().")
        return {"answer": None, "score": 0.0}

    if len(_FAQ_CACHE) == 0:
        return {"answer": None, "score": 0.0}

    try:
        encoder = _get_encoder()

        best_score = -1.0
        best_entry = None
        query_tokens = _tokenize(query)

        if encoder is not None:
            # ── Full embedding mode ────────────────────────────────────────────
            query_vec = encoder.encode(query, convert_to_numpy=True).astype(np.float32)
            norm = np.linalg.norm(query_vec)
            if norm > 0:
                query_vec = query_vec / norm

            for entry, faq_vec in _FAQ_CACHE:
                if faq_vec is not None:
                    score = _cosine_similarity(query_vec, faq_vec)
                else:
                    # No stored embedding for this entry — fall back to text.
                    score = _tfidf_score(query_tokens, entry.question)

                if score > best_score:
                    best_score = score
                    best_entry = entry
        else:
            # ── Word-overlap fallback (Docker / no PyTorch) ────────────────────
            for entry, _faq_vec in _FAQ_CACHE:
                score = _tfidf_score(query_tokens, entry.question)
                if score > best_score:
                    best_score = score
                    best_entry = entry

        if best_score >= _FAQ_MATCH_THRESHOLD and best_entry is not None:
            return {"answer": best_entry.answer, "score": round(best_score, 4)}

        return {"answer": None, "score": round(best_score, 4)}

    except Exception:
        logger.exception("get_faq_answer: unexpected error during similarity search.")
        return {"answer": None, "score": 0.0}
