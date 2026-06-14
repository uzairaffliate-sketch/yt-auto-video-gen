"""
Scene Processor – Splits script into logical scenes and extracts meaningful keywords.
Uses spaCy for part-of-speech tagging, named entity recognition, and NOW sentence splitting.
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# Lazy load spaCy to avoid heavy import at module level
_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.error("spaCy model 'en_core_web_sm' not found. Install with: python -m spacy download en_core_web_sm")
            raise
    return _nlp


def split_script(text: str, sentences_per_scene: int = 3) -> List[str]:
    """
    Break script into a list of scene texts.
    Logic (in order):
    1. Explicit scene markers like 'Scene 1:', 'SCENE 1:', '[SCENE]'.
    2. Blank lines (paragraphs).
    3. If still only 1 scene: use spaCy sentence tokenization and group sentences
       into scenes of <sentences_per_scene> each.
    """
    # Step 1 – Explicit markers
    scene_markers = re.split(r'(?i)^\s*(?:Scene\s*\d+|SCENE\s*\d+|\[SCENE\])\s*[:\-]?', text, flags=re.MULTILINE)
    if len(scene_markers) > 1:
        scenes = [s.strip() for s in scene_markers if s.strip()]
        logger.debug(f"Split by scene markers: {len(scenes)} scenes.")
        return scenes

    # Step 2 – Blank lines
    scenes = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
    if len(scenes) > 1:
        logger.debug(f"Split by blank lines: {len(scenes)} scenes.")
        return scenes

    # Step 3 – Fallback: sentence-based splitting
    logger.info("No scene markers or blank lines found – using sentence splitting.")
    nlp = _get_nlp()
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]

    if len(sentences) <= sentences_per_scene:
        # Very few sentences; keep as one scene
        return [text.strip()] if sentences else []

    # Group sentences into scenes
    new_scenes = []
    for i in range(0, len(sentences), sentences_per_scene):
        chunk = " ".join(sentences[i:i + sentences_per_scene])
        new_scenes.append(chunk)

    logger.info(f"Split by sentences into {len(new_scenes)} scenes (sentences_per_scene={sentences_per_scene}).")
    return new_scenes


def extract_keywords(scene_text: str, max_keywords: int = 10) -> List[str]:
    """
    Extract key nouns, proper nouns, adjectives, and named entities from scene text.
    Returns a list of unique keywords, ordered by relevance score.
    """
    nlp = _get_nlp()
    doc = nlp(scene_text)

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
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 3})

    keyword_map = {}
    for item in keyword_candidates:
        w = item["text"]
        if w not in keyword_map:
            keyword_map[w] = {"text": w, "priority": item["priority"], "count": 1}
        else:
            keyword_map[w]["priority"] = max(keyword_map[w]["priority"], item["priority"])
            keyword_map[w]["count"] += 1

    sorted_keywords = sorted(keyword_map.values(), key=lambda x: (x["priority"], x["count"]), reverse=True)
    return [kw["text"] for kw in sorted_keywords[:max_keywords]]
