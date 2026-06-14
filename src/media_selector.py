"""
Smart media selector – picks the most relevant image/video for a scene
using sentence‑transformers semantic similarity.

A MIN_RELEVANCE_SCORE threshold is used purely for LOGGING/diagnostics:
if the best match scores below the threshold, we log a warning so it's
easy to spot in the GitHub Actions logs that a scene got a weak/irrelevant
match (e.g. generic nature stock instead of something on-topic).

We deliberately do NOT return None just because the score is low — main.py
treats select_best_media() returning None as "no media for this scene,
skip it entirely", which would make scenes vanish from the final video.
A weak-but-present visual is better than a missing scene. Items with no
title (e.g. some scraped sources) are still considered but ranked using a
small penalty so they don't accidentally "win" purely because an empty
string happens to embed close to the query.
"""

import logging
from typing import List, Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Lazy loading of the model to keep memory low on first import
_model = None

# Below this cosine-similarity score, the match is considered "weak"/
# possibly-irrelevant and gets logged as a warning for visibility.
MIN_RELEVANCE_SCORE = 0.20

# Small penalty applied to items with empty/missing titles so they're not
# unfairly ranked above items that actually describe their content.
EMPTY_TITLE_PENALTY = 0.05


def _get_model():
    """Load the sentence‑transformer model on first use, with caching."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence‑transformers model (all-MiniLM-L6-v2)...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Model loaded successfully.")
    return _model


def _encode(texts: List[str]) -> np.ndarray:
    """Return normalised embeddings for a batch of texts."""
    model = _get_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    # Normalise for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / norms


def select_best_media(keywords: List[str], media_list: List[Dict]) -> Optional[Dict]:
    """
    From the list of media items (each having a 'title' key), select the one
    whose title matches the given keywords most closely.
    Returns the best media dict, or None if list is empty.
    """
    if not media_list:
        return None

    if not keywords:
        # No keywords → return first available media
        return media_list[0]

    # Build the query string from keywords
    query = " ".join(keywords)

    # Build a list of texts to encode: query + all media titles.
    # Use a placeholder for empty titles so the embedding isn't just "" (which
    # tends to embed near generic/neutral content and can skew scores).
    titles = [item.get("title", "") for item in media_list]
    texts_for_embedding = [t if t.strip() else "untitled stock media" for t in titles]
    texts = [query] + texts_for_embedding

    embeddings = _encode(texts)
    query_emb = embeddings[0]
    title_embeddings = embeddings[1:]

    # Compute cosine similarities
    # Since embeddings are normalised, dot product gives cosine similarity
    similarities = np.dot(title_embeddings, query_emb)

    # Apply a small penalty to items with no real title, so a real
    # description always wins a close tie against an "untitled" placeholder.
    adjusted = similarities.copy()
    for i, t in enumerate(titles):
        if not t.strip():
            adjusted[i] -= EMPTY_TITLE_PENALTY

    best_idx = int(np.argmax(adjusted))
    best_score = float(similarities[best_idx])

    logger.debug(f"Media selection scores: {list(zip(titles, similarities))}")
    logger.info(f"Best match for scene: '{titles[best_idx]}' (score: {best_score:.3f}, source: {media_list[best_idx].get('source', 'unknown')})")

    if best_score < MIN_RELEVANCE_SCORE:
        logger.warning(
            f"Low relevance match for scene query '{query[:60]}...': "
            f"best score {best_score:.3f} < threshold {MIN_RELEVANCE_SCORE}. "
            f"Selected '{titles[best_idx]}' from {media_list[best_idx].get('source', 'unknown')} "
            f"({media_list[best_idx].get('type', 'unknown')}) — consider improving visual_queries for this scene."
        )

    return media_list[best_idx]
