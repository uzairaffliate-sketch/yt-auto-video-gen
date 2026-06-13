"""
Scene Processor – Splits script into logical scenes and extracts meaningful keywords.
Uses spaCy for part-of-speech tagging and named entity recognition.
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# Lazy load spaCy to avoid heavy import at module level
_nlp = None

def _get_nlp():
    """Load spaCy model on first use (cached)."""
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.error("spaCy model 'en_core_web_sm' not found. Install with: python -m spacy download en_core_web_sm")
            raise
    return _nlp


def split_script(text: str) -> List[str]:
    """
    Break script into a list of scene texts.
    Logic:
    1. If script contains explicit scene markers like 'Scene 1:', 'SCENE:', '[SCENE]' etc.
       use regex to split on those.
    2. Else split by double newlines (blank lines) which usually separate paragraphs/scenes.
    3. Trim whitespace and ignore empty scenes.
    """
    # Try explicit scene markers
    scene_markers = re.split(r'(?i)^\s*(?:Scene\s*\d+|SCENE\s*\d+|\[SCENE\])\s*[:\-]?', text, flags=re.MULTILINE)
    if len(scene_markers) > 1:
        scenes = [s.strip() for s in scene_markers if s.strip()]
        logger.debug(f"Split by scene markers: {len(scenes)} scenes.")
        return scenes

    # Fallback: split by blank lines
    scenes = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
    logger.debug(f"Split by blank lines: {len(scenes)} scenes.")
    if not scenes:
        # Last resort: split by single newline
        scenes = [s.strip() for s in text.split('\n') if s.strip()]
        logger.debug(f"Split by single newline: {len(scenes)} scenes (probably not ideal).")
    return scenes


def extract_keywords(scene_text: str, max_keywords: int = 10) -> List[str]:
    """
    Extract key nouns, proper nouns, adjectives, and named entities from scene text.
    Returns a list of unique keywords, ordered by relevance score.
    Score is based on frequency and entity type priority.
    """
    nlp = _get_nlp()
    doc = nlp(scene_text)

    # Priority: Named entities (PERSON, GPE, ORG, PRODUCT, etc.) > proper nouns > common nouns > adjectives
    keyword_candidates = []

    for ent in doc.ents:
        if ent.label_ in {"PERSON", "GPE", "LOC", "ORG", "PRODUCT", "EVENT", "WORK_OF_ART", "FAC"}:
            keyword_candidates.append({"text": ent.text.strip().lower(), "priority": 10})

    for token in doc:
        if token.is_stop or token.is_punct or token.is_space:
            continue
        if token.pos_ in {"PROPN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 8})
        elif token.pos_ in {"NOUN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 5})
        elif token.pos_ in {"ADJ"}:
            # Include adjectives only if paired with a noun? For now, include standalone (like 'sunset', 'red')
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 3})

    # Group by text, keep highest priority, then sort by priority desc and then by frequency (frequency to break ties)
    keyword_map = {}
    for item in keyword_candidates:
        w = item["text"]
        if w not in keyword_map:
            keyword_map[w] = {"text": w, "priority": item["priority"], "count": 1}
        else:
            keyword_map[w]["priority"] = max(keyword_map[w]["priority"], item["priority"])
            keyword_map[w]["count"] += 1

    # Sort by priority (descending) then by count (descending)
    sorted_keywords = sorted(keyword_map.values(), key=lambda x: (x["priority"], x["count"]), reverse=True)

    # Return just the text, limited to max_keywords
    return [kw["text"] for kw in sorted_keywords[:max_keywords]]