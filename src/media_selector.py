"""
Smart media selector – picks the most relevant image/video for a scene
using sentence‑transformers semantic similarity.
"""

import logging
from typing import List, Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Lazy loading of the model to keep memory low on first import
_model = None

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

    # Build a list of texts to encode: query + all media titles
    titles = [item.get("title", "") for item in media_list]
    texts = [query] + titles

    embeddings = _encode(texts)
    query_emb = embeddings[0]
    title_embeddings = embeddings[1:]

    # Compute cosine similarities
    # Since embeddings are normalised, dot product gives cosine similarity
    similarities = np.dot(title_embeddings, query_emb)

    best_idx = int(np.argmax(similarities))
    best_score = float(similarities[best_idx])

    logger.debug(f"Media selection scores: {list(zip(titles, similarities))}")
    logger.info(f"Best match for scene: '{titles[best_idx]}' (score: {best_score:.3f})")

    return media_list[best_idx]